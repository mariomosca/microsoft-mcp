"""Unit tests for the refresh-token rotation grace window (oauth_grace.py).

No network: the upstream (Entra) refresh is stubbed with a fake base
``exchange_refresh_token`` that mimics OAuthProxy's storage behavior
(one-time-use: consume the JTI mapping + refresh metadata, issue new tokens).
"""

import logging
import time

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from fastmcp.server.auth.oauth_proxy.models import (
    JTIMapping,
    RefreshTokenMetadata,
    _hash_token,
)
from fastmcp.server.auth.oauth_proxy.proxy import OAuthProxy

from microsoft_mcp.oauth_grace import GraceAzureProvider

CLIENT_ID = "test-client"
SCOPES = ["access_as_user"]


def _make_provider(grace: int) -> GraceAzureProvider:
    p = GraceAzureProvider(
        rotation_grace_seconds=grace,
        client_id="11111111-1111-1111-1111-111111111111",
        client_secret="dummy-secret",
        tenant_id="22222222-2222-2222-2222-222222222222",
        base_url="https://gateway.example.com",
        required_scopes=SCOPES,
        client_storage=MemoryStore(),
        jwt_signing_key="unit-test-signing-key-material",
        require_authorization_consent=False,
    )
    p.set_mcp_path("/mcp")  # initializes the JWT issuer
    return p


@pytest.fixture
def provider() -> GraceAzureProvider:
    return _make_provider(60)


@pytest.fixture
def grace_log(caplog):
    """Capture oauth_grace warnings even if the fastmcp logger doesn't propagate."""
    logger = logging.getLogger("fastmcp.microsoft_mcp.oauth_grace")
    logger.addHandler(caplog.handler)
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    yield caplog
    logger.setLevel(old_level)
    logger.removeHandler(caplog.handler)


def _client() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=CLIENT_ID,
        redirect_uris=["https://client.example.com/callback"],
    )


async def _seed_refresh_token(provider: GraceAzureProvider, jti: str) -> str:
    """Create a valid FastMCP refresh JWT with its storage entries."""
    token = provider.jwt_issuer.issue_refresh_token(
        client_id=CLIENT_ID, scopes=SCOPES, jti=jti, expires_in=3600
    )
    await provider._jti_mapping_store.put(
        key=jti,
        value=JTIMapping(jti=jti, upstream_token_id="upstream-1", created_at=time.time()),
        ttl=3600,
    )
    await provider._refresh_token_store.put(
        key=_hash_token(token),
        value=RefreshTokenMetadata(
            client_id=CLIENT_ID,
            scopes=SCOPES,
            expires_at=int(time.time()) + 3600,
            created_at=time.time(),
        ),
        ttl=3600,
    )
    return token


def _fake_base_exchange(counter: dict):
    """Mimic OAuthProxy.exchange_refresh_token storage side effects."""

    async def fake(self, client, refresh_token, scopes):
        payload = self.jwt_issuer.verify_token(
            refresh_token.token, expected_token_use="refresh"
        )
        jti = payload["jti"]
        mapping = await self._jti_mapping_store.get(key=jti)
        if not mapping:
            raise TokenError("invalid_grant", "Refresh token mapping not found")
        counter["rotations"] += 1
        new_jti = f"jti-new-{counter['rotations']}"
        new_refresh = self.jwt_issuer.issue_refresh_token(
            client_id=client.client_id, scopes=scopes, jti=new_jti, expires_in=3600
        )
        await self._jti_mapping_store.put(
            key=new_jti,
            value=JTIMapping(
                jti=new_jti,
                upstream_token_id=mapping.upstream_token_id,
                created_at=time.time(),
            ),
            ttl=3600,
        )
        await self._refresh_token_store.put(
            key=_hash_token(new_refresh),
            value=RefreshTokenMetadata(
                client_id=client.client_id,
                scopes=scopes,
                expires_at=int(time.time()) + 3600,
                created_at=time.time(),
            ),
            ttl=3600,
        )
        # one-time use: consume the old token
        await self._jti_mapping_store.delete(key=jti)
        await self._refresh_token_store.delete(key=_hash_token(refresh_token.token))
        return OAuthToken(
            access_token="new-access",
            token_type="Bearer",
            expires_in=3600,
            refresh_token=new_refresh,
            scope=" ".join(scopes),
        )

    return fake


@pytest.mark.anyio
async def test_rotation_arms_grace_window(provider, monkeypatch):
    counter = {"rotations": 0}
    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", _fake_base_exchange(counter))

    token = await _seed_refresh_token(provider, "jti-old")
    loaded = await provider.load_refresh_token(_client(), token)
    assert loaded is not None

    await provider.exchange_refresh_token(_client(), loaded, SCOPES)
    assert counter["rotations"] == 1

    # Old token must still be loadable (grace re-armed the metadata)...
    still_loaded = await provider.load_refresh_token(_client(), token)
    assert still_loaded is not None
    # ...and the rotation marker must exist.
    marker = await provider._rotation_marker_store.get(key=_hash_token(token))
    assert marker is not None
    assert marker.client_id == CLIENT_ID


@pytest.mark.anyio
async def test_reuse_within_grace_gets_fresh_tokens(provider, monkeypatch, grace_log):
    counter = {"rotations": 0}
    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", _fake_base_exchange(counter))

    token = await _seed_refresh_token(provider, "jti-old")
    loaded = await provider.load_refresh_token(_client(), token)
    first = await provider.exchange_refresh_token(_client(), loaded, SCOPES)

    # Retry with the OLD token (client never saved the rotated one).
    retry_loaded = await provider.load_refresh_token(_client(), token)
    assert retry_loaded is not None
    with grace_log.at_level("WARNING"):
        second = await provider.exchange_refresh_token(_client(), retry_loaded, SCOPES)

    assert counter["rotations"] == 2
    assert second.refresh_token != first.refresh_token
    assert any("reuse detected within grace window" in r.message for r in grace_log.records)


@pytest.mark.anyio
async def test_graced_reuse_does_not_extend_chain(provider, monkeypatch, grace_log):
    counter = {"rotations": 0}
    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", _fake_base_exchange(counter))

    token = await _seed_refresh_token(provider, "jti-old")
    loaded = await provider.load_refresh_token(_client(), token)
    await provider.exchange_refresh_token(_client(), loaded, SCOPES)

    # First reuse: honored.
    retry_loaded = await provider.load_refresh_token(_client(), token)
    await provider.exchange_refresh_token(_client(), retry_loaded, SCOPES)

    # Second reuse: the graced retry consumed the entries and must NOT have
    # re-armed them - load fails, with an explicit reuse-detected warning.
    with grace_log.at_level("WARNING"):
        third = await provider.load_refresh_token(_client(), token)
    assert third is None
    assert any("Refresh token reuse detected" in r.message for r in grace_log.records)
    assert counter["rotations"] == 2


@pytest.mark.anyio
async def test_zero_grace_disables_window(monkeypatch):
    provider = _make_provider(0)
    counter = {"rotations": 0}
    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", _fake_base_exchange(counter))

    token = await _seed_refresh_token(provider, "jti-old")
    loaded = await provider.load_refresh_token(_client(), token)
    await provider.exchange_refresh_token(_client(), loaded, SCOPES)

    # Strict one-time use: no grace entries re-armed.
    assert await provider.load_refresh_token(_client(), token) is None
