# PLAN — Azure Remote MCP Gateway per BrandArt

**Progetto**: `microsoft-mcp` (fork `github.com/mariomosca/microsoft-mcp`)
**Obiettivo**: migrare da stdio locale a **Remote MCP server** deployato su Azure Container Apps con OAuth 2.1 + Entra ID, accessibile da Claude Desktop come Custom Connector per tutto il management BrandArt.
**Data creazione**: 2026-04-13
**Target delivery**: entro 2026-04-22 (recap progetti AI BrandArt)
**Owner**: Mario (CTO), esecuzione Alita/Kai

---

## 1. Contesto e problema attuale

Il server MCP oggi gira come **processo stdio locale** su ogni macchina utente via `uvx --from <tarball>`. Problemi già incontrati:

1. **Windows + Python 3.14 RC**: uv scarica versione instabile → `exit code 1` (vedi log Rocco 13 Apr)
2. **VC++ Redistributable mancante**: su Windows clean → python.exe non parte
3. **Antivirus aziendale**: blocca binari unsigned in `%APPDATA%\uv\`
4. **Device code flow**: Claude Desktop non espone stdout → auth invisibile
5. **Distribuzione**: ogni utente richiede setup manuale (uv + git/tarball + config JSON + auth CLI)
6. **Update**: modifiche al fork richiedono `--refresh` lato ogni utente, nessun rollback centralizzato

Un server **remoto con transport HTTP + OAuth 2.1** elimina tutti e 6 questi problemi.

---

## 2. Obiettivo e criteri di successo

### Obiettivo
Deployare `microsoft-mcp` come servizio HTTP OAuth-native su Azure, raggiungibile da qualunque Claude Desktop (Mac/Windows) tramite "Aggiungi connettore personalizzato" con URL unico e login SSO BrandArt.

### Success criteria (deliverable 22 Apr)
- [ ] Endpoint pubblico HTTPS `https://brandart-mcp.azurecontainerapps.io/mcp` (o custom domain)
- [ ] Mario completa l'onboarding Custom Connector Claude Desktop in < 60 secondi senza terminale
- [ ] Rocco (Windows clean) completa l'onboarding in < 60 secondi senza terminale, senza `uv`, senza VC++
- [ ] Multi-utente: ogni utente ha cache token isolata, accesso solo al proprio account Microsoft 365
- [ ] Tutti i 35 tool esistenti (mail/calendar/files/contacts/search) funzionanti via HTTP
- [ ] Osservabilità: log richieste + errori su Application Insights
- [ ] Costo runtime mensile < 20€

### Non-goals (fuori scope per 22 Apr)
- Custom domain `mcp.brandart.it` (nice-to-have, 10€/anno + DNS config — farlo in W17)
- High-availability multi-region
- Rate limiting / quota per utente (farlo in W17 se utenti > 10)
- UI amministrativa web (dashboard Azure basta)

---

## 3. Architettura target

```
┌────────────────────────┐      HTTPS + OAuth 2.1 PKCE       ┌──────────────────────────────────────┐
│    Claude Desktop      │ ──────────────────────────────────>│  Azure Container Apps                │
│   (N utenti BrandArt)  │                                    │  app: "brandart-mcp-gateway"          │
│                        │ <─── JSON-RPC (streamable HTTP) ───│                                       │
└────────────┬───────────┘                                    │   ┌─────────────────────────────────┐ │
             │ redirect al login                              │   │  microsoft-mcp (Python 3.12)    │ │
             ▼                                                │   │  + fastmcp streamable-http      │ │
┌────────────────────────┐     id_token JWT                   │   │  + authlib OAuth 2.1 proxy      │ │
│  Entra ID tenant       │ ──────────────────────────────────>│   │  + async Blob cache backend     │ │
│  BrandArt              │                                    │   └──────────────┬──────────────────┘ │
│  (App Registration     │ <── device code NOT USED ──────────│                  │                    │
│   "BrandArt MCP        │     (solo OAuth web flow)          │                  │ Graph API calls   │
│    Gateway")           │                                    └──────────────────┼────────────────────┘
└────────────────────────┘                                                       ▼
                                                              ┌──────────────────────────────────────┐
                                                              │     Microsoft Graph API               │
                                                              │   (mail, calendar, files, contacts)   │
                                                              └──────────────────────────────────────┘

  Supporting resources:
  - Azure Blob Storage      → container "mcp-tokens", blob per user_id
  - Azure Key Vault         → Entra ID Client Secret, JWT signing key
  - Application Insights    → logs + metrics + traces
  - Log Analytics Workspace → long-term retention
```

