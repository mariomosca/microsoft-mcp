# microsoft-mcp — Stato Deploy Azure

## Endpoint produzione
```
https://brandart-mcp-gateway.jollyfield-bcd8d619.westeurope.azurecontainerapps.io/mcp
```

## Claude Desktop
Connettori → "Brandart Microsoft 365" → URL sopra → lascia Advanced Settings vuote

## Risorse Azure (rg-brandart-mcp, westeurope)
- Container App: `brandart-mcp-gateway` (image **v0.2.12** — read_attachment_text generic reader + 11 formati)
- Container Registry: `acrbrandartmcp.azurecr.io`
- Key Vault: `kv-brandart-mcp` (secrets: entra-client-secret, jwt-signing-key, redis-connection-string)
- App Insights: `appi-brandart-mcp`
- **Redis**: `redis-brandart-mcp` (Basic C0, 250MB) — persiste OAuth client registrations + token state
- Entra App: `Brandart MCP Gateway` (client_id: 4506b2a9-cdc5-4e7c-8e43-cc0307cb9f61)

## Architettura auth state
v0.2.4 e precedenti usavano filesystem ephemeral (FastMCP default platformdirs) → ogni restart container = re-OAuth manuale obbligato per tutti i client.
v0.2.5 introduce `client_storage=RedisStore(REDIS_URL)` → registrations e token sopravvivono a restart, redeploy, scaling.

## Redeploy (dopo modifiche codice)
```bash
VERSION=vX.Y.Z
git push origin master
az acr build -t microsoft-mcp:$VERSION -r acrbrandartmcp .
az containerapp update -g rg-brandart-mcp -n brandart-mcp-gateway --image acrbrandartmcp.azurecr.io/microsoft-mcp:$VERSION
```

## Rotation Redis primaryKey
```bash
NEW_KEY=$(az redis regenerate-keys -g rg-brandart-mcp -n redis-brandart-mcp --key-type Primary --query primaryKey -o tsv)
NEW_URL="rediss://:${NEW_KEY}@redis-brandart-mcp.redis.cache.windows.net:6380/0"
az keyvault secret set --vault-name kv-brandart-mcp --name redis-connection-string --value "$NEW_URL"
# Container app risincronizza automaticamente (sync ogni 30 min) o force restart:
az containerapp revision restart -g rg-brandart-mcp -n brandart-mcp-gateway --revision $(az containerapp revision list -g rg-brandart-mcp -n brandart-mcp-gateway --query "[?properties.active].name | [0]" -o tsv)
```

## Storia versioni deploy
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
