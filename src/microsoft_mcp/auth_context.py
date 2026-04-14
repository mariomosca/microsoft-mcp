from __future__ import annotations
from contextvars import ContextVar

# Populated by middleware in HTTP mode (Fase 3).
# In stdio mode stays None → graph.request() falls back to MSAL get_token(account_id).
current_graph_token: ContextVar[str | None] = ContextVar(
    "current_graph_token", default=None
)
