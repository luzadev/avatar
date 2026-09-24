"""Finestra impostazioni: motore, chiavi, voce e riconoscimento vocale."""
from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget, QDoubleSpinBox)

from .engines.claude_code import check_claude_code
from .engines.openai_compat import list_models
from .settings import Settings
from .tts import KOKORO_VOICES, SystemVoice

STYLE = """
QDialog, QScrollArea, QScrollArea > QWidget > QWidget { background: #030a10; color: #cfe8ff; }
QLabel { color: #8fb8d8; font-family: 'Menlo'; font-size: 14px; }
QLineEdit, QComboBox, QDoubleSpinBox { background: #000d12; color: #e6f4ff; border: 1px solid #12354a; border-radius: 3px; padding: 4px 6px; font-family: 'Menlo'; font-size: 14px; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #3fd0ff; }
QPushButton { background: #05202c; color: #9fdfff; border: 1px solid #12506a; border-radius: 3px; padding: 5px 12px; font-family: 'Menlo'; font-size: 14px; }
QPushButton:hover { border-color: #3fd0ff; color: #ffffff; }
QPushButton#primary { background: #0a4a66; color: #ffffff; }
"""

PROVIDERS = [("anthropic", "Claude (Anthropic, cloud)"), ("local", "Server locale compatibile OpenAI (vLLM, Ollama…)"),
             ("claudecode", "Claude Code (il tuo accesso, nessuna chiave)")]
EFFORTS = [("low", "Veloce"), ("medium", "Bilanciata"), ("high", "Approfondita")]
ACCESS = [("chat", "Solo conversazione e ricerca web"), ("read", "Leggere file e cercare nelle cartelle"),
          ("full", "Completo: modifica file ed esegue comandi senza chiedere")]
CC_MODELS = [("sonnet", "Sonnet (veloce, consigliato)"), ("opus", "Opus (più capace)"), ("haiku", "Haiku (il più rapido)")]
STT_MODELS = [("mlx-community/whisper-base-mlx", "Whisper base (leggero)"), ("mlx-community/whisper-small-mlx", "Whisper small (consigliato)"),
              ("mlx-community/whisper-large-v3-turbo", "Whisper large v3 turbo (preciso, pesante)")]


def _combo(items, current) -> QComboBox:
    c = QComboBox()
    for value, label in items:
        c.addItem(label, value)
    idx = c.findData(current)
    c.setCurrentIndex(idx if idx >= 0 else 0)
    return c