### Flow auth (primo uso)
1. Utente clicca "Aggiungi connettore personalizzato" in Claude Desktop
2. URL: `https://brandart-mcp.azurecontainerapps.io/mcp`
3. Claude Desktop chiama `GET /.well-known/oauth-authorization-server` → metadata
4. Claude Desktop avvia OAuth 2.1 PKCE flow → redirect a `/oauth/authorize`
5. Nostro server redirige a Entra ID `/oauth2/v2.0/authorize?client_id=<BRANDART_MCP>&...`
6. Utente fa login su Entra ID (SSO se già loggato in BrandArt)
7. Entra ID redirige a `/oauth/callback?code=<entra_code>`
8. Nostro server scambia `entra_code` → id_token + access_token Graph
9. Persiste tupla `(user_id, graph_refresh_token)` in Blob Storage
10. Genera **proprio** JWT per Claude (nostro access_token)
11. Redirige Claude Desktop a callback con `our_access_token`
12. Claude memorizza e usa per richieste MCP successive

### Flow tool call (use case steady state)
1. Claude Desktop: `POST /mcp` con header `Authorization: Bearer <our_access_token>`
2. Nostro server decodifica JWT → ricava `user_id`
3. Carica `graph_refresh_token` da Blob Storage
4. Ottiene access_token Graph fresh (refresh se scaduto)
5. Chiama Graph API per conto dell'utente
6. Ritorna risposta MCP JSON-RPC

---

## 4. Roadmap fasi (post-spike, ~10h totali)

| # | Fase | Durata stimata | Dipendenze | Status |
|---|------|----------------|------------|--------|
| 1 | HTTP transport (`http_app` + Starlette mount) + Dockerfile + `/health` | 2h | — | TODO |
| 2 | `auth_context.py` ContextVar + refactor `graph.request()` con fallback MSAL | 1h | Fase 1 | TODO |
| 3 | `AzureProvider` config + OBO middleware (o EntraOBOToken DI fallback) | 2-3h | Fase 2 | TODO |
| 4 | Azure Container Apps deploy (Key Vault + Managed Identity + ACR) | 3h | Fase 1, 3 | TODO |
| 5 | Test E2E Claude Desktop Custom Connector (Advanced Settings client_id manuale) | 1h | Fase 4 | TODO |
| 6 | Documentazione onboarding + commit + README update | 1h | Fase 5 | TODO |

Ordine sequenziale obbligatorio (ogni fase usa la precedente).

**Stima originale pre-spike**: 14h → **realistico 19-22h**. Post-spike: **10-11h** grazie a FastMCP `AzureProvider` nativo che copre Fase 3 quasi interamente. Buffer generoso rispetto al target 22 Apr.

**NOTA VERSIONE**: `AzureProvider` richiede FastMCP **3.x** (verificato: non presente in 2.8.0 installata). Upgrade a 3.2.4 da fare in **Fase 3**, non prima. Fasi 1-2 funzionano con 2.8.0 (`http_app()` verificato presente). Dry-run upgrade: zero conflitti, 3.2.4 pulito.

---

## 5. Fase 1 — HTTP transport + Dockerfile + health endpoint

### 5.1 Aggiungere HTTP entry point in `server.py`

Oggi `server.py` contiene solo `main()` che usa stdio. L'API corretta di FastMCP (verificata via spike) è **`mcp.http_app(path='/mcp')`** — restituisce un'ASGI app Starlette-based che si monta in un parent Starlette. **Critico**: passare `lifespan=mcp_app.lifespan` al parent, altrimenti il session manager non si inizializza (bug noto, nested lifespans non sono riconosciuti).

```python
# src/microsoft_mcp/server.py (append)

def main_http() -> None:
    """Run the MCP server over streamable HTTP (for remote deployment)."""
    import os
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.responses import PlainTextResponse
    from . import tools

    mcp_app = tools.mcp.http_app(path="/mcp")

    async def health(_request):
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/", app=mcp_app),  # Mount a root: /mcp diventa l'endpoint JSON-RPC
        ],
        lifespan=mcp_app.lifespan,  # REQUIRED: altrimenti FastMCP session manager non parte
    )

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
```

