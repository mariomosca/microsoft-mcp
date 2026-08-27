"""Refresh-token rotation grace period on top of FastMCP's AzureProvider.

FastMCP's OAuthProxy enforces strict one-time-use refresh tokens: the old
token is invalidated the moment a rotation succeeds. If the client never
receives the rotated token (dropped response, container killed mid-flight,
two sessions sharing one connector), its next refresh fails with
invalid_grant and the connector shows up as "connected but not authorized"
until the user manually re-links it (observed on the Brandart gateway,
25-26 Aug 2026).

GraceAzureProvider keeps the *previous* refresh token usable for a short
grace window after each rotation (industry-standard "reuse interval", cf.
Auth0/Okta). A retry with the old token inside the window performs a fresh
rotation and gets a new valid pair. Reuse is always logged explicitly:

- inside the window:  "Refresh token reuse detected within grace window"
- after the window:   "Refresh token reuse detected: token was rotated ...s ago"

No extra plaintext tokens are persisted: the grace re-arms the same
hash-keyed metadata + JTI mapping entries the proxy already stores, plus a
small marker record used only for reuse detection/logging.
"""

from __future__ import annotations

import time

from key_value.aio.adapters.pydantic import PydanticAdapter
from mcp.server.auth.provider import RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import BaseModel

from fastmcp.server.auth.providers.azure import AzureProvider
from fastmcp.server.auth.oauth_proxy.models import (
    RefreshTokenMetadata,
    _hash_token,
)
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# How long a rotated-out refresh token stays usable (seconds).
DEFAULT_ROTATION_GRACE_SECONDS = 60

# How long we remember that a token was rotated, for reuse-detection logging
# even after the grace window has closed (seconds).
ROTATION_MARKER_TTL_SECONDS = 24 * 60 * 60


class RotationGraceMarker(BaseModel):
    """Records that a refresh token was rotated out, keyed by token hash."""

    client_id: str
    rotated_at: float


class GraceAzureProvider(AzureProvider):
    def __init__(
        self,
        *args,
        rotation_grace_seconds: int = DEFAULT_ROTATION_GRACE_SECONDS,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._rotation_grace_seconds = rotation_grace_seconds
        self._rotation_marker_store: PydanticAdapter[RotationGraceMarker] = (
            PydanticAdapter[RotationGraceMarker](
                key_value=self._client_storage,
                pydantic_model=RotationGraceMarker,
                default_collection="mcp-rotation-grace",
                raise_on_validation_error=False,
            )
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        result = await super().load_refresh_token(client, refresh_token)
        if result is None:
            # Metadata gone: either an unknown token or one rotated out past
            # its grace window. Surface reuse explicitly for observability.
            marker = await self._rotation_marker_store.get(
                key=_hash_token(refresh_token)
            )
            if marker is not None:
                logger.warning(
                    "Refresh token reuse detected: token was rotated %.0fs ago "
                    "(client=%s, grace=%ds) - rejecting with invalid_grant",
                    time.time() - marker.rotated_at,
                    client.client_id,
                    self._rotation_grace_seconds,
                )
        return result

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        token_hash = _hash_token(refresh_token.token)
        marker = await self._rotation_marker_store.get(key=token_hash)
        if marker is not None:
            logger.warning(
                "Refresh token reuse detected within grace window "
                "(rotated %.0fs ago, client=%s) - honoring retry with a fresh rotation",
                time.time() - marker.rotated_at,
                client.client_id,
            )

        # Capture the JTI mapping before the base rotation deletes it, so we
        # can re-arm it for the grace window afterwards.
        old_jti: str | None = None
        old_mapping = None
        try:
            payload = self.jwt_issuer.verify_token(
                refresh_token.token, expected_token_use="refresh"
            )
            old_jti = payload.get("jti")
            if old_jti:
                old_mapping = await self._jti_mapping_store.get(key=old_jti)
        except Exception:
            pass  # base implementation will raise the proper TokenError

        try:
            result = await super().exchange_refresh_token(
                client, refresh_token, scopes
            )
        except TokenError:
            if old_jti is not None and old_mapping is None:
                logger.warning(
                    "Refresh token reuse detected: JTI mapping already consumed "
                    "(client=%s) - rejecting with invalid_grant",
                    client.client_id,
                )
            raise

        # Re-arm the old token for the grace window - but never extend the
        # chain: a token that was itself a graced retry stays consumed.
        grace = self._rotation_grace_seconds
        if grace > 0 and marker is None and old_jti and old_mapping and client.client_id:
            now = time.time()
            await self._jti_mapping_store.put(
                key=old_jti, value=old_mapping, ttl=grace
            )
            await self._refresh_token_store.put(
                key=token_hash,
                value=RefreshTokenMetadata(
                    client_id=client.client_id,
                    scopes=refresh_token.scopes,
                    expires_at=int(now) + grace,
                    created_at=now,
                ),
                ttl=grace,
            )
            await self._rotation_marker_store.put(
                key=token_hash,
                value=RotationGraceMarker(
                    client_id=client.client_id, rotated_at=now
                ),
                ttl=ROTATION_MARKER_TTL_SECONDS,
            )
            logger.debug(
                "Rotation grace armed for previous refresh token (%ds, client=%s)",
                grace,
                client.client_id,
            )

        return result
