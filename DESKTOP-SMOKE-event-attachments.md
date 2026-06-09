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

## Passo 3 — scarica il contenuto (use case CEO)
```
Scarica il primo allegato di quell'evento con get_event_attachment e, se e' un biglietto aereo o un PDF, estrai il PNR e gli orari del volo.
```

Atteso:
- la call ritorna `content_base64` (contenuto inline, niente errori di filesystem),
- Claude decodifica e legge il contenuto,
- se e' un biglietto: PNR + orari estratti correttamente.

## Cosa conferma il PASS
- `list_event_attachments` ritorna metadata coerenti con l'allegato reale.
- `get_event_attachment`:
  - allegato **piccolo** (<=256 KB): ritorna `content_base64` valido, Claude lo legge inline.
  - allegato **grande** (>256 KB, es. PDF biglietto): NON ritorna il base64 (bloccherebbe
    il client). Lo carica su OneDrive in `Attachments/Events/` e ritorna
    `staged_to_onedrive: true` + `onedrive_file_id` + `web_url`. Claude puo' poi
    aprire il link o usare `get_file`. Questo e' il fix del blocco osservato in v0.2.9.
- Il contenuto e' leggibile (inline o via il file OneDrive).

> **Nota soglia (v0.2.10)**: la soglia inline e' 256 KB. Per forzare l'inline anche
> su file grandi (sconsigliato via Desktop): passare `max_inline_size` piu' alto.

## Se fallisce
- Errore auth/401 → ri-autentica il connettore (Redis dovrebbe persistere lo state,
  ma un re-OAuth risolve).
- "Attachment content not available" → l'allegato e' di tipo reference (link, non
  fileAttachment). Graph non espone `contentBytes` per quelli; e' un limite noto
  dell'API, non un bug del connettore. Prova con un allegato file vero.
- Tool non presente nella lista → il deploy non e' v0.2.9; rifai il redeploy.