class SettingsDialog(QDialog):
    _async = pyqtSignal(str, object)

    def __init__(self, settings: Settings, on_saved, parent=None) -> None:
        super().__init__(parent)
        self.settings, self.on_saved = settings, on_saved
        self.setWindowTitle("Motore & Voce")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(720)
        s = settings
        # Contenuto scorrevole; i pulsanti Salva/Annulla restano fissi in basso.
        content = QWidget()
        root = QVBoxLayout(content)

        form = QFormLayout()
        self.provider = _combo(PROVIDERS, s.get("provider"))
        form.addRow("Motore", self.provider)
        self.effort = _combo(EFFORTS, s.get("effort"))
        form.addRow("Profondità di ragionamento (Claude e Claude Code)", self.effort)
        root.addLayout(form)

        self.stack = QStackedWidget()
        # Anthropic
        w = QWidget(); f = QFormLayout(w)
        self.api_key = QLineEdit(); self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("•••••• (salvata, lascia vuoto per non cambiarla)" if s.get_secret("anthropic_api_key") else "sk-ant-…")
        f.addRow("Chiave API Anthropic", self.api_key)
        f.addRow("", QLabel("Creala su console.anthropic.com. Viene salvata nel portachiavi di macOS."))
        self.stack.addWidget(w)
        # Locale
        w = QWidget(); f = QFormLayout(w)
        self.local_url = QLineEdit(s.get("local_base_url")); f.addRow("Indirizzo del server", self.local_url)
        self.local_key = QLineEdit(); self.local_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.local_key.setPlaceholderText("opzionale"); f.addRow("Chiave API del server", self.local_key)
        row = QHBoxLayout(); self.local_model = QComboBox(); self.local_model.setEditable(True)
        self.local_model.setEditText(s.get("local_model") or ""); row.addWidget(self.local_model, 1)
        b = QPushButton("Rileva"); b.clicked.connect(self._detect_models); row.addWidget(b)
        f.addRow("Modello", row)
        self.local_hint = QLabel("Premi \"Rileva\" per leggere i modelli dal server."); f.addRow("", self.local_hint)
        self.search_key = QLineEdit(s.get("search_api_key")); self.search_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.search_key.setPlaceholderText("opzionale: senza chiave la ricerca web resta spenta")
        f.addRow("Chiave Brave Search", self.search_key)
        self.stack.addWidget(w)
        # Claude Code
        w = QWidget(); f = QFormLayout(w)
        self.cc_model = _combo(CC_MODELS, s.get("claudecode_model")); f.addRow("Modello", self.cc_model)
        self.cc_access = _combo(ACCESS, s.get("claudecode_access")); f.addRow("Cosa può fare sul Mac", self.cc_access)
        row = QHBoxLayout(); self.cc_config = QComboBox(); self.cc_config.setEditable(True)
        self.cc_config.setEditText(s.get("claudecode_config_dir") or ""); row.addWidget(self.cc_config, 1)
        b = QPushButton("Verifica"); b.clicked.connect(self._check_cc); row.addWidget(b)
        f.addRow("Profilo (cartella di configurazione)", row)
        self.cc_hint = QLabel(""); self.cc_hint.setWordWrap(True); f.addRow("", self.cc_hint)
        self.cc_path = QLineEdit(s.get("claudecode_path")); self.cc_path.setPlaceholderText("vuoto = automatico")
        f.addRow("Percorso del comando claude", self.cc_path)
        self.stack.addWidget(w)
        root.addWidget(self.stack)
        self.provider.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.stack.setCurrentIndex(self.provider.currentIndex())

        form2 = QFormLayout()
        self.tts_engine = _combo([("kokoro", "Kokoro, voce neurale in locale"), ("voicebox", "Voicebox: Qwen3-TTS in locale, espressiva, voce clonata"), ("elevenlabs", "ElevenLabs, espressiva nel cloud (chiave API)"), ("chatterbox", "Chatterbox, espressiva in locale (lenta)"), ("system", "Voce di sistema (macOS)")], s.get("tts_engine"))
        form2.addRow("Motore voce", self.tts_engine)
        self.kokoro_voice = _combo(list(KOKORO_VOICES.items()), s.get("kokoro_voice")); form2.addRow("Voce Kokoro", self.kokoro_voice)
        self.system_voice = _combo([("", "Automatica (Alice)")] + [(v, v) for v in SystemVoice.list_voices()], s.get("system_voice"))
        form2.addRow("Voce di sistema", self.system_voice)
        row = QHBoxLayout()
        self.cb_exag = QDoubleSpinBox(); self.cb_exag.setRange(0.2, 1.0); self.cb_exag.setSingleStep(0.1); self.cb_exag.setValue(float(s.get("chatterbox_exaggeration", 0.6))); row.addWidget(QLabel("enfasi")); row.addWidget(self.cb_exag)
        self.cb_cfg = QDoubleSpinBox(); self.cb_cfg.setRange(0.0, 1.0); self.cb_cfg.setSingleStep(0.1); self.cb_cfg.setValue(float(s.get("chatterbox_cfg", 0.3))); row.addWidget(QLabel("aderenza (0 = più veloce)")); row.addWidget(self.cb_cfg)
        form2.addRow("Chatterbox", row)
        row = QHBoxLayout()
        cur_ref = s.get("chatterbox_ref") or ""
        self.cb_voice = _combo([("", "Predefinita del modello"), ("preset:femminile", "Femminile (timbro di Sara)"), ("preset:maschile", "Maschile (timbro di Nicola)"), ("custom", "Personalizzata: file wav")],
                               cur_ref if cur_ref in ("", "preset:femminile", "preset:maschile") else "custom")
        row.addWidget(QLabel("voce")); row.addWidget(self.cb_voice)
        self.cb_ref = QLineEdit(cur_ref if cur_ref.startswith("/") or cur_ref.startswith("~") else ""); self.cb_ref.setPlaceholderText("percorso di un wav di 10 secondi con la voce da imitare"); row.addWidget(self.cb_ref, 1)
        form2.addRow("Voce Chatterbox", row)
        row = QHBoxLayout()
        self.vb_profile = QComboBox(); self.vb_profile.setMinimumWidth(220)
        self._vb_current = str(s.get("voicebox_profile_id") or "")
        self.vb_profile.addItem("(carica i profili da Voicebox)" if not self._vb_current else f"profilo salvato: {self._vb_current[:8]}…", self._vb_current)
        row.addWidget(self.vb_profile, 1)
        b = QPushButton("Carica profili"); b.clicked.connect(self._vb_profiles); row.addWidget(b)
        self.vb_engine = _combo([("qwen", "Qwen3-TTS (consigliato)"), ("kokoro", "Kokoro"), ("chatterbox_turbo", "Chatterbox Turbo"), ("chatterbox", "Chatterbox"), ("luxtts", "LuxTTS")], s.get("voicebox_engine")); row.addWidget(self.vb_engine)
        form2.addRow("Voicebox", row)
        self.vb_instruct = QLineEdit(s.get("voicebox_instruct") or ""); self.vb_instruct.setPlaceholderText("istruzione di stile per Qwen, es. «parla in modo caloroso e calmo» (facoltativa)")
        form2.addRow("", self.vb_instruct)
        row = QHBoxLayout()
        self.el_key = QLineEdit(); self.el_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.el_key.setPlaceholderText("•••••• (salvata)" if s.get_secret("elevenlabs_api_key") else "chiave API da elevenlabs.io"); row.addWidget(self.el_key, 1)
        self.el_voice = QComboBox(); self.el_voice.setMinimumWidth(220); row.addWidget(self.el_voice, 1)
        b = QPushButton("Carica voci"); b.clicked.connect(self._el_voices); row.addWidget(b)
        form2.addRow("ElevenLabs", row)
        row = QHBoxLayout()
        self.el_model = _combo([("eleven_flash_v2_5", "Flash v2.5 (rapido)"), ("eleven_multilingual_v2", "Multilingual v2 (qualità)"), ("eleven_v3", "v3 (più espressivo, sperimentale)")], s.get("elevenlabs_model")); row.addWidget(QLabel("modello")); row.addWidget(self.el_model)
        self.el_stab = QDoubleSpinBox(); self.el_stab.setRange(0.0, 1.0); self.el_stab.setSingleStep(0.05); self.el_stab.setValue(float(s.get("elevenlabs_stability", 0.45))); row.addWidget(QLabel("stabilità")); row.addWidget(self.el_stab)
        self.el_style = QDoubleSpinBox(); self.el_style.setRange(0.0, 1.0); self.el_style.setSingleStep(0.05); self.el_style.setValue(float(s.get("elevenlabs_style", 0.3))); row.addWidget(QLabel("stile")); row.addWidget(self.el_style)
        row.addStretch()
        form2.addRow("", row)
        self._el_current = str(s.get("elevenlabs_voice_id") or "")
        self.el_voice.addItem("(carica le voci con la chiave)" if not self._el_current else f"voce salvata: {self._el_current[:10]}…", self._el_current)
        self.stt_model = _combo(STT_MODELS, s.get("stt_model")); form2.addRow("Riconoscimento vocale", self.stt_model)
        from .avatar3d import list_models
        models = list_models()
        self.avatar_model = _combo([(m, m.replace("_", " ").title()) for m in models] or [("", "nessun modello")], s.get("avatar_model"))
        form2.addRow("Avatar 3D (file in avatar3d/models)", self.avatar_model)
        self.vad = QDoubleSpinBox(); self.vad.setRange(0.02, 0.5); self.vad.setSingleStep(0.01); self.vad.setValue(float(s.get("vad_threshold", 0.08)))
        form2.addRow("Sensibilità microfono (più basso = più sensibile)", self.vad)
        root.addLayout(form2)

        # ── Telegram ──────────────────────────────────────────────────────
        form3 = QFormLayout()
        form3.addRow(QLabel("Telegram (account personale): credenziali da my.telegram.org > API development tools"))
        row = QHBoxLayout()
        self.tg_id = QLineEdit(str(s.get("telegram_api_id") or "")); self.tg_id.setPlaceholderText("api id"); row.addWidget(self.tg_id)
        self.tg_hash = QLineEdit(); self.tg_hash.setEchoMode(QLineEdit.EchoMode.Password)
        self.tg_hash.setPlaceholderText("•••••• (salvato)" if s.get_secret("telegram_api_hash") else "api hash"); row.addWidget(self.tg_hash, 1)
        form3.addRow("Credenziali", row)
        row = QHBoxLayout()
        self.tg_phone = QLineEdit(); self.tg_phone.setPlaceholderText("+39…"); row.addWidget(self.tg_phone)
        b = QPushButton("Invia codice"); b.clicked.connect(self._tg_send_code); row.addWidget(b)
        self.tg_code = QLineEdit(); self.tg_code.setPlaceholderText("codice"); row.addWidget(self.tg_code)
        self.tg_pwd = QLineEdit(); self.tg_pwd.setEchoMode(QLineEdit.EchoMode.Password); self.tg_pwd.setPlaceholderText("password 2FA (se attiva)"); row.addWidget(self.tg_pwd)
        b = QPushButton("Accedi"); b.clicked.connect(self._tg_sign_in); row.addWidget(b)
        b = QPushButton("Esci"); b.clicked.connect(self._tg_logout); row.addWidget(b)
        form3.addRow("Accesso", row)
        row = QHBoxLayout()
        b = QPushButton("Accesso con QR (senza codice)"); b.clicked.connect(self._tg_qr); row.addWidget(b)
        self.tg_qr_label = QLabel(); self.tg_qr_label.setFixedSize(220, 220); self.tg_qr_label.setScaledContents(True); row.addWidget(self.tg_qr_label); row.addStretch()
        form3.addRow("Alternativa", row)
        self.tg_hint = QLabel("…"); self.tg_hint.setWordWrap(True); form3.addRow("", self.tg_hint)
        root.addLayout(form3)
        threading.Thread(target=lambda: self._async.emit("tg", self._tg_status()), daemon=True).start()

        # ── WhatsApp (dispositivo collegato, Baileys) ─────────────────────
        form4 = QFormLayout()
        form4.addRow(QLabel("WhatsApp in tempo reale (client non ufficiale: possibile blocco del numero, a tuo rischio)"))
        row = QHBoxLayout()
        self.wa_live = QCheckBox("Attivo all'avvio"); self.wa_live.setChecked(bool(s.get("whatsapp_live"))); row.addWidget(self.wa_live)
        self.wa_annuncia = QCheckBox("Annuncia a voce i messaggi in arrivo"); self.wa_annuncia.setChecked(bool(s.get("whatsapp_annuncia"))); row.addWidget(self.wa_annuncia)
        b = QPushButton("Collega (QR)"); b.clicked.connect(self._wa_link); row.addWidget(b)
        b = QPushButton("Scollega"); b.clicked.connect(self._wa_logout); row.addWidget(b)
        row.addStretch()
        form4.addRow("Stato", row)
        row = QHBoxLayout()
        self.wa_qr_label = QLabel(); self.wa_qr_label.setFixedSize(220, 220); self.wa_qr_label.setScaledContents(True); row.addWidget(self.wa_qr_label)
        self.wa_hint = QLabel("…"); self.wa_hint.setWordWrap(True); row.addWidget(self.wa_hint, 1)
        form4.addRow("", row)
        root.addLayout(form4)
        threading.Thread(target=lambda: self._async.emit("wa", (self._wa_status(), None)), daemon=True).start()

        # ── Monitor avvisi ────────────────────────────────────────────────
        form5 = QFormLayout()
        row = QHBoxLayout()
        self.mon_on = QCheckBox("Controlla Mail, WhatsApp e Telegram"); self.mon_on.setChecked(bool(s.get("monitor_enabled"))); row.addWidget(self.mon_on)
        self.mon_say = QCheckBox("Annuncia a voce gli avvisi"); self.mon_say.setChecked(bool(s.get("monitor_annuncia"))); row.addWidget(self.mon_say)
        self.mon_int = _combo([(30, "ogni 30 s"), (60, "ogni minuto"), (300, "ogni 5 minuti"), (900, "ogni 15 minuti")], int(s.get("monitor_intervallo") or 60)); row.addWidget(self.mon_int)
        row.addStretch()
        form5.addRow("Monitor", row)
        from .monitor import DEFAULT_RULES
        self.mon_rules = QLineEdit(s.get("monitor_regole") or ""); self.mon_rules.setPlaceholderText(DEFAULT_RULES[:110] + "…")
        form5.addRow("Cosa merita un avviso", self.mon_rules)
        self.mon_excl = QLineEdit(s.get("monitor_escludi") or ""); self.mon_excl.setPlaceholderText("es. newsletter, offerta, gruppo Calcetto, Amazon  — parole separate da virgola, cercate in mittente, chat e testo")
        form5.addRow("Escludi dagli avvisi", self.mon_excl)
        root.addLayout(form5)

        btns = QHBoxLayout(); btns.addStretch()
        cancel = QPushButton("Annulla"); cancel.clicked.connect(self.reject); btns.addWidget(cancel)
        save = QPushButton("Salva"); save.setObjectName("primary"); save.clicked.connect(self._save); btns.addWidget(save)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(content); scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self); outer.setContentsMargins(10, 10, 10, 10)
        outer.addWidget(scroll, 1)
        outer.addLayout(btns)
        screen = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        h = int(screen.height() * 0.85) if screen else 800
        self.resize(820, min(900, h))
        self._async.connect(self._on_async)
        self._check_cc()

    # ── Azioni ────────────────────────────────────────────────────────────
    def _detect_models(self) -> None:
        url, key = self.local_url.text().strip().rstrip("/"), self.local_key.text().strip() or self.settings.get_secret("local_api_key")
        self.local_hint.setText("Interrogo il server…")
        def work():
            try:
                self._async.emit("models", list_models(url, key))
            except Exception as err:
                self._async.emit("models_error", str(err)[:160])
        threading.Thread(target=work, daemon=True).start()

    def _check_cc(self) -> None:
        self.cc_hint.setText("Verifico…")
        path, cfg = self.cc_path.text().strip(), self.cc_config.currentText().strip()
        threading.Thread(target=lambda: self._async.emit("cc", check_claude_code(path, cfg)), daemon=True).start()

    # ── Telegram ──────────────────────────────────────────────────────────
    def _tg_save_creds(self) -> None:
        vals = {"telegram_api_id": self.tg_id.text().strip()}
        if self.tg_hash.text().strip():
            vals["telegram_api_hash"] = self.tg_hash.text().strip()
        self.settings.update(vals)

    def _tg_status(self) -> str:
        try:
            from .telegram_client import status
            return status()
        except Exception as err:
            return f"Errore: {err}"

    def _tg_send_code(self) -> None:
        self._tg_save_creds()
        phone = self.tg_phone.text().strip()
        self.tg_hint.setText("Invio il codice…")
        def work():
            try:
                from .telegram_client import send_code
                self._async.emit("tg", send_code(phone))
            except Exception as err:
                self._async.emit("tg", f"Errore: {err}")
        threading.Thread(target=work, daemon=True).start()

    def _tg_sign_in(self) -> None:
        self._tg_save_creds()
        code, pwd = self.tg_code.text().strip(), self.tg_pwd.text()
        self.tg_hint.setText("Accedo…")
        def work():
            try:
                from .telegram_client import sign_in
                self._async.emit("tg", sign_in(code, pwd))
            except Exception as err:
                self._async.emit("tg", f"Errore: {err}")
        threading.Thread(target=work, daemon=True).start()

    def _tg_qr(self) -> None:
        self._tg_save_creds()
        self.tg_hint.setText("Genero il codice QR…")
        def work():
            try:
                from .telegram_client import qr_login, _qr_state
                _qr_state["password"] = self.tg_pwd.text()
                qr_login(lambda msg, png: self._async.emit("tg_qr", (msg, png)))
            except Exception as err:
                self._async.emit("tg", f"Errore: {err}")
        threading.Thread(target=work, daemon=True).start()

    def _tg_logout(self) -> None:
        def work():
            try:
                from .telegram_client import logout
                self._async.emit("tg", logout())
            except Exception as err:
                self._async.emit("tg", f"Errore: {err}")
        threading.Thread(target=work, daemon=True).start()

    # ── WhatsApp ──────────────────────────────────────────────────────────
    def _wa_status(self) -> str:
        try:
            from . import whatsapp_bridge as wb
            return wb.status_text()
        except Exception as err:
            return f"Errore: {err}"

    def _wa_link(self) -> None:
        self.wa_hint.setText("Avvio il ponte e genero il QR…")
        def work():
            try:
                from . import whatsapp_bridge as wb
                import time as _t
                msg = wb.start()
                self._async.emit("wa", (msg, None))
                for _ in range(120):   # fino a 2 minuti: aggiorna QR e stato
                    _t.sleep(1)
                    st = wb.status()
                    if st.get("connection") == "open":
                        self._async.emit("wa", (wb.status_text() + " Sul telefono: WhatsApp › Dispositivi collegati.", None)); return
                    if st.get("qr"):
                        self._async.emit("wa", ("Inquadra il QR dal telefono: WhatsApp › Impostazioni › Dispositivi collegati › Collega un dispositivo.", wb.qr_png()))
                self._async.emit("wa", ("Tempo scaduto: premi di nuovo «Collega (QR)».", None))
            except Exception as err:
                self._async.emit("wa", (f"Errore: {err}", None))
        threading.Thread(target=work, daemon=True).start()

    def _wa_logout(self) -> None:
        def work():
            try:
                from . import whatsapp_bridge as wb
                self._async.emit("wa", (wb.logout(), None))
            except Exception as err:
                self._async.emit("wa", (f"Errore: {err}", None))
        threading.Thread(target=work, daemon=True).start()

    def _vb_profiles(self) -> None:
        def work():
            try:
                from .tts import VoiceboxVoice
                self._async.emit("vb", VoiceboxVoice.list_profiles())
            except Exception as err:
                self._async.emit("vb_error", str(err)[:120])
        threading.Thread(target=work, daemon=True).start()

    def _el_voices(self) -> None:
        key = self.el_key.text().strip() or self.settings.get_secret("elevenlabs_api_key")
        if not key:
            self.el_voice.clear(); self.el_voice.addItem("inserisci prima la chiave API", ""); return
        if self.el_key.text().strip():
            self.settings.update({"elevenlabs_api_key": key})
        def work():
            try:
                from .tts import ElevenLabsVoice
                self._async.emit("el", ElevenLabsVoice.list_voices(key))
            except Exception as err:
                self._async.emit("el_error", str(err)[:120])
        threading.Thread(target=work, daemon=True).start()

    def _on_async(self, kind: str, payload) -> None:
        if kind == "vb":
            self.vb_profile.clear()
            for pid, label in payload:
                self.vb_profile.addItem(label, pid)
            idx = self.vb_profile.findData(self._vb_current)
            self.vb_profile.setCurrentIndex(idx if idx >= 0 else 0)
            return
        if kind == "vb_error":
            self.vb_profile.clear(); self.vb_profile.addItem(f"errore: {payload}", ""); return
        if kind == "el":
            self.el_voice.clear()
            for vid, label in payload:
                self.el_voice.addItem(label, vid)
            idx = self.el_voice.findData(self._el_current)
            self.el_voice.setCurrentIndex(idx if idx >= 0 else 0)
            return
        if kind == "el_error":
            self.el_voice.clear(); self.el_voice.addItem(f"errore: {payload}", ""); return
        if kind == "wa":
            msg, png = payload
            self.wa_hint.setText(str(msg))
            if png:
                from PyQt6.QtGui import QPixmap
                pm = QPixmap(); pm.loadFromData(png); self.wa_qr_label.setPixmap(pm)
            else:
                self.wa_qr_label.clear()
            return
        if kind == "tg":
            self.tg_hint.setText(str(payload))
            return
        if kind == "tg_qr":
            msg, png = payload
            self.tg_hint.setText(str(msg))
            if png:
                from PyQt6.QtGui import QPixmap
                pm = QPixmap(); pm.loadFromData(png); self.tg_qr_label.setPixmap(pm)
            else:
                self.tg_qr_label.clear()
            return
        if kind == "models":
            current = self.local_model.currentText().strip()
            self.local_model.clear(); self.local_model.addItems(payload)
            if current in payload:
                self.local_model.setCurrentText(current)
            self.local_hint.setText(f"Trovati {len(payload)} modelli: {', '.join(payload)}" if payload else "Il server risponde ma non espone modelli.")
        elif kind == "models_error":
            self.local_hint.setText(f"Server non raggiungibile: {payload}")
        elif kind == "cc":
            st = payload
            current = self.cc_config.currentText().strip()
            self.cc_config.clear(); self.cc_config.addItems(st.get("profiles", [])); self.cc_config.setEditText(current)
            if not st.get("binary"):
                self.cc_hint.setText("Comando claude non trovato. Installa Claude Code o indica il percorso.")
            elif st.get("error"):
                self.cc_hint.setText(f"Profilo {st['configDir']}: {st['error']}")
            elif st.get("loggedIn"):
                self.cc_hint.setText(f"Profilo {st['configDir']}: collegato come {st.get('email') or 'account sconosciuto'}. "
                                     f"Con un errore 401 esegui: CLAUDE_CONFIG_DIR={st['configDir']} claude auth login")
            else:
                self.cc_hint.setText(f"Profilo {st['configDir']}: nessun accesso. Esegui: CLAUDE_CONFIG_DIR={st['configDir']} claude auth login")

    def _save(self) -> None:
        values = {
            "provider": self.provider.currentData(), "effort": self.effort.currentData(),
            "local_base_url": self.local_url.text().strip().rstrip("/"), "local_model": self.local_model.currentText().strip(),
            "search_api_key": self.search_key.text().strip(),
            "claudecode_model": self.cc_model.currentData(), "claudecode_access": self.cc_access.currentData(),
            "claudecode_config_dir": self.cc_config.currentText().strip(), "claudecode_path": self.cc_path.text().strip(),
            "tts_engine": self.tts_engine.currentData(), "kokoro_voice": self.kokoro_voice.currentData(),
            "system_voice": self.system_voice.currentData(), "stt_model": self.stt_model.currentData(),
            "voicebox_profile_id": self.vb_profile.currentData() or "", "voicebox_engine": self.vb_engine.currentData(), "voicebox_instruct": self.vb_instruct.text().strip(),
            "elevenlabs_voice_id": self.el_voice.currentData() or "", "elevenlabs_model": self.el_model.currentData(),
            "elevenlabs_stability": float(self.el_stab.value()), "elevenlabs_style": float(self.el_style.value()),
            "chatterbox_exaggeration": float(self.cb_exag.value()), "chatterbox_cfg": float(self.cb_cfg.value()),
            "chatterbox_ref": (self.cb_ref.text().strip() if self.cb_voice.currentData() == "custom" else self.cb_voice.currentData()),
            "vad_threshold": float(self.vad.value()),
            "avatar_model": self.avatar_model.currentData() or "",
            "telegram_api_id": self.tg_id.text().strip(),
            "whatsapp_live": self.wa_live.isChecked(),
            "whatsapp_annuncia": self.wa_annuncia.isChecked(),
            "monitor_enabled": self.mon_on.isChecked(),
            "monitor_annuncia": self.mon_say.isChecked(),
            "monitor_intervallo": int(self.mon_int.currentData() or 60),
            "monitor_regole": self.mon_rules.text().strip(),
            "monitor_escludi": self.mon_excl.text().strip(),
        }
        if self.tg_hash.text().strip():
            values["telegram_api_hash"] = self.tg_hash.text().strip()
        if self.api_key.text().strip():
            values["anthropic_api_key"] = self.api_key.text().strip()
        if self.local_key.text().strip():
            values["local_api_key"] = self.local_key.text().strip()
        if self.el_key.text().strip():
            values["elevenlabs_api_key"] = self.el_key.text().strip()
        self.settings.update(values)
        self.accept()
        if self.on_saved:
            self.on_saved()
