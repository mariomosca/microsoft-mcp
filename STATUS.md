# microsoft-mcp — Stato Deploy Azure

## Endpoint produzione
```
https://brandart-mcp-gateway.jollyfield-bcd8d619.westeurope.azurecontainerapps.io/mcp
```

## Claude Desktop
Connettori → "Brandart Microsoft 365" → URL sopra → lascia Advanced Settings vuote

## Risorse Azure (rg-brandart-mcp, westeurope)
- Container App: `brandart-mcp-gateway` (image **v0.2.14** — OAuth store su Postgres durabile, rev 0000017)
- Container Registry: `acrbrandartmcp.azurecr.io`
- Key Vault: `kv-brandart-mcp` (secrets: entra-client-secret, jwt-signing-key)
- App Insights: `appi-brandart-mcp`
- **OAuth store**: PostgreSQL — DB `mcp_oauth` su `brandart-visitors-db` (Postgres 16 flexible, BrandartSvilRG), utente dedicato `mcp_oauth_user`. Tabella auto-creata `kv_store`. Connection string nel secret `oauth-store-url`, montata come env `OAUTH_STORE_URL`. Il Container App raggiunge il Postgres via regola firewall `AllowAzureServices`.
- ~~Redis: `redis-brandart-mcp`~~ — **eliminato il 24 Jun 2026** (mai realmente collegato, sostituito da Postgres). Rimossi anche i secret orfani `redis-url` (Container App) e `redis-connection-string` (Key Vault). Il codice mantiene un fallback `elif REDIS_URL` ma non è configurato.
- Entra App: `Brandart MCP Gateway` (client_id: 4506b2a9-cdc5-4e7c-8e43-cc0307cb9f61)

## Architettura auth state
v0.2.4 e precedenti usavano filesystem ephemeral (FastMCP default platformdirs) → ogni restart container = re-OAuth manuale obbligato per tutti i client.
v0.2.5 introduceva `client_storage=RedisStore(REDIS_URL)`, MA in produzione `REDIS_URL` **non è mai stato montato** come env nel container → FastMCP cadeva sul file store ephemeral. Risultato: ogni riciclo del Container App (manutenzione Azure, redeploy) azzerava lo stato OAuth → `invalid_token` 401 → re-login forzato per tutti gli utenti "dopo qualche giorno". Bug latente da v0.2.5 a v0.2.13.
**v0.2.14** (24 Jun 2026) migra lo store a **PostgreSQL durabile** (`OAUTH_STORE_URL` → `PostgreSQLStore`): client registrations, upstream tokens (cifrati), JTI mappings e refresh token persistono su disco e sopravvivono a restart/redeploy/node-recycle/scaling. Redis resta come fallback nel codice (`elif REDIS_URL`) ma non è configurato. Refresh/upstream token TTL 30gg con rotazione ad ogni uso → connessione permanente finché usata ≥1 volta/30gg.

> Token a riposo: gli upstream Graph token sono cifrati nello store; i refresh token salvati solo come hash/metadata. Anche con accesso al DB non sono estraibili in chiaro.

## Redeploy (dopo modifiche codice)
```bash
VERSION=vX.Y.Z
git push origin master
az acr build -t microsoft-mcp:$VERSION -r acrbrandartmcp .
az containerapp update -g rg-brandart-mcp -n brandart-mcp-gateway --image acrbrandartmcp.azurecr.io/microsoft-mcp:$VERSION
```

## Rotation password OAuth store (utente Postgres `mcp_oauth_user`)
```bash
# Cambia la password del ruolo dedicato sul DB mcp_oauth, poi aggiorna il secret.
# Serve una firewall-rule temp sull'IP corrente per raggiungere il PG da fuori Azure.
MYIP=$(curl -s https://api.ipify.org)
az postgres flexible-server firewall-rule create -g BrandartSvilRG --name brandart-visitors-db \
  --rule-name tmp-rotate --start-ip-address "$MYIP" --end-ip-address "$MYIP"
NEW_PASS=$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32)
PGPASSWORD="<admin-pass>" psql "host=brandart-visitors-db.postgres.database.azure.com port=5432 user=brandartadmin dbname=mcp_oauth sslmode=require" \
  -c "ALTER ROLE mcp_oauth_user PASSWORD '$NEW_PASS';"
NEW_URL="postgresql://mcp_oauth_user:${NEW_PASS}@brandart-visitors-db.postgres.database.azure.com:5432/mcp_oauth?sslmode=require"
az containerapp secret set -g rg-brandart-mcp -n brandart-mcp-gateway --secrets "oauth-store-url=$NEW_URL"
# Il secretref si aggiorna ma serve restart per rileggerlo:
az containerapp revision restart -g rg-brandart-mcp -n brandart-mcp-gateway --revision $(az containerapp revision list -g rg-brandart-mcp -n brandart-mcp-gateway --query "[?properties.active].name | [0]" -o tsv)
az postgres flexible-server firewall-rule delete -g BrandartSvilRG --name brandart-visitors-db --rule-name tmp-rotate --yes
# NB: la pass admin di brandartadmin è recuperabile da DATABASE_URL della webapp brandart-visitors (BrandartSvilRG).
```

