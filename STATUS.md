# microsoft-mcp — Stato Deploy Azure

## v0.4.0 (27 Aug 2026) — 4 tool SharePoint read-only (brief Leo 27/08) — NON ANCORA DEPLOYATA
- **Nuovi tool** (tutti read-only, auth delegata → permessi SharePoint dell'utente rispettati by design):
  - `sp_search` — ricerca driveItem cross-site via `/search/query`; ritorna metadati essenziali (name, item_id, drive_id, type, size, modified, web_url). Nota: nei risultati search il facet `folder` non è affidabile (le cartelle possono apparire come `file`); `sp_list_folder` invece è corretto.
  - `sp_list_folder` — children di una cartella via `/drives/{driveId}/...` (per item_id, per path drive-relative, o root), paginato, solo metadati.
  - `sp_get_file` — contenuto file: default estrazione testo server-side (riusa `_extract_text`: xlsx/pdf/docx/csv/... , cap 25MB / 50k chars), `save_path` per download raw in streaming.
  - `sp_get_excel` — xlsx via Graph Excel API senza download: senza `worksheet` → lista fogli; con `worksheet` → usedRange come TSV; con `cell_range` → finestra specifica. Fallback automatico download+openpyxl su 4xx dell'Excel API.
- **Scope: NESSUN admin consent necessario.** Verificato live (27/08, account mario.mosca): i tool usano solo endpoint drive-scoped coperti dal delegated `Files.ReadWrite` già consentito. `Sites.Read.All` NON aggiunto agli authorize scopes: richiederlo avrebbe forzato un nuovo admin consent rompendo i login dei connettori fino alla grant. Da rivalutare solo se servissero endpoint `/sites` (metadati site).
- **Smoke test 27/08 (stdio locale, token .default = stesso permission set dell'OBO gateway)**: search `DATA_PLATFORM_TEAM` su 100-SHARED OK; list folder `POWERBI/Integrazione Fabric/DATA_PLATFORM_TEAM` OK; `Data_Catalog_Fabric.xlsx` trovato in `04_DOCUMENTAZIONE/DOCUMENTAZIONE_TECNICA`; Excel API: 10 fogli listati, usedRange `Legenda` e range `Gold Tables Metadata!A1:D4` letti OK; estrazione testo xlsx OK. Regression: list_emails/list_events/list_files OK. Nota: la search full-text non trova "Data_Catalog_Fabric" per query esatta (tokenizzazione underscore dell'indice SharePoint, non un bug del tool) — trovato via list della cartella.
- Deploy: in attesa di ok Mario (procedura v0.3.5 su `brandart-mcp-gateway`; il deploy azzera anche il contatore del leak fastmcp ~22MB/g).

## v0.3.5 (26 Aug 2026) — grace period rotation refresh token + fix OOM/download (brief Leo 26/08)
Risposta all'incidente "connettori scollegati" del 25-26/08 (Mario + Massimo):
- **Rotation grace period** (`oauth_grace.py`, `GraceAzureProvider`): il vecchio refresh token resta utilizzabile per una finestra di grazia (default 60s, env `OAUTH_ROTATION_GRACE_SECONDS`, 0 = strict) dopo ogni rotation. Un client che non riceve/salva il token ruotato (risposta persa, kill del container mid-flight, doppia sessione sullo stesso connettore) può riprovare col vecchio token e ottiene una coppia fresca invece di `invalid_grant` → niente più "connettore collegato ma non autorizzato". Il retry graced NON estende la catena (one-shot). Nessun token extra in chiaro nello store: si ri-armano le stesse entry hash-keyed di FastMCP + un marker di rotation (TTL 24h) usato solo per logging.
- **Log espliciti reuse-detected** (richiesta Leo): `Refresh token reuse detected within grace window ...` (retry onorato), `Refresh token reuse detected: token was rotated Ns ago ...` (rifiutato, fuori grace), `... JTI mapping already consumed` (rifiutato in exchange). Logger `fastmcp.microsoft_mcp.oauth_grace` → ContainerAppConsoleLogs_CL.
- **Mitigazione OOM** (exit 137 x3 il 18/08, limite 1Gi): cap 25 MB (`ATTACHMENT_PARSE_MAX_BYTES`) sull'estrazione testo allegati (`read_attachment_text`, `read_event_attachment`, lettura OneDrive) con pre-check `$select=size` prima di scaricare i contentBytes (un file grande costa ~2.3x la sua size in RAM: base64 JSON + bytes). I tool di download restano senza cap (streaming). **Infra**: Container App portato da 0.5 vCPU / 1Gi a 1.0 vCPU / 2Gi.
- **Fix `get_file`** (raffica `RuntimeError: Failed to download file` del 25/08 17:38 UTC): rimosso `subprocess curl` → streaming httpx a chunk da 1 MB con errori HTTP leggibili nei log. Rimossa la dipendenza da curl nel runtime.
- Test: `tests/test_oauth_grace.py` (8 test: grace armato, reuse onorato entro finestra, catena non estesa, grace=0 strict).

## v0.3.4 (20 Jul 2026) — fix body merge (quote persa) + cc su reply/forward
- **Bugfix critico**: passando `message.body` a createReply/createForward, Graph SOSTITUIVA il body → quoted history persa e contentType degradato a text (visto su forward fatture Deloitte). Fix: la bozza si crea SENZA body (Graph mette quote+allegati+html), poi PATCH che inietta il nuovo testo subito dopo `<body>` preservando la history sotto. Vale per reply, reply-all, forward.
- Nuovo param `cc` su `create_reply_draft` / `create_reply_all_draft` / `create_forward_draft` (helper `_build_reply_draft` esteso con `cc_recipients`). Risolve il caso "Guido in copia" sul forward.

## v0.3.3 (20 Jul 2026) — create_forward_draft
- Nuovo tool `create_forward_draft(email_id, to, body?, body_type?, attachments?)`: crea una bozza di **inoltro** (draft-only, mai send) via Graph `createForward`. Gli **allegati originali** del messaggio inoltrato sono copiati automaticamente sulla bozza (nessun re-upload). Body HTML/text + eventuali allegati extra.
- Helper `_build_reply_draft` esteso con `to_recipients` (usato solo dal forward). Reply/reply-all invariati.

## v0.3.2 (20 Jul 2026) — kill-switch invio mail
- Nuovo guard `_assert_send_enabled()`: se env `MICROSOFT_MCP_DISABLE_SEND` è truthy (1/true/yes/on), `send_email` / `reply_to_email` / `reply_all_email` sollevano errore e rimandano ai `*_draft`. Hard-block a livello codice, reversibile senza rebuild (basta togliere/settare la env sul Container App).
- **Attivo in prod**: env `MICROSOFT_MCP_DISABLE_SEND=true` sul Container App → invio mail bloccato per policy Mario (workflow = solo bozze, invio manuale da Outlook).
- `respond_event` (RSVP inviti calendar) NON gated: resta attivo. I `create_*_draft` restano pieni.

## v0.3.1 (20 Jul 2026) — reply-draft HTML+allegati + fix filtro flag
- `list_emails` con `only_flagged=True`: rimosso `$orderby` (Graph 400 con advanced query su `flag/flagStatus`) → ordinamento client-side per `receivedDateTime desc`.
- `create_reply_draft` / `create_reply_all_draft`: **draft-only invariato** (mai `/send`). Nuovi param `body_type` ("html" default | "text") e `attachments`/`attachments_inline`. Il body HTML non è più forzato a Text; gli allegati (piccoli inline, grandi via upload session) si aggiungono sulla bozza. Thread integrity + quoting preservati da createReply/createReplyAll.

## v0.3.0 (20 Jul 2026) — flag + custom folders + count
Fix del triage mail flag, prima non gestibile via MCP (obbligava a workaround Graph/CLI).
- `list_emails`: aggiunto `flag` al `$select`; nuovo param `only_flagged` (`$filter=flag/flagStatus eq 'flagged'`).
- `get_email`: aggiunto `flag` (+ `categories`) al `$select` esplicito → lo stato flag è visibile aprendo la singola mail.
- **Folder custom**: nuovo helper `_resolve_folder_id()` che risolve per display-name anche le child folder (BFS su `childFolders`). Prima `list_emails`/`move_email` sulle custom (Prioritaria, ToMe, Follow-up) davano 400. Ora funzionano.
- Nuovo tool `count_emails(folder, only_flagged, only_unread)` → conteggio via `$count` senza scaricare i corpi ("quante flaggate/non lette ho").
- `graph.py`: `ConsistencyLevel: eventual` esteso ai filtri `flag/flagStatus`.

## Endpoint produzione
```
https://brandart-mcp-gateway.jollyfield-bcd8d619.westeurope.azurecontainerapps.io/mcp
```

## Claude Desktop
Connettori → "Brandart Microsoft 365" → URL sopra → lascia Advanced Settings vuote

## Risorse Azure (rg-brandart-mcp, westeurope)
- Container App: `brandart-mcp-gateway` (image **v0.3.4** — fix body merge quote + cc su reply/forward; kill-switch invio mail (MICROSOFT_MCP_DISABLE_SEND=true attivo); OAuth store su Postgres durabile)
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
