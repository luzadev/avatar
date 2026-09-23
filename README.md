# AvatarPy — assistente personale con volto, in Python

Riscrittura in Python dell'assistente "Avatar": stessa idea (un assistente che ascolta, parla, ricorda e cerca sul web) con l'interfaccia HUD di **Mark LIV**: un volto tridimensionale renderizzato in software, con sincronizzazione labiale reale, forma d'onda, pannello di log e impostazioni.

L'interfaccia è riusata con licenza CC BY-NC 4.0 (uso personale, non commerciale): vedi `NOTICE.md`.

## Cosa fa

- **Conversazione a mani libere**: il microfono è sempre in ascolto; quando smetti di parlare, la frase viene trascrizione in locale (Whisper via MLX) e inviata al motore. In alternativa: premi-per-parlare, parola di attivazione "Hey Jarvis" (openwakeword, installabile dal pannello), o il campo di testo.
- **Voce**: Kokoro in locale con le voci italiane Sara e Nicola (PyTorch), oppure la voce di sistema di macOS. Le frasi vengono sintetizzate in anticipo mentre l'app pronuncia la precedente.
- **Volto**: la bocca segue lo spettro dell'audio (50 forme al secondo) fuso con il testo pronunciato; lo sguardo e le sopracciglia seguono lo stato (ascolta, pensa, parla, dorme).
- **Tre motori**, selezionabili dal pulsante "Motore & Voce":
  - *Claude (Anthropic)*: Claude Opus 5 via API, con ricerca web server-side. Serve una chiave, salvata nel portachiavi di macOS.
  - *Server locale compatibile OpenAI*: vLLM, Ollama, LM Studio. Ricerca web con una chiave Brave Search (opzionale).
  - *Claude Code*: usa Claude Code installato sul Mac e il suo accesso, senza chiave. Tre livelli: solo conversazione, lettura dei file, completo.
- **Memoria a lungo termine** per categorie (identità, preferenze, progetti, relazioni, desideri, note) nel file `memory/long_term.json`, condivisa da tutti i motori e visibile dal pulsante "Memory" del pannello. Con Claude Code la memoria passa da un piccolo server MCP incluso.

## Avatar 3D personale

Il pulsante "HUD" nel pannello alterna tre viste: volto animato, nucleo, **avatar 3D**. La terza mostra un modello GLB (`avatar3d/models/avatar.glb`) renderizzato con three.js dentro l'interfaccia: inquadratura sul busto (clic per la figura intera), animazione di riposo, testa e occhi che guardano la camera e seguono lo stato (ascolta, pensa, parla, dorme), sbattito delle palpebre, forma d'onda e stato in basso.

La **sincronizzazione labiale** usa i blend shape del modello: visemi Oculus (`viseme_aa`, `viseme_O`, `viseme_I`…) e ARKit (`jawOpen`, `mouthSmileLeft`, `mouthFunnel`, `eyeBlinkLeft`…), pilotati dalle forme della bocca calcolate dall'audio. I modelli inclusi sono avatar Avaturn esportati con i blend shape e le ossa degli occhi: `allegra_2.glb`, `allegra.glb` e `luza.glb`. Si sceglie dal menu "Avatar 3D" in "Motore e Voce", che elenca i file GLB presenti in `avatar3d/models`; il cambio è immediato. Per aggiungere un avatar basta copiare il suo GLB in quella cartella: il nome del file diventa il nome nel menu.

Per usare un altro modello sostituisci il GLB: vanno bene avatar Avaturn, Ready Player Me o Mixamo con scheletro standard (ossa `Head`, `Neck`, `Hips`, opzionali `LeftEye`/`RightEye`). Senza blend shape la bocca resta ferma e si muove solo la testa.

## Allegati

Trascina un file nella zona "File upload" (o clicca per sceglierlo) e poi fai la domanda: il file viene passato al motore con il messaggio successivo, una volta sola.