## Storia versioni deploy
- **v0.2.14** (24 Jun 2026) — **fix scollegamento connettore "dopo qualche giorno"**. Root cause: `REDIS_URL` mai montato come env nel container → `client_storage=None` → file store ephemeral wipato ad ogni riciclo del Container App → `invalid_token` 401 → re-OAuth forzato per tutti. La "Redis v0.2.5" non era mai stata realmente attiva in prod. Fix: store migrato a **PostgreSQL durabile** (`PostgreSQLStore(OAUTH_STORE_URL)`), DB `mcp_oauth` su `brandart-visitors-db`, utente dedicato `mcp_oauth_user`. Codice: ~6 righe `server.py` (Postgres primario, Redis fallback) + dep `py-key-value-aio[redis,postgresql]` (asyncpg). Verificato E2E: `/register` → tabella `kv_store` popolata; login reale → 5 record (clients, upstream-tokens cifrati, jti-mappings, refresh-tokens), TTL 30gg. Revision `0000017`. Rollback: redeploy `v0.2.13`.
- **v0.2.13** (9 Jun 2026) — solo docstring (no logic). Il modello sceglieva `get_event_attachment` (download) quando l'utente chiedeva di LEGGERE → dead-end su base64. Descrizioni rese direttive: read_* = "USE THIS to read/open/view contents", get_* = "DOWNLOAD ... NOT for reading". Revision `0000016`. Commit `5594dc0`.
- **v0.2.12** (9 Jun 2026) — lettura generica "qualsiasi file". Nuovo tool `read_attachment_text` (account_id + una sorgente: event_id+attachment_id | email_id+attachment_id | onedrive_file_id) → estrae TESTO lato server per: text/csv/tsv/md/json/xml/yaml, xlsx/xlsm/ods, pdf, docx/odt, pptx, rtf, html, eml, msg. Mai base64. Motore `_extract_text` esteso (deps: python-pptx, striprtf, odfpy, beautifulsoup4, extract-msg). OneDrive read via `@microsoft.graph.downloadUrl` in-memory. Unsupported (immagini, .doc/.xls/.ppt legacy, archivi) → kind=unsupported + hint download. NB: Claude Desktop cachea la tool-list → ricaricare il connettore dopo il deploy. Revision `0000015`. Commit `576b47d`.
- **v0.2.11** (9 Jun 2026) — fix definitivo blocco lettura allegato. Root cause vero (NON la dimensione): `get_event_attachment` ritornava i byte come base64 nel tool result; anche un xlsx da 28KB veniva rimbalzato a `create_file` e il blob base64 nel context bloccava Claude Desktop (e comunque xlsx/pdf non sono leggibili dai byte grezzi). Best practice MCP (modelcontextprotocol.io / llmindset): i byte NON devono mai entrare nel context. Soluzione: nuovo tool `read_event_attachment` che estrae TESTO lato server (openpyxl/pypdf/python-docx/csv/txt) e ritorna solo testo; `get_event_attachment` ora carica su OneDrive e ritorna `web_url` (mai base64). Deps: openpyxl, pypdf, python-docx. Revision `0000014`. Commit `d544a7a`.
- **v0.2.10** (9 Jun 2026) — fix blocco lettura allegato grande su Claude Desktop. `get_event_attachment` ritornava tutto il base64 inline → un PDF biglietto (centinaia di KB / MB) saturava il context del modello e bloccava il client (server logava 200 OK, non era un crash backend). Ora: inline solo se ≤256 KB (`max_inline_size`), altrimenti upload su OneDrive (`Attachments/Events/`) + ritorna `onedrive_file_id`+`web_url`. Messaggio chiaro per referenceAttachment. Revision `0000013`. Commit `2436f7b`.
- **v0.2.9** (9 Jun 2026) — allegati eventi calendario: nuovi tool `list_event_attachments` + `get_event_attachment` (GET `/me/events/{id}/attachments` + download by id, base64 inline per HTTP o `save_path` per stdio). Specchia il pattern email. Nessun cambio scope Azure (`Calendars.Read` copre). Use case CEO: biglietti aerei allegati ad appuntamenti → PNR/orari. Revision `0000012`. Commit `a5e9c42`. Smoke E2E: `DESKTOP-SMOKE-event-attachments.md`.
- **v0.2.8** (5 Jun 2026) — fix inline-base64 per allegati email (`create_email_draft`/`send_email` ora accettano `attachments_inline=[{name, content_base64}]`). Necessario su remote HTTP: server e client non condividono filesystem, quindi `attachments` (path locale) non funziona via connettore. Revision `0000011`.
- v0.2.7 (3 Jun) — tag orfano in ACR buildato da `6fe283c` con numerazione +1; **non corrisponde a un commit dedicato**. Superato da v0.2.8.
- v0.2.6 (5 Jun) — primo build del fix allegati, sostituito da v0.2.8 per riallineare la numerazione sopra v0.2.7.
- v0.2.5 — Redis client_storage per OAuth state.

> Nota numerazione: il tag immagine ACR NON è agganciato a git tag. Per il prossimo deploy: `VERSION` = ultimo tag ACR + 1 (`az acr repository show-tags -n acrbrandartmcp --repository microsoft-mcp --orderby time_desc`).

## TODO rimanenti
- [ ] Test con Rocco (secondo utente) per multi-tenancy
- [ ] README sezione "Remote Deployment"
- [ ] DEPLOY.md (guida admin)
- [ ] (futuro) Custom domain mcp.brandart.it
- [ ] (futuro) CI/CD GitHub Actions
- [ ] (futuro) Redis Private Endpoint (ora public + TLS 1.2 + key auth)
