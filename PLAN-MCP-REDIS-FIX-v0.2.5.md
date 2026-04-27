# PLAN - MCP Gateway v0.2.5 - Redis client_storage

**Data**: 2026-04-27
**Owner**: Mario + Alita
**Trigger**: ricorrenti `invalid_token` post-restart container app. Root cause: FastMCP `AzureProvider` usa filesystem ephemeral di default per `client_storage`. Container restart → registrations + token persi → loop OAuth lato client claude.ai.
**Goal**: rendere il gateway resiliente a restart, scaling, redeploy. Mai piu' re-OAuth manuale.

---

## Stato pre-fix

- Container app `brandart-mcp-gateway` (rg-brandart-mcp, westeurope), revision 0000006, image v0.2.4
- `AzureProvider` istanziato senza `client_storage` -> default file-based su `/root/.local/share/...` (ephemeral)
- Secrets in `kv-brandart-mcp`: `entra-client-secret` (valido fino 2028), `jwt-signing-key`
- Workaround attivo: restart 27 Apr 11:30 + reconnect connector claude.ai

## Decisioni architetturali

| Scelta | Valore | Motivo |
|---|---|---|
| Storage backend | **Azure Cache for Redis Basic C0** (250MB) | TTL nativo, multi-replica ready, ~16€/mese |
| Network | Public endpoint + auth + TLS (`rediss://`) | Setup minimale; Private endpoint = upgrade post-MVP |
| Region | westeurope (stessa di container app) | Latency <2ms |
| Connection string in | Key Vault `kv-brandart-mcp/secrets/redis-connection-string` | Secret rotation discipline |
| Adapter | `key_value.aio.stores.redis.RedisStore` (gia' in venv) | Zero codice custom |

---

## Step-by-step

### 1. Provisioning Redis (15 min lavoro + 15-20 min wait)

```bash
RG=rg-brandart-mcp
LOCATION=westeurope
REDIS_NAME=redis-brandart-mcp
KV_NAME=kv-brandart-mcp

# Crea Redis Basic C0
az redis create \
  -g $RG \
  -n $REDIS_NAME \
  -l $LOCATION \
  --sku Basic \
  --vm-size c0 \
  --redis-version 6 \
  --enable-non-ssl-port false \
  --minimum-tls-version 1.2

# Wait provisioning
az redis show -g $RG -n $REDIS_NAME --query "provisioningState" -o tsv
# expected: Succeeded (poll fino a quando non e' Succeeded)
```

### 2. Salvare connection string in Key Vault (5 min)

```bash
PRIMARY_KEY=$(az redis list-keys -g $RG -n $REDIS_NAME --query primaryKey -o tsv)
HOST="${REDIS_NAME}.redis.cache.windows.net"
REDIS_URL="rediss://:${PRIMARY_KEY}@${HOST}:6380/0"

az keyvault secret set \
  --vault-name $KV_NAME \
  --name redis-connection-string \
  --value "$REDIS_URL"
```

### 3. Aggiungere secret + env var al container app (10 min)

```bash
KV_URL="https://${KV_NAME}.vault.azure.net/secrets/redis-connection-string"
MI_ID=$(az containerapp show -g $RG -n brandart-mcp-gateway \
  --query "identity.userAssignedIdentities | keys(@)[0]" -o tsv)

az containerapp secret set \
  -g $RG -n brandart-mcp-gateway \
  --secrets "redis-url=keyvaultref:${KV_URL},identityref:${MI_ID}"

az containerapp update \
  -g $RG -n brandart-mcp-gateway \
  --set-env-vars "REDIS_URL=secretref:redis-url"
```

### 4. Patch `src/microsoft_mcp/server.py` (10 min)

Modifica `_configure_auth()`:

```python
def _configure_auth() -> None:
    from fastmcp.server.auth.providers.azure import AzureProvider

    entra_client_id = os.environ["ENTRA_CLIENT_ID"]
    entra_client_secret = os.environ["ENTRA_CLIENT_SECRET"]
    entra_tenant_id = os.environ["ENTRA_TENANT_ID"]
    public_base_url = os.environ["PUBLIC_BASE_URL"]

    client_storage = None
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        from key_value.aio.stores.redis import RedisStore
        client_storage = RedisStore(url=redis_url, default_collection="mcp-oauth")

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
        client_storage=client_storage,
    )

    mcp.auth = auth
```

`pyproject.toml`: aggiungere `redis>=5.0` se non gia' presente (verificare; py-key-value-aio[redis] dovrebbe gia' tirarlo).

### 5. Build + deploy v0.2.5 (15 min)

```bash
cd /Users/mariomosca/work-hub/Projects/04-Production/microsoft-mcp
git add -A
git commit -m "feat(auth): add Redis client_storage for OAuth state persistence"
git tag v0.2.5
git push origin master --tags

az acr build -t microsoft-mcp:v0.2.5 -r acrbrandartmcp .
az containerapp update -g $RG -n brandart-mcp-gateway \
  --image acrbrandartmcp.azurecr.io/microsoft-mcp:v0.2.5
```

### 6. Test validazione (20 min)

1. **Health check**: `curl https://brandart-mcp-gateway.../health` -> `ok`
2. **Connect claude.ai**: rimuovi connector, aggiungi, login, invoca tool
3. **Persistence test critico**: 
   ```bash
   az containerapp revision restart -g $RG -n brandart-mcp-gateway \
     --revision $(az containerapp revision list -g $RG -n brandart-mcp-gateway --query "[?properties.active].name | [0]" -o tsv)
   ```
4. Senza touch claude.ai, **invoca un tool**. Deve funzionare. Se SI, fix riuscito.
5. **Test 2 (Mario+Rocco)**: Rocco fa OAuth flow su sua claude.ai. Restart. Entrambi continuano a funzionare.

### 7. Documentazione (15 min)

- `STATUS.md`: aggiornare versione a v0.2.5, rimuovere known issue, aggiungere "Redis backed OAuth state"
- `README.md`: sezione "Operations / OAuth state storage"
- `runbook-rotation.md` (nuovo): come ruotare Redis primaryKey + redis-url secret in KV

---

## Costi mensili stimati

| Risorsa | Costo |
|---|---|
| Container App (1 replica min, sempre on) | ~10€ |
| ACR Basic | ~5€ |
| Key Vault | <1€ |
| App Insights | <2€ |
| **Redis Basic C0 (NEW)** | **~16€** |
| **Totale gateway** | **~34€/mese** |

## Rollback plan

Se v0.2.5 ha problemi:
```bash
az containerapp update -g $RG -n brandart-mcp-gateway \
  --image acrbrandartmcp.azurecr.io/microsoft-mcp:v0.2.4
```
Redis rimane allocato ma inutilizzato (-> cancellabile). Costo evitato:
```bash
az redis delete -g $RG -n redis-brandart-mcp --yes
```

## Future work (post v0.2.5)

- [ ] Private endpoint Redis (sicurezza)
- [ ] Redis tier Standard (HA SLA) quando >5 utenti BrandArt attivi
- [ ] Custom domain `mcp.brandart.it`
- [ ] CI/CD GitHub Actions (build+deploy automatico)
- [ ] Monitoring: alert su 401 spike (App Insights query)
