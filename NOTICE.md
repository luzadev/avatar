# Attribuzioni

L'interfaccia grafica di AvatarPy (file `ui.py`, cartella `core/` con avatar, mesh del volto,
visemi, dispositivi audio, hotkey, parola di attivazione, conferme, e la cartella `memory/`)
proviene da **Mark LIV — JARVIS**, Copyright (c) 2026 FatihMakes,
<https://github.com/FatihMakes/Mark-LIV>, rilasciato con licenza
Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0).
Il testo completo è in `LICENSE-MARK-LIV.txt`. Le funzioni di analisi audio in
`avatar/lipsync.py` sono adattate da `main.py` dello stesso progetto.

Modifiche rispetto all'originale: rimossa la dipendenza da Gemini, aggiunti i pulsanti
"Motore e Voce" e "Nuova conversazione", cambiato l'elenco delle voci nel pannello Customise,
caratteri ingranditi, aggiunta la vista avatar 3D. Su richiesta dell'utente i riferimenti a
"MARK LIV" e "FatihMakes" sono stati tolti dall'interfaccia (23 settembre 2026): l'attribuzione
richiesta dalla licenza resta in questo file e nel README.

**Questa licenza vieta l'uso commerciale** dell'app nel suo insieme finché contiene questi file.

Il modello del volto (`core/face_model.obj`) deriva dal canonical face model di MediaPipe
(Apache 2.0). Kokoro-82M è di hexgrad (Apache 2.0). Whisper è di OpenAI (MIT), nella
versione MLX di mlx-community.

Il resto del codice (cartella `avatar/`, `main.py`) è opera di questo progetto.
