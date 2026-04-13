import os
import tempfile
import threading
import webbrowser
import msal
import pathlib as pl
from typing import NamedTuple
from dotenv import load_dotenv

load_dotenv()

CACHE_FILE = pl.Path.home() / ".microsoft_mcp_token_cache.json"
SCOPES = ["https://graph.microsoft.com/.default"]


class Account(NamedTuple):
    username: str
    account_id: str


class AuthPendingError(Exception):
    """Raised when device-code auth has been triggered in a background thread.

    The user must complete sign-in in the browser that was opened automatically.
    The MCP client should surface this message and the user should retry the
    command a few seconds later, after which the cached token will be used.
    """


# Module-level state for coordinating browser-based device flows across
# concurrent tool calls (several Graph requests may arrive at once).
_auth_lock = threading.Lock()
_auth_in_progress = threading.Event()


def _read_cache() -> str | None:
    try:
        return CACHE_FILE.read_text()
    except FileNotFoundError:
        return None


def _write_cache(content: str) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(content)


def get_app() -> msal.PublicClientApplication:
    client_id = os.getenv("MICROSOFT_MCP_CLIENT_ID")
    if not client_id:
        raise ValueError("MICROSOFT_MCP_CLIENT_ID environment variable is required")

    tenant_id = os.getenv("MICROSOFT_MCP_TENANT_ID", "common")
    authority = f"https://login.microsoftonline.com/{tenant_id}"

    cache = msal.SerializableTokenCache()
    cache_content = _read_cache()
    if cache_content:
        cache.deserialize(cache_content)

    app = msal.PublicClientApplication(
        client_id, authority=authority, token_cache=cache
    )

    return app