- **Testo, PDF, Word, RTF**: viene allegato il testo estratto (fino a 8000 caratteri).
- **Immagini** (PNG, JPEG, HEIC…): il testo presente viene riconosciuto in locale con l'OCR di macOS, quindi funziona con tutti i motori, anche quello locale. Con Claude API e Claude Code l'immagine viene anche vista davvero, per descrizioni e domande sul contenuto.

## Importare le memorie da ChatGPT (o da un altro assistente)

1. In ChatGPT chiedi: "Elenca tutte le memorie che hai salvato su di me, una per riga" e copia la risposta in un file di testo (oppure copia l'elenco da Impostazioni › Personalizzazione › Gestisci memorie).
2. Nell'app trascina il file nella zona "File upload" e di': "importa queste memorie". L'assistente le salva nella categoria giusta con lo strumento `salva_memorie`, in una volta sola.
3. Controlla il risultato dal pulsante "Memory" e cancella ciò che non vuoi tenere.

## Monitor con avvisi (Mail, WhatsApp, Telegram)

Attivabile in "Motore e Voce" › Monitor. Ogni minuto (intervallo regolabile) l'app raccoglie i messaggi nuovi da Mail (posta in arrivo), WhatsApp (ponte in tempo reale) e Telegram (account collegato), li passa a un modello veloce con le regole che scrivi tu, e per quelli che meritano attenzione crea un avviso: riga rossa nel log, notifica di macOS e, se vuoi, annuncio a voce. Le regole predefinite segnalano richieste di aiuto o assistenza, domande che aspettano risposta, problemi, urgenze, scadenze, pagamenti e appuntamenti da confermare, e ignorano newsletter, promozioni e notifiche automatiche.

A voce: "ci sono avvisi?", "chiudi l'avviso di Marco", "controlla adesso". Il primo giro dopo l'attivazione prende solo la linea di base: vengono valutati i messaggi arrivati da quel momento in poi. Con il motore Claude Code la valutazione usa Haiku a basso sforzo; con Claude API usa Haiku 4.5; con il server locale usa il modello locale.

## Plugin: comandare il Mac a voce

I plugin sono file Python nella cartella `plugins/`: ognuno dichiara uno o più strumenti (nome, descrizione, parametri, funzione) e l'assistente li usa da tutti e tre i motori. Il pannello "Plugins" li elenca e permette di disattivarli.

Incluso: **`calendario`**, che legge, crea, sposta e cancella eventi del Calendario di macOS tramite EventKit. Esempi a voce: "che impegni ho domani", "sono libero venerdì pomeriggio", "segna dentista giovedì alle 15", "sposta la riunione di lunedì alle 11", "cancella il pranzo di mercoledì". La cancellazione chiede conferma sullo schermo (pulsante Confirm nel pannello); con Claude Code l'assistente chiede conferma a voce. Al primo uso macOS chiede il permesso per il Calendario.

Incluso anche **`mail`**, per l'app Mail di macOS: "ho email nuove?", "cerca le email di Marco", "leggimi l'ultima email di Amazon", "scrivi a mario@esempio.it che arrivo alle dieci". Elenco, ricerca e lettura usano l'indice locale di Mail (istantanei, anche a Mail chiusa); l'invio passa da Mail con conferma sullo schermo e richiede il permesso Automazione al primo uso.

Altri plugin inclusi, tutti locali:

| Plugin | Cosa fa | Esempi |
|---|---|---|
| `promemoria` | app Promemoria (EventKit) | "ricordami di chiamare Marco alle 17", "aggiungi latte alla spesa", "cosa devo fare oggi" |
| `note` | app Note | "prendi nota: …", "leggimi la nota sul progetto", "aggiungi alla nota Idee…" |
| `musica` | Apple Music (controlli anche su Spotify) | "metti la playlist Mattina", "pausa", "prossimo", "cosa sta suonando", "volume musica al 30" |
| `app` | apri/chiudi app, siti e file | "apri Safari sul Corriere", "avvia Xcode", "chiudi Mail", "che app sono aperte" |
| `mac` | volume, luminosità, Non disturbare, Wi-Fi, stato, blocco schermo, screenshot | "alza il volume", "spegni il Wi-Fi", "quanta batteria ho", "blocca il Mac" |
| `timer` | timer, sveglie, attività programmate anche giornaliere | "timer di 10 minuti per la pasta", "svegliami alle 7", "ogni mattina alle 8 leggimi la posta" |
| `meteo` | previsioni (Open-Meteo, senza chiave) | "che tempo fa domani a Milano" |
| `contatti` | Contatti | "qual è il numero di Anna", "l'email di Luca" |
| `messaggi` | iMessage con conferma | "manda un messaggio a Luca che arrivo alle 9" |
| `file` | Spotlight, cartelle, lettura txt/PDF/Word | "cerca il PDF del contratto", "cosa c'è sul Desktop", "riassumi il documento X" |
| `comandi_rapidi` | Comandi Rapidi, anche HomeKit | "accendi le luci del soggiorno" (se esiste il comando rapido) |
| `browser` | legge pagine, cerca su Google/YouTube/Maps/Amazon/Wikipedia | "leggimi questa pagina", "metti su YouTube i Pink Floyd" |
| `buongiorno` | riepilogo: impegni, promemoria, email, meteo | "buongiorno", "come si presenta la giornata" |
| `whatsapp_archivio` | chat WhatsApp esportate: importazione, ricerca full-text, lettura per periodo, statistiche | "cosa mi ha detto Marco sulla riunione?", "riassumi la chat con Anna dell'ultima settimana", "quando abbiamo parlato del dentista?" |
| `whatsapp_live` | WhatsApp in tempo reale come dispositivo collegato (Baileys): novità, invio con conferma, annunci vocali | "ci sono novità su WhatsApp?", "chi mi ha scritto?", "scrivi a Marco su WhatsApp che arrivo" |
| `telegram` | Telegram con il tuo account (Telethon): non letti, lettura, ricerca, invio con conferma | "ho messaggi su Telegram?", "leggimi la chat con Anna", "scrivi a Luca su Telegram che sono in ritardo" |

**WhatsApp in tempo reale** usa un client non ufficiale (Baileys, in `whatsapp_bridge/`, Node.js) collegato come "dispositivo collegato". È contro i termini di WhatsApp e Meta può bloccare il numero: l'app non invia mai senza conferma e non fa azioni di massa, ma il rischio resta a carico di chi lo attiva. Per collegarlo: "Motore e Voce" › WhatsApp › "Collega (QR)", poi sul telefono WhatsApp › Impostazioni › Dispositivi collegati › Collega un dispositivo. Da quel momento i messaggi in arrivo (e quelli che invii da altri dispositivi) finiscono nell'archivio, in tempo reale; con "Annuncia a voce" l'assistente li legge appena arrivano. Il ponte parte da solo all'avvio dell'app una volta collegato. Richiede Node.js (`npm install` in `whatsapp_bridge/` è già fatto).

Per lo storico, l'app lavora sulle chat **esportate**: in WhatsApp apri la chat › Altro › Esporta chat (senza media), poi di' all'assistente "importa la chat WhatsApp che è in Download" (o "importa tutte le chat della cartella X") oppure copia il file `.txt` o lo `.zip` in `data/whatsapp/`. Gli zip "WhatsApp Chat - Nome.zip" prendono il nome del contatto. L'indice è locale e istantaneo anche con decine di migliaia di messaggi; riesportando la chat, l'archivio si aggiorna.

Telegram richiede una configurazione una tantum in "Motore e Voce": api id e api hash creati su my.telegram.org (API development tools), poi il numero di telefono, "Invia codice", il codice ricevuto su Telegram e "Accedi" (più la password se hai la verifica in due passaggi). La sessione resta salvata in `data/telegram.session`; "Esci" la cancella. WhatsApp non ha un'API per account personali e non è supportato.

Le attività programmate (plugin `timer`) vengono eseguite dall'app quando è aperta: un avviso viene detto a voce, un comando viene eseguito come se lo avessi chiesto tu. Non disturbare richiede due Comandi Rapidi chiamati "Non disturbare ON/OFF". Ogni app controllata chiede il permesso Automazione la prima volta.

Per scriverne uno nuovo copia la struttura di `plugins/calendario.py`: una lista `TOOLS` con `name`, `description`, `parameters` (JSON Schema) e `run(parameters, ctx)`; `ctx` offre `say` (parlare), `log` e `confirm` (conferma a schermo per azioni irreversibili). Riavvia l'app dopo aver aggiunto il file. Funziona anche il formato a singolo `PLUGIN` di Mark-LIV.

## Requisiti

- macOS su Apple Silicon, Python 3.12, [uv](https://docs.astral.sh/uv/).
- `brew install espeak-ng portaudio` (fonetica italiana per Kokoro e audio).
- Permessi macOS richiesti al primo uso: Microfono, Calendario.
- Per il motore Claude Code: Claude Code installato e collegato.

## Avvio

```bash
cd AvatarPy
uv sync
uv run python main.py
```

Al primo avvio si apre la finestra "Motore & Voce" se manca la configurazione. La prima volta vengono scaricati i modelli di Kokoro (circa 330 MB) e di Whisper (circa 500 MB per "small"); poi tutto resta in locale.

## Uso

| Azione | Come |
|---|---|
| Parlare | Parla e basta: la frase parte quando fai una pausa di circa un secondo |
| Premi-per-parlare | Pulsante "Push-to-talk" nel cassetto rapido; il microfono si apre solo con il tasto premuto |
| Scrivere | Campo di testo in basso a destra, Invio per inviare |
| Interrompere | Esc, oppure il pulsante di interruzione |
| Silenziare il microfono | F4 |
| Vedere o cancellare la memoria | Pulsante "Memory" |
| Cambiare nome, colore e voce | Pulsante "Customise assistant" |
| Cambiare motore, chiavi, riconoscimento vocale | Pulsante "Motore & Voce" |
| Ricominciare da zero | Pulsante "Nuova conversazione" |

## Profili di Claude Code

Se sul Mac ci sono più profili (`~/.claude`, `~/.claude-<nome>`), scegli quello giusto in "Motore & Voce"; "Verifica" mostra l'account collegato. Un errore `401 OAuth access token is invalid` significa che l'accesso di quel profilo è scaduto: `CLAUDE_CONFIG_DIR=<cartella> claude auth login`.

## Struttura

```
main.py                    avvio, collegamento tra interfaccia e assistente
ui.py, core/, memory/      interfaccia e moduli da Mark LIV (vedi NOTICE.md)
avatar/
  assistant.py             orchestratore: microfono → Whisper → motore → voce, stati e log
  audio.py                 microfono con rilevamento della voce; riproduzione con visemi
  lipsync.py               livello audio e forme della bocca dallo spettro
  stt.py                   Whisper (MLX, ripiego su faster-whisper)
  tts.py                   Kokoro e voce di sistema, spezzettamento in frasi
  memory_tools.py          strumenti di memoria comuni ai motori
  memory_mcp.py            server MCP che espone la memoria a Claude Code
  websearch.py             ricerca Brave (motore locale)
  settings.py              impostazioni, chiavi nel portachiavi
  settings_dialog.py       finestra "Motore e Voce"
  avatar3d.py              vista 3D (QWebEngineView + server locale) sincronizzata con stati e visemi
  persona.md               personalità (modificabile)
  plugins.py               registro dei plugin (strumenti per i motori e per MCP)
  engines/                 anthropic_engine.py, openai_compat.py, claude_code.py
plugins/                   plugin (calendario.py incluso)
avatar3d/                  pagina three.js, modello GLB e librerie
config/                    api_keys.json (identità), settings.json (impostazioni)
data/                      cronologie delle conversazioni per motore
```

La personalità dell'assistente è in `avatar/persona.md`; il nome e il tuo nome si impostano dal pulsante "Customise assistant".
