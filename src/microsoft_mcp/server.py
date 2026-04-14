import os
import sys
from .tools import mcp


def main() -> None:
    if not os.getenv("MICROSOFT_MCP_CLIENT_ID"):
        print(
            "Error: MICROSOFT_MCP_CLIENT_ID environment variable is required",
            file=sys.stderr,
        )
        sys.exit(1)

    mcp.run()


def _configure_auth() -> None:
    """Configure AzureProvider auth on the shared mcp instance for HTTP mode."""
    from fastmcp.server.auth.providers.azure import AzureProvider

    entra_client_id = os.environ["ENTRA_CLIENT_ID"]
    entra_client_secret = os.environ["ENTRA_CLIENT_SECRET"]
    entra_tenant_id = os.environ["ENTRA_TENANT_ID"]
    public_base_url = os.environ["PUBLIC_BASE_URL"]

    auth = AzureProvider(
        client_id=entra_client_id,
        client_secret=entra_client_secret,
        tenant_id=entra_tenant_id,
        base_url=public_base_url,
        required_scopes=["access_as_user"],
        additional_authorize_scopes=[
            "https://graph.microsoft.com/offline_access",
            "https://graph.microsoft.com/User.Read",
            "https://graph.microsoft.com/Mail.ReadWrite",
            "https://graph.microsoft.com/Mail.Send",
            "https://graph.microsoft.com/Calendars.ReadWrite",
            "https://graph.microsoft.com/Files.ReadWrite",
            "https://graph.microsoft.com/Contacts.Read",
            "https://graph.microsoft.com/People.Read",
        ],
        jwt_signing_key=os.getenv("JWT_SIGNING_KEY"),
    )

    mcp.auth = auth


def _configure_obo_middleware() -> None:
    """Add middleware that performs OBO token exchange before each tool call.

    Intercepts every tool call, exchanges the FastMCP-issued JWT for a
    Microsoft Graph access token via Entra OBO flow, and stores it in
    the ContextVar so graph.py can use it transparently.
    """
    from fastmcp.server.auth.providers.azure import AzureProvider
    from fastmcp.server.middleware import Middleware, MiddlewareContext
    from fastmcp.server.dependencies import get_access_token
    from .auth_context import current_graph_token

    GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]

    class GraphOBOMiddleware(Middleware):
        async def on_call_tool(self, context: MiddlewareContext, call_next):
            access_token = get_access_token()
            if access_token is not None and isinstance(mcp.auth, AzureProvider):
                credential = await mcp.auth.get_obo_credential(
                    user_assertion=access_token.token,
                )
                result = await credential.get_token(*GRAPH_SCOPES)
                token_reset = current_graph_token.set(result.token)
                try:
                    return await call_next(context)
                finally:
                    current_graph_token.reset(token_reset)
            return await call_next(context)

    mcp.add_middleware(GraphOBOMiddleware())


def main_http() -> None:
    """Run the MCP server over streamable HTTP (for remote deployment)."""
    missing = [v for v in ("ENTRA_CLIENT_ID", "ENTRA_CLIENT_SECRET", "ENTRA_TENANT_ID", "PUBLIC_BASE_URL") if not os.getenv(v)]
    if missing:
        print(f"Error: missing environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    _configure_auth()
    _configure_obo_middleware()

    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.responses import PlainTextResponse

    mcp_app = mcp.http_app(path="/mcp")

    async def health(_request):
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=mcp_app.lifespan,
    )

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
