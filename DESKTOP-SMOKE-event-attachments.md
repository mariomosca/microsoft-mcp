# Smoke test live — allegati eventi calendario (Claude Desktop)

Verifica end-to-end dei due tool nuovi (`list_event_attachments`, `get_event_attachment`)
contro il deploy Azure, dal connettore **"Brandart Microsoft 365"**.

Questo e' l'UNICO path che esercita davvero l'auth OBO reale (il token Graph
e' iniettato per-request dal connettore — non testabile via `az containerapp exec`).

## Prerequisiti
- Deploy v0.2.9 attivo (vedi STATUS.md → Redeploy).
- Connettore "Brandart Microsoft 365" gia' configurato e autenticato in Claude Desktop.
- Un evento in calendario con un allegato. Se non ne hai: crea un appuntamento
  qualsiasi su Outlook e allega un PDF/PNG (es. un biglietto). Va bene anche
  passato o futuro entro ~60 giorni.

## Passo 1 — trova un evento con allegato
Prompt in Claude Desktop:

```
Elenca i miei eventi calendario delle prossime 2 settimane e di quelle passate 2 settimane. Per ognuno dimmi se ha allegati (hasAttachments). Dammi l'id dell'evento che ha almeno un allegato.
```

(Se conosci gia' l'evento, salta al passo 2.)

## Passo 2 — lista allegati dell'evento
```
Usa list_event_attachments su quell'evento e mostrami nome, content type e dimensione di ogni allegato.
```

Atteso: una o piu' righe con `name`, `content_type`, `size`, `is_inline` e un `id` per ciascun allegato.

## Passo 3a — LEGGERE il contenuto (use case CEO)
```
Leggi il primo allegato di quell'evento con read_event_attachment. Se e' un biglietto aereo estrai PNR e orari; se e' una scheda costi riassumi le voci.
```

Atteso:
- la call ritorna `kind` (xlsx/pdf/docx/csv/text) + `text` (contenuto estratto lato server),
- NESSUN `content_base64` nel risultato — il binario non entra mai nel context,
- Claude legge il testo e risponde (PNR/orari, oppure sintesi della scheda costi).

## Passo 3b — SCARICARE il file (opzionale)
```
Scarica quell'allegato con get_event_attachment e dammi il link.
```

Atteso: la call carica il file su OneDrive (`Attachments/Events/`) e ritorna
`onedrive_file_id` + `web_url`. Claude ti da' il link; i byte non passano dal context.

## Cosa conferma il PASS
- `list_event_attachments` ritorna metadata coerenti con l'allegato reale.
- `read_event_attachment` ritorna `text` leggibile (xlsx/pdf/docx/csv/txt), MAI base64.
  Per immagini/altri binari: `kind=unsupported` + nota che rimanda a get_event_attachment.
- `get_event_attachment` ritorna `web_url` OneDrive, MAI base64 inline.
- In NESSUN caso Claude resta a "scrivere/decodificare base64" (il blocco di v0.2.9/v0.2.10).

## Architettura (perche' cosi')
I byte di un allegato non devono MAI entrare nel context del modello: un blob base64
in un tool result satura il context e blocca il client (e comunque il modello non puo'
leggere xlsx/pdf dai byte grezzi). Best practice MCP: estrarre testo lato server
(`read_event_attachment`) per leggere, e ritornare un link (`get_event_attachment` →
OneDrive) per scaricare. Vedi commit/STATUS v0.2.11.

## Se fallisce
- Errore auth/401 → ri-autentica il connettore (Redis persiste lo state, ma un re-OAuth risolve).
- "reference attachment (link)" → l'allegato e' un link (referenceAttachment), non un file:
  Graph non espone bytes. Prova con un allegato file vero.
- `kind=unsupported` su un formato che ti aspettavi testuale → dimmelo, aggiungo il parser.
- Tool non presente → il deploy non e' v0.2.11; rifai il redeploy.
