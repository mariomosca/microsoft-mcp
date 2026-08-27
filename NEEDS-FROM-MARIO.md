# NEEDS FROM MARIO

## v0.4.0 — SharePoint tools (27 Aug 2026)

### Admin consent: NON SERVE
Il brief prevedeva l'aggiunta del delegated `Sites.Read.All` con probabile admin consent.
Verificato live il 27/08: i 4 tool `sp_*` usano solo endpoint drive-scoped
(`/drives/{driveId}/...`, `/search/query` su driveItem), già coperti dal delegated
`Files.ReadWrite` consentito sull'app registration. Il test locale usa scope `.default`
come l'OBO del gateway, quindi il permission set in produzione è identico.

`Sites.Read.All` NON è stato aggiunto agli authorize scopes: richiederlo senza grant
preventiva avrebbe rotto il login dei connettori ("need admin approval") fino alla
concessione. Se in futuro serviranno endpoint `/sites` (metadati/elenco site), andrà
aggiunto il permesso in app registration + admin consent PRIMA di metterlo negli scopes.

### Ok al deploy: SERVE
La v0.4.0 è pronta e smoke-testata in locale ma NON deployata su `brandart-mcp-gateway`.
Procedura: la stessa della v0.3.5. Dai l'ok e parte il deploy (azzera anche il contatore
del leak fastmcp noto, ~22MB/g).