def _create_helper_html(verification_uri: str, user_code: str) -> str:
    """Render a small local HTML helper shown to the user in the browser.

    Microsoft's device-code endpoint does not accept the user code as a
    query parameter, so we display it prominently with copy-to-clipboard
    and a button that opens the Microsoft sign-in page in a new tab.
    """
    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Microsoft MCP - Autenticazione</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #1e1e2e; color: #e0e0e0; margin: 0; padding: 40px;
         display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
  .card {{ background: #2a2a3e; padding: 40px; border-radius: 12px; max-width: 520px; width: 100%;
          box-shadow: 0 10px 40px rgba(0,0,0,0.4); }}
  h1 {{ margin: 0 0 8px; font-size: 22px; color: #fff; }}
  .sub {{ color: #a0a0b0; margin: 0 0 24px; font-size: 14px; }}
  .code {{ font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 44px; font-weight: 700;
          background: #3a3a52; padding: 20px 30px; border-radius: 8px; text-align: center;
          letter-spacing: 6px; color: #89ddff; margin: 0 0 16px; user-select: all; cursor: pointer;
          transition: background 0.2s; }}
  .code:hover {{ background: #4a4a62; }}
  .code.copied {{ background: #4caf50; color: #fff; }}
  .steps {{ padding-left: 22px; margin: 20px 0; }}
  .steps li {{ margin: 10px 0; line-height: 1.5; color: #c0c0d0; }}
  .btn {{ display: block; width: 100%; background: #0078d4; color: #fff; border: 0;
         padding: 14px 24px; font-size: 16px; border-radius: 8px; cursor: pointer;
         margin-top: 8px; text-decoration: none; text-align: center; font-weight: 600; }}
  .btn:hover {{ background: #106ebe; }}
  .btn.secondary {{ background: #3a3a52; color: #e0e0e0; }}
  .btn.secondary:hover {{ background: #4a4a62; }}
  .note {{ color: #808090; font-size: 12px; margin-top: 24px; text-align: center; }}
</style>
</head>
<body>
<div class="card">
  <h1>Microsoft MCP - Autenticazione</h1>
  <p class="sub">Completa i passaggi sotto per autorizzare Claude Desktop ad accedere al tuo account Microsoft 365.</p>

  <div class="code" id="code" onclick="copyCode()" title="Clicca per copiare">{user_code}</div>

  <ol class="steps">
    <li>Clicca sul codice sopra per copiarlo</li>
    <li>Clicca <strong>Apri Microsoft Login</strong></li>
    <li>Incolla il codice, accedi, autorizza l'app</li>
    <li>Torna a Claude Desktop e riscrivi lo stesso comando</li>
  </ol>

  <a class="btn" href="{verification_uri}" target="_blank" rel="noopener">Apri Microsoft Login</a>
  <button class="btn secondary" onclick="copyCode()">Copia Codice</button>

  <p class="note">Puoi chiudere questa scheda dopo aver autorizzato.</p>
</div>
<script>
function copyCode() {{
  navigator.clipboard.writeText("{user_code}").catch(function() {{}});
  var el = document.getElementById("code");
  el.classList.add("copied");
  setTimeout(function() {{ el.classList.remove("copied"); }}, 400);
}}
</script>
</body>
</html>"""


def _background_complete_flow(
    app: msal.PublicClientApplication, flow: dict
) -> None:
    """Run the blocking MSAL device-flow completion in a background thread.

    When the user finishes sign-in in the browser, MSAL returns a token and we
    persist the cache so subsequent tool calls succeed silently.
    """
    try:
        result = app.acquire_token_by_device_flow(flow)
        if "error" not in result:
            cache = app.token_cache
            if isinstance(cache, msal.SerializableTokenCache) and cache.has_state_changed:
                _write_cache(cache.serialize())
    except Exception:
        # Background failures are intentionally swallowed; the next tool call
        # will simply re-trigger the flow if the token still is not present.
        pass
    finally:
        _auth_in_progress.clear()


def _trigger_browser_auth(app: msal.PublicClientApplication) -> None:
    """Open the browser with a helper page and start auth in the background.

    Always raises AuthPendingError so the current tool call returns a clear
    message to the user. The next call (after sign-in) will find the cached
    token and succeed silently.
    """
    # Fast path: an auth flow is already running (another tool call triggered it).
    if _auth_in_progress.is_set():
        raise AuthPendingError(
            "Autenticazione Microsoft in corso. Completa il login nel browser "
            "gia' aperto, poi riprova il comando."
        )

    with _auth_lock:
        if _auth_in_progress.is_set():
            raise AuthPendingError(
                "Autenticazione Microsoft in corso. Completa il login nel browser "
                "gia' aperto, poi riprova il comando."
            )

        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise Exception(
                f"Failed to get device code: {flow.get('error_description', 'Unknown error')}"
            )

        verification_uri = flow.get(
            "verification_uri",
            flow.get("verification_url", "https://microsoft.com/devicelogin"),
        )
        user_code = flow["user_code"]

        # Write the helper HTML to a temp file and open it in the default browser.
        html = _create_helper_html(verification_uri, user_code)
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        )
        tmp.write(html)
        tmp.close()

        try:
            webbrowser.open(f"file://{tmp.name}")
        except Exception as e:
            # If we cannot open a browser we still want to surface URL + code.
            print(
                f"Impossibile aprire il browser automaticamente: {e}\n"
                f"URL: {verification_uri}\nCodice: {user_code}"
            )

        # Log to stderr for CLI debugging / fallback.
        print(
            f"\n[microsoft-mcp] Auth richiesta:\n"
            f"  URL:    {verification_uri}\n"
            f"  Codice: {user_code}\n"
        )

        _auth_in_progress.set()
        thread = threading.Thread(
            target=_background_complete_flow,
            args=(app, flow),
            daemon=True,
        )
        thread.start()

        raise AuthPendingError(
            f"Autenticazione Microsoft richiesta. Si e' appena aperta una pagina "
            f"nel browser con il codice [{user_code}]. Completa il login e riprova "
            f"lo stesso comando tra qualche secondo."
        )


def get_token(account_id: str | None = None) -> str:
    app = get_app()

    accounts = app.get_accounts()
    account = None

    if account_id:
        account = next(
            (a for a in accounts if a["home_account_id"] == account_id), None
        )
    elif accounts:
        account = accounts[0]

    result = app.acquire_token_silent(SCOPES, account=account)

    if not result:
        # Two modes of interactive auth:
        # - CLI_AUTH=1 (used by `microsoft-mcp-auth` / authenticate.py): the
        #   traditional blocking device flow printed to stdout. Good when the
        #   caller IS a human terminal.
        # - default (Claude Desktop / Claude Code stdio MCP): open a browser
        #   helper page and complete auth in a background thread, so the
        #   current tool call returns immediately with a clear message.
        if os.getenv("MICROSOFT_MCP_CLI_AUTH") == "1":
            flow = app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise Exception(
                    f"Failed to get device code: {flow.get('error_description', 'Unknown error')}"
                )
            verification_uri = flow.get(
                "verification_uri",
                flow.get("verification_url", "https://microsoft.com/devicelogin"),
            )
            print(
                f"\nTo authenticate:\n1. Visit {verification_uri}\n2. Enter code: {flow['user_code']}"
            )
            result = app.acquire_token_by_device_flow(flow)
        else:
            _trigger_browser_auth(app)  # always raises AuthPendingError
            raise RuntimeError("unreachable")  # for type checkers

    if "error" in result:
        raise Exception(
            f"Auth failed: {result.get('error_description', result['error'])}"
        )

    cache = app.token_cache
    if isinstance(cache, msal.SerializableTokenCache) and cache.has_state_changed:
        _write_cache(cache.serialize())

    return result["access_token"]


def list_accounts() -> list[Account]:
    app = get_app()
    return [
        Account(username=a["username"], account_id=a["home_account_id"])
        for a in app.get_accounts()
    ]


def authenticate_new_account() -> Account | None:
    """Authenticate a new account interactively (CLI-style, blocking).

    Used by the `microsoft-mcp-auth` CLI entry point. Prints URL + code to
    stdout and blocks until the user completes sign-in.
    """
    app = get_app()

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise Exception(
            f"Failed to get device code: {flow.get('error_description', 'Unknown error')}"
        )

    print("\nTo authenticate:")
    print(
        f"1. Visit: {flow.get('verification_uri', flow.get('verification_url', 'https://microsoft.com/devicelogin'))}"
    )
    print(f"2. Enter code: {flow['user_code']}")
    print("3. Sign in with your Microsoft account")
    print("\nWaiting for authentication...")

    result = app.acquire_token_by_device_flow(flow)

    if "error" in result:
        raise Exception(
            f"Auth failed: {result.get('error_description', result['error'])}"
        )

    cache = app.token_cache
    if isinstance(cache, msal.SerializableTokenCache) and cache.has_state_changed:
        _write_cache(cache.serialize())

    # Get the newly added account
    accounts = app.get_accounts()
    if accounts:
        # Find the account that matches the token we just got
        for account in accounts:
            if (
                account.get("username", "").lower()
                == result.get("id_token_claims", {})
                .get("preferred_username", "")
                .lower()
            ):
                return Account(
                    username=account["username"], account_id=account["home_account_id"]
                )
        # If exact match not found, return the last account
        account = accounts[-1]
        return Account(
            username=account["username"], account_id=account["home_account_id"]
        )

    return None
