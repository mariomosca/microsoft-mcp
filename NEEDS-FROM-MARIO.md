# NEEDS FROM MARIO

## v0.4.0 — SharePoint tools (27 Aug 2026)

### RISOLTO 27/08 13:50 — permesso aggiunto e consentito da Mario
`Files.Read.All` delegated aggiunto all'app gateway + admin consent tenant-wide,
verificato in `az ad app permission list-grants`. Nessuna azione residua.

### UPDATE 27/08 12:50 — REGRESSIONE: admin consent SERVE (correzione)
La conclusione sotto era SBAGLIATA: lo smoke test locale girava sull'app registration
stdio (`61682051-...`, che ha `Files.Read.All` + `Sites.Read.All`), mentre il gateway
usa l'app `Brandart MCP Gateway` (`4506b2a9-...`) che ha solo `Files.ReadWrite`
(OneDrive personale). Risultato: sp_* → 403 in produzione (segnalato da Leo).

Fix a minor impatto — aggiungere delegated `Files.Read.All` all'app gateway (copre
site drives E Search API driveItem; `Sites.Read.All` non necessario) + admin consent:

```bash
# 1. aggiungi il permesso (Files.Read.All delegated, id globale Graph)
az ad app permission add --id 4506b2a9-cdc5-4e7c-8e43-cc0307cb9f61 \
  --api 00000003-0000-0000-c000-000000000000 \
  --api-permissions df85f4d6-205c-4ac5-a5ea-6bf408dba283=Scope
# 2. admin consent tenant-wide
az ad app permission admin-consent --id 4506b2a9-cdc5-4e7c-8e43-cc0307cb9f61
```

Nessun redeploy necessario (l'OBO usa `.default` → il nuovo scope entra nei token
alla prossima emissione, al massimo entro la scadenza del token Graph corrente ~1h).

### ~~Admin consent: NON SERVE~~ (SUPERATO — vedi sopra)
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