### 5.2 Esporre entry point in `pyproject.toml`

```toml
[project.scripts]
microsoft-mcp = "microsoft_mcp.server:main"
microsoft-mcp-auth = "microsoft_mcp.cli_auth:main"
microsoft-mcp-http = "microsoft_mcp.server:main_http"   # NEW
```

### 5.3 Note su route custom e auth bypass

FastMCP documenta esplicitamente che route custom montate a pari livello (come `/health`) **bypassano l'auth middleware by design** — comportamento corretto per liveness/readiness probes Azure. L'auth (Fase 3) si applicherà solo a `/mcp`.

Dipendenza nuova da aggiungere in `pyproject.toml`:

```toml
dependencies = [
    ...
    "uvicorn[standard]>=0.30.0",   # NEW
]
```

Starlette arriva transitivamente con FastMCP, non serve dichiararlo.

### 5.4 Dockerfile alla root del repo

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD curl -f http://localhost:8000/health || exit 1
CMD ["microsoft-mcp-http"]
```

### 5.5 `.dockerignore`

```
.git
.venv
__pycache__
*.pyc
.env
tests/
docs/
```

### 5.6 Test locale

```bash
docker build -t microsoft-mcp-http .
docker run --rm -p 8000:8000 \
  -e MICROSOFT_MCP_CLIENT_ID=<dev-app-id> \
  -e MICROSOFT_MCP_TENANT_ID=<brandart-tenant-id> \
  microsoft-mcp-http
# In altra shell:
curl http://localhost:8000/health   # atteso: "ok"
```

**Nota**: a Fase 1 il server HTTP funziona ma **senza OAuth**. Le chiamate /mcp risponderanno ma senza autenticazione utente (single-tenant, usa env var). OK per dev/test locale.

### 5.7 Exit criteria Fase 1
- [ ] `docker build .` ok
- [ ] Container risponde a `/health` con 200 OK
- [ ] Endpoint `/mcp` accetta JSON-RPC e lista i tool

---

## 6. Fase 2 — Multi-utente: ContextVar middleware + FastMCP storage

### 6.1 Rivisitato post-spike

L'approccio originale (cache MSAL custom per-utente su Blob Storage) **non serve più**. FastMCP `AzureProvider` (Fase 3) gestisce nativamente:
- Cache multi-utente via `client_storage` pluggable (default in-memory)
- OBO flow verso Graph API via `EntraOBOToken` dependency injection
- Isolamento automatico per `sub` claim

Questa fase si riduce a **due** lavori:

1. Predisporre un `ContextVar` per il Graph token (popolato da middleware in Fase 3)
2. Rendere `graph.request()` capace di leggere il token dal ContextVar quando presente, con fallback a MSAL per backward compat stdio

### 6.2 Nuovo modulo `auth_context.py`

```python
# src/microsoft_mcp/auth_context.py
from __future__ import annotations
from contextvars import ContextVar

# Popolato dal middleware FastMCP in HTTP mode (Fase 3).
# In stdio mode resta None → graph.request() cade su MSAL get_token(account_id).
current_graph_token: ContextVar[str | None] = ContextVar(
    "current_graph_token", default=None
)
```

### 6.3 Refactor `graph.py` (minimo)

```python
# src/microsoft_mcp/graph.py
from .auth_context import current_graph_token
from .auth import get_token

def request(method, path, account_id=None, ...):
    # HTTP mode: il middleware ha già fatto OBO, token è nel ContextVar
    ctx_token = current_graph_token.get()
    token = ctx_token if ctx_token else get_token(account_id)
    headers = {"Authorization": f"Bearer {token}", ...}
    ...
```

**Zero modifiche ai 35 tool in `tools.py`** — continuano a passare `account_id`, che in HTTP mode viene semplicemente ignorato (già coperto dal ContextVar).

### 6.4 Storage decisione MVP

Default FastMCP: **in-memory** `client_storage`. Vincoli implicati:
- `min-replicas: 1` obbligatorio (no scale-to-zero, altrimenti utenti riloggano a ogni cold start)
- `max-replicas: 1` per MVP (multi-replica richiederebbe storage condiviso)
- Container restart → utenti riloggano una volta (accettabile, raro)

Upgrade path W17 se utenti >5 o SLA richiesto: implementare adapter `key_value` su Azure Table Storage (pochi centesimi/mese, no Redis).

### 6.5 Nessuna dipendenza nuova in questa fase

`azure-storage-blob` **non serve più**. MSAL rimane (usato solo dal path stdio legacy).

### 6.6 Exit criteria Fase 2
- [ ] `auth_context.py` con ContextVar creato
- [ ] `graph.request()` legge da ContextVar con fallback MSAL
- [ ] Test stdio esistenti passano invariati (`uv run pytest`)
- [ ] Nessuna regressione backward compat

---

## 7. Fase 3 — AzureProvider + OBO middleware

### 7.1 Rivisitato post-spike

FastMCP fornisce nativamente `AzureProvider` (classe `fastmcp.server.auth.providers.azure.AzureProvider`) che implementa **tutto** il contenuto della vecchia Fase 3:
- OAuth 2.1 Authorization Server verso Claude Desktop (metadata, authorize, token, callback)
- OAuth Client verso Entra ID con state signing, PKCE, token exchange
- Emissione JWT propri con `jwt_signing_key` auto-generato (o configurabile)
- Cache multi-utente via `client_storage` (in-memory default → MVP OK)

**Non serve** più implementare manualmente authlib, oauth_proxy.py, metadata endpoint, state HMAC, JWT issuance. L'intera Fase 3 si riduce a: **configurare AzureProvider + scrivere middleware OBO→ContextVar**.

### 7.2 Configurazione `AzureProvider` in `server.py`

```python
# src/microsoft_mcp/server.py (main_http, integrato con Fase 1)
from fastmcp import FastMCP
from fastmcp.server.auth.providers.azure import AzureProvider

def build_mcp_with_auth() -> FastMCP:
    auth = AzureProvider(
        client_id=os.environ["ENTRA_CLIENT_ID"],
        client_secret=os.environ["ENTRA_CLIENT_SECRET"],
        tenant_id=os.environ["ENTRA_TENANT_ID"],
        base_url=os.environ["PUBLIC_BASE_URL"],   # es: https://brandart-mcp-gateway.<fqdn>
        required_scopes=["access_as_user"],       # scope custom definito sulla App Registration
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
    )
    # Recupero l'istanza mcp esistente e le assegno l'auth provider
    from . import tools
    tools.mcp.auth = auth
    return tools.mcp
```

### 7.3 Middleware OBO → ContextVar

Per evitare di toccare i 35 tool (vedi sez. 6.3), un middleware FastMCP intercetta ogni tool call, esegue OBO verso Graph, e popola `current_graph_token`:

```python
# src/microsoft_mcp/obo_middleware.py
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.auth.providers.azure import request_obo_token
from .auth_context import current_graph_token

GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]

class GraphOBOMiddleware(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next):
        # OBO exchange: dal JWT FastMCP → Graph access token per l'utente
        graph_token = await request_obo_token(GRAPH_SCOPES)
        token_ctx = current_graph_token.set(graph_token)
        try:
            return await call_next(context)
        finally:
            current_graph_token.reset(token_ctx)
```

**Nota**: il nome esatto `request_obo_token` va verificato contro la versione di FastMCP installata (l'API pubblica potrebbe essere `EntraOBOToken` come dependency-injection parameter; in tal caso la strategia ContextVar cambia leggermente — vedi 7.3-bis).

### 7.3-bis Alternativa: rifattorizzare `graph.py` per accettare OBO come parametro

Se FastMCP non espone OBO come call sincrona ma solo come dependency injection (`EntraOBOToken(...)`), pattern alternativo:
- Aggiungere un wrapper tool `_graph_wrap` che prende `graph_token: str = EntraOBOToken(...)` e lo salva nel ContextVar prima di delegare al tool vero
- Oppure refactor completo dei 35 tool per aggiungere il parametro `graph_token`

Decisione finale dopo il primo try: iniziare con 7.3 (middleware ContextVar), fallback a 7.3-bis se API FastMCP lo richiede.

### 7.4 Entra ID App Registration (setup su portal.azure.com)

```
Nome:         BrandArt MCP Gateway
Tipo:         Web
Redirect URI: https://brandart-mcp-gateway.<fqdn>.azurecontainerapps.io/auth/callback
               (+ http://localhost:8000/auth/callback per dev)
               NOTA: il path esatto è dettato da AzureProvider — verificare output di
               fastmcp.server.auth.providers.azure.AzureProvider.get_well_known_routes()
Expose an API: definisci scope custom "access_as_user" (default per AzureProvider)
API perm:     Microsoft Graph delegated
              - offline_access
              - User.Read
              - Mail.ReadWrite
              - Mail.Send
              - Calendars.ReadWrite
              - Files.ReadWrite
              - Contacts.Read
              - People.Read
Admin consent: Grant (tu sei Global Admin → 1 click, evita consent dialog a ogni utente)
Client secret: genera, salva in Key Vault con nome `entra-client-secret`
```

### 7.5 Secrets management (semplificato)

Env vars richieste dal container:

| Nome | Source | Note |
|---|---|---|
| `ENTRA_CLIENT_ID` | App Registration | plain env var OK |
| `ENTRA_CLIENT_SECRET` | Key Vault secret ref | segreto |
| `ENTRA_TENANT_ID` | `a315e1f0-b536-49ed-885b-7c31d23927de` | plain env var OK |
| `PUBLIC_BASE_URL` | `https://brandart-mcp-gateway.<fqdn>` | plain env var OK |

**Rimossi** rispetto al piano originale: `JWT_SIGNING_KEY` (FastMCP lo genera automaticamente), `AZURE_STORAGE_CONNECTION_STRING` (no Blob Storage nell'MVP).

Se serve persistenza JWT signing key tra restart (altrimenti tutti i client ri-autenticano a ogni deploy), passare esplicitamente il parametro `jwt_signing_key` ad `AzureProvider` da Key Vault — **raccomandato per MVP**.

### 7.6 Exit criteria Fase 3
- [ ] `GET /.well-known/oauth-authorization-server` ritorna metadata generato da AzureProvider
- [ ] Flow manuale browser: `/mcp` con bearer mancante → 401 con WWW-Authenticate → redirect a `/authorize` → Entra login → callback → JWT FastMCP ricevuto
- [ ] Middleware OBO popola ContextVar prima di ogni tool call
- [ ] `graph.request()` usa token da ContextVar, chiamata Graph API va a buon fine
- [ ] Multi-utente: due sessioni con utenti Entra diversi hanno token isolati

---

## 8. Fase 4 — Azure Container Apps deploy

### 8.1 Risorse Azure da creare (tutte nel resource group `rg-brandart-mcp`)

| Risorsa | SKU | Motivo |
|---|-----|--------|
| Resource Group `rg-brandart-mcp` | — | container logico |
| Log Analytics Workspace | PerGB2018 | retention log |
| Application Insights | Pay-as-you-go | telemetry |
| Container Registry ACR `acrbrandartmcp` | Basic | host Docker image |
| Container Apps Environment | Consumption | runtime |
| Container App `brandart-mcp-gateway` | Consumption (min=1, max=1) | il servizio |
| Key Vault `kv-brandart-mcp` | Standard | secrets (client_secret, jwt_signing_key) |
| User-assigned Managed Identity | — | auth ACA → Key Vault |

**Rimosso** rispetto al piano originale: Storage Account (no Blob Storage token cache — FastMCP in-memory).

### 8.2 Deploy via `az` CLI (sequenza comandi)

```bash
# 0. Login e set subscription
az login
az account set --subscription "<SUB_ID>"

# 1. Resource Group
RG=rg-brandart-mcp
LOC=westeurope
az group create -n $RG -l $LOC

# 2. Log Analytics + App Insights
az monitor log-analytics workspace create -g $RG -n log-brandart-mcp -l $LOC
LA_ID=$(az monitor log-analytics workspace show -g $RG -n log-brandart-mcp --query customerId -o tsv)
LA_KEY=$(az monitor log-analytics workspace get-shared-keys -g $RG -n log-brandart-mcp --query primarySharedKey -o tsv)

az monitor app-insights component create -g $RG -a appi-brandart-mcp -l $LOC \
  --workspace $(az monitor log-analytics workspace show -g $RG -n log-brandart-mcp --query id -o tsv)

# 3. Azure Container Registry
az acr create -g $RG -n acrbrandartmcp --sku Basic --admin-enabled true

# 4. Build & push image
az acr build -t microsoft-mcp:v0.1.0 -r acrbrandartmcp .

# 5. Key Vault + secrets (Storage Account non serve più nell'MVP)
az keyvault create -g $RG -n kv-brandart-mcp -l $LOC
az keyvault secret set --vault-name kv-brandart-mcp -n entra-client-secret --value "<DA_ENTRA_APP_REGISTRATION>"
az keyvault secret set --vault-name kv-brandart-mcp -n jwt-signing-key --value "$(openssl rand -hex 32)"

# 7. Managed Identity + role assignment
MI_ID=$(az identity create -g $RG -n mi-brandart-mcp --query id -o tsv)
MI_PRINCIPAL=$(az identity show -g $RG -n mi-brandart-mcp --query principalId -o tsv)
az role assignment create --assignee $MI_PRINCIPAL --role "Key Vault Secrets User" \
  --scope $(az keyvault show -n kv-brandart-mcp --query id -o tsv)

# 8. Container Apps Environment
az containerapp env create -g $RG -n cae-brandart-mcp -l $LOC \
  --logs-workspace-id $LA_ID --logs-workspace-key $LA_KEY

# 9. Container App
az containerapp create -g $RG -n brandart-mcp-gateway \
  --environment cae-brandart-mcp \
  --image acrbrandartmcp.azurecr.io/microsoft-mcp:v0.1.0 \
  --registry-server acrbrandartmcp.azurecr.io \
  --ingress external --target-port 8000 \
  --min-replicas 1 --max-replicas 1 \
  --user-assigned $MI_ID \
  --secrets \
    entra-client-secret=keyvaultref:https://kv-brandart-mcp.vault.azure.net/secrets/entra-client-secret,identityref:$MI_ID \
    jwt-key=keyvaultref:https://kv-brandart-mcp.vault.azure.net/secrets/jwt-signing-key,identityref:$MI_ID \
  --env-vars \
    ENTRA_CLIENT_ID=<NEW_APP_REGISTRATION_ID> \
    ENTRA_TENANT_ID=a315e1f0-b536-49ed-885b-7c31d23927de \
    ENTRA_CLIENT_SECRET=secretref:entra-client-secret \
    JWT_SIGNING_KEY=secretref:jwt-key \
    PUBLIC_BASE_URL=https://brandart-mcp-gateway.<REGION>.azurecontainerapps.io \
    APPLICATIONINSIGHTS_CONNECTION_STRING="<INSERISCI_DOPO>"

# NOTA min=1/max=1: FastMCP client_storage è in-memory per MVP.
# Scale-to-zero romperebbe sessioni OAuth (cold start 3-5s durante redirect).
# Upgrade a min=0 possibile solo con Redis/Table Storage in W17.

# 10. Recupera FQDN e aggiorna PUBLIC_BASE_URL + Entra redirect URI
FQDN=$(az containerapp show -g $RG -n brandart-mcp-gateway --query properties.configuration.ingress.fqdn -o tsv)
echo "Public URL: https://$FQDN/mcp"
```

### 8.3 Bicep equivalente (opzionale ma consigliato per IaC)

Creare `infra/main.bicep` come template ufficiale. Idempotente e versionabile. Rimandato a W17 se pressing sul 22 Apr.

### 8.4 CI/CD (opzionale, nice-to-have)

GitHub Actions workflow `.github/workflows/deploy.yml` che al push su master:
1. Build image con `az acr build`
2. Update revision Container App con nuova image tag
3. Smoke test `/health`

### 8.5 Exit criteria Fase 4
- [ ] Container App running, `/health` risponde 200 via HTTPS
- [ ] Logs visibili in Azure portal (Application Insights)
- [ ] Cold start < 5 secondi (scale-from-zero)
- [ ] Costi giornalieri monitorati (Cost Analysis widget)

---

## 9. Fase 5 — Test end-to-end con Claude Desktop Custom Connector

### 9.1 Onboarding primo utente (Mario)

1. Apri Claude Desktop → Impostazioni → Connettori → **Aggiungi connettore personalizzato**
2. Nome: `BrandArt Microsoft 365`
3. URL: `https://brandart-mcp-gateway.<FQDN>.azurecontainerapps.io/mcp`
4. Click "**Advanced settings**" → inserisci:
   - OAuth Client ID: `<ENTRA_CLIENT_ID>` (stesso della App Registration)
   - OAuth Client Secret: `<ENTRA_CLIENT_SECRET>` (raro che serva lato client per PKCE public flow, ma Claude Desktop lo richiede per alcuni setup)
   - **Nota**: da luglio 2025 Claude Desktop accetta client_id/secret manuali → evitiamo di implementare Dynamic Client Registration (RFC 7591) lato server
5. Click "Collega"
6. Browser: redirect Entra ID → login SSO BrandArt → consent (1-click grazie ad admin consent pre-fatto) → callback
7. Claude Desktop dice "Connesso"

### 9.2 Smoke test prompts

- "Elenca le mie email di oggi" → deve ritornare lista mail
- "Crea evento calendar domani 10:00 titolo 'Test MCP'" → evento creato
- "Trova tutti i file OneDrive modificati questa settimana" → lista file

### 9.3 Onboarding secondo utente (Rocco)

Stesso flow. Verifica che:
- Rocco vede SOLO la sua mail, non quella di Mario (isolamento token)
- Entrambi gli utenti funzionano in parallelo (multi-tenancy)

### 9.4 Exit criteria Fase 5
- [ ] 2 utenti collegati, entrambi completano tool call senza errori
- [ ] Log in Application Insights mostrano richieste isolate per user_id
- [ ] Application Insights traces: latenza p50 < 500ms, p99 < 2s

---

## 10. Fase 6 — Documentazione + cleanup

### 10.1 Aggiornare `README.md`

Nuova sezione "Remote Deployment" che spiega:
- URL endpoint per Claude Desktop Custom Connector
- Come richiedere accesso (chiedi a Mario di aggiungere al tenant)
- Come revocare (Entra ID → Users → revoke consent)

### 10.2 Aggiungere `DEPLOY.md` (separato dal README)

Documentazione per futuro sviluppatore/admin BrandArt:
- Comandi az cli per redeploy
- Come rollback (`az containerapp revision list` + `az containerapp revision activate`)
- Come fare debug live (log streaming)

### 10.3 Preservare modalità stdio (backward compat)

NON rimuovere `main()` stdio. Lasciare entrambe le modalità: stdio e HTTP. Utenti individuali possono continuare a usare stdio se lo preferiscono.

### 10.4 Commit messages (Conventional Commits)

```
feat(http): add streamable HTTP transport with /health endpoint
feat(auth): abstract token cache behind TokenStore protocol + BlobStore impl
feat(oauth): implement OAuth 2.1 proxy to Entra ID
chore(docker): add Dockerfile + .dockerignore for Container Apps
docs(deploy): add Azure Container Apps deployment guide
```

### 10.5 Exit criteria Fase 6
- [ ] README + DEPLOY.md aggiornati
- [ ] Tutte le modifiche committate con Conventional Commits
- [ ] Push su `master` del fork
- [ ] Tag `v0.2.0` per release

---

## 11. Decisioni aperte / TODO pre-start

### 11.1 Entra App Registration: nuova o riuso?

Oggi usiamo `61682051-ad6b-4d39-b4db-63f94da354ed` (App 2, device code, creata da Rocco).
Per Remote MCP serve **Web App** (non public client), redirect URI HTTPS fissa, client secret.

**Decisione**: creare **nuova** App Registration "BrandArt MCP Gateway" (Web). Mantenere la App 2 per backward compat con stdio flow.

### 11.2 Domain: default Azure o custom?

- Default: `brandart-mcp-gateway.<hash>.<region>.azurecontainerapps.io` (brutto ma gratis, HTTPS incluso)
- Custom: `mcp.brandart.it` (professional, 10€/anno + Cloudflare o Azure DNS)

**Decisione per 22 Apr**: default Azure, custom domain in W17.

### 11.3 JWT vs opaque token? **RISOLTA DA FASTMCP**

Non decisione nostra: `AzureProvider` emette JWT signed con `jwt_signing_key`. Noi forniamo la chiave (da Key Vault) per persistenza tra restart. Claim gestiti internamente.

### 11.4 DCR? **DECISIONE: NO, skippata**

Da luglio 2025 Claude Desktop accetta client_id/secret manuali via Advanced Settings. Skippiamo `/oauth/register`, l'utente inserisce credenziali App Registration al primo setup. Se utenti >5 (W17+), implementare DCR per ridurre friction onboarding.

### 11.5 Rate limiting?

Non nel MVP. Se abuso rilevato in W17, aggiungere via Azure Front Door o middleware `slowapi`.

### 11.6 Middleware OBO vs EntraOBOToken DI? **DA VERIFICARE IN FASE 3**

FastMCP espone due pattern per OBO:
- `EntraOBOToken(scopes)` come dependency-injection parameter nel tool
- API programmatica per middleware (nome esatto da verificare installando FastMCP)

Preferiamo middleware ContextVar (sez. 7.3) per preservare firma dei 35 tool. Fallback: wrapper tool o refactor esplicito se API middleware non disponibile.

---

## 12. Rollback plan

Se qualcosa va storto post-deploy:

1. **Soft rollback**: `az containerapp revision activate` su revision precedente
2. **Hard rollback**: revoke OAuth App Registration, utenti ricadono su setup stdio vecchio (ancora funzionante grazie a 10.3)
3. **Emergency stop**: `az containerapp update --min-replicas 0 --max-replicas 0` → scala a zero, nessuno risponde

---

## 13. Prompt operativo per Kai (Sonnet dev)

Quando apri questo repo con `cd Projects/04-Production/microsoft-mcp && claude`, copia-incolla:

> Sono Kai, dev Sonnet. Leggi `PLAN-AZURE-REMOTE-MCP.md` dalla root del repo. Iniziamo dalla **Fase 1** (HTTP transport + Dockerfile).
>
> Vincoli:
> - Preservo il comportamento stdio esistente (`main()` non tocca). Aggiungo `main_http()` a fianco.
> - Non rompo test esistenti (`uv run pytest` deve passare).
> - Ogni fase deve superare i suoi "exit criteria" prima di passare alla successiva.
> - Commit granulari con Conventional Commits.
>
> Quando hai finito Fase 1 (test locale `docker build` + `/health` OK), fermati e chiedi conferma prima di Fase 2.

---

## 14. Stato attuale (13 Apr 2026)

### Baseline (committato)
- [x] Fork pubblico `github.com/mariomosca/microsoft-mcp` attivo
- [x] Entry point CLI `microsoft-mcp-auth` (commit `aec4a19`)
- [x] Browser-based auto auth per stdio (commit `e9dcbc4`)
- [x] Bug fix `expires_in` string (commit `fa2e5ea`)

### Spike preliminari (13 Apr 2026, completati)
- [x] **Spike 1** — FastMCP ASGI: API corretta è `mcp.http_app(path='/mcp')`, richiede `lifespan=mcp_app.lifespan` passthrough
- [x] **Spike 2** — Claude Desktop Custom Connector: Advanced Settings accetta client_id/secret manuali da luglio 2025 → DCR non obbligatoria
- [x] **Spike 3** — FastMCP auth provider: `AzureProvider` nativo copre l'intero OAuth 2.1 proxy verso Entra ID (no authlib manuale)
- [x] **Spike 4** — OBO flow: `EntraOBOToken` dependency injection o API programmatica per middleware → Graph token pronto nei tool

### Fasi da eseguire
- [ ] **Fase 1 — HTTP transport** (2h)
- [ ] **Fase 2 — ContextVar + graph.py refactor** (1h)
- [ ] **Fase 3 — AzureProvider + OBO middleware** (2-3h)
- [ ] **Fase 4 — Deploy ACA** (3h)
- [ ] **Fase 5 — Test end-to-end** (1h)
- [ ] **Fase 6 — Documentation** (1h)

**Totale stimato post-spike**: ~10-11h (vs 19-22h del piano originale).

**Prossima azione**: aprire il repo con Claude Code, incollare il prompt Sezione 13, iniziare Fase 1 (delegabile a Kai/Sonnet).
