"""Orchestratore: microfono → trascrizione → motore → voce, con stati e log sulla UI."""
from __future__ import annotations

import json
import queue
import threading
import time

import numpy as np

from memory import config_manager as cm

from .audio import Microphone, Player
from .engines.anthropic_engine import AnthropicEngine
from .engines.claude_code import ClaudeCodeEngine
from .engines.openai_compat import OpenAICompatEngine
from .engines.mlx_engine import MLXEngine
from .settings import CONFIG_DIR, Settings
from .stt import Transcriber
from .tts import SentenceSplitter, clean_for_speech, make_voice

END = object()
STATUS_LABELS = {
    "thinking": "THINKING", "searching": "PROCESSING", "memory": "PROCESSING",
    "working": "PROCESSING", "responding": "THINKING",
}


# Frasi che Whisper "inventa" su silenzio o rumore di fondo (sottotitoli visti in addestramento).
_HALLUCINATIONS = ("sottotitoli", "a cura di", "grazie per aver guardato", "iscriviti al canale",
                   "www.", "amara.org", "subtitles", "thank you for watching")


def _is_hallucination(text: str) -> bool:
    t = text.lower()
    return any(h in t for h in _HALLUCINATIONS)


def _identity() -> tuple[str, str]:
    try:
        d = json.loads((CONFIG_DIR / "api_keys.json").read_text(encoding="utf-8"))
        return (d.get("assistant_name") or "Ava").strip(), (d.get("user_name") or "").strip()
    except Exception:
        return "Ava", ""


class Assistant:
    def __init__(self, ui, settings: Settings) -> None:
        self.ui = ui
        self.settings = settings
        self.name, self.user_name = _identity()
        self._abort = threading.Event()
        self._turn_lock = threading.Lock()
        self._busy = False
        self._speaking = False
        self._tail_until = 0.0
        self._turn = 0
        self._ptt_enabled = False
        self._ptt_held = False
        self._wake_enabled = False
        self._awake = True
        self._wake = None
        self._last_speech = time.monotonic()
        self._engine = None
        self._engine_key = None
        self._last_attachment: tuple[str, float] | None = None
        self._speech_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue(maxsize=3)
        self.player = Player(self.ui.push_visemes, self.ui.set_audio_level)
        self.mic = Microphone(self._on_utterance, self._mic_level, self._can_listen,
                              float(settings.get("vad_threshold", 0.08)), on_frame=self._on_mic_frame)
        self.stt = Transcriber(str(settings.get("stt_model")), on_status=self._note)
        self.voice = make_voice(settings, on_status=self._note)

    # ── Avvio ────────────────────────────────────────────────────────────
    def start(self) -> None:
        threading.Thread(target=self._synth_loop, name="tts-synth", daemon=True).start()
        threading.Thread(target=self._play_loop, name="tts-play", daemon=True).start()
        threading.Thread(target=self._warmup, name="warmup", daemon=True).start()
        threading.Thread(target=self._scheduler_loop, name="scheduler", daemon=True).start()

    def _start_whatsapp_bridge(self) -> None:
        try:
            from avatar import whatsapp_bridge as wb
            if self.settings.get("whatsapp_live") or wb.is_linked():
                self.ui.write_log(f"SYS: WhatsApp — {wb.start(self.user_name)}")
                self._wa_last_check = time.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as err:
            self.ui.write_log(f"ERR: WhatsApp — {err}")

    def _announce_whatsapp(self) -> None:
        """Legge i messaggi WhatsApp arrivati dall'ultimo controllo e, se richiesto, li annuncia."""
        import sqlite3
        from avatar.settings import DATA_DIR
        db = DATA_DIR / "whatsapp" / "index.sqlite"
        if not db.exists():
            return
        since = getattr(self, "_wa_last_check", None) or time.strftime("%Y-%m-%d %H:%M:%S")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT ts, chat, sender, text FROM messages WHERE source = 'live' AND from_me = 0 AND ts > ? ORDER BY ts LIMIT 5", (since,)).fetchall()
        finally:
            con.close()
        self._wa_last_check = now
        for ts, chat, sender, text in rows:
            who = chat if sender == chat else f"{sender} nel gruppo {chat}"
            self.ui.write_log(f"WhatsApp: {who}: {text[:200]}")
            if self.settings.get("whatsapp_annuncia") and not self._busy and not self._speaking:
                self.say(f"Messaggio WhatsApp da {who}: {text[:220]}")

    def _scheduler_loop(self) -> None:
        """Fa scattare timer, sveglie e attività programmate dal plugin timer."""
        import importlib
        while True:
            time.sleep(5)
            try:
                mod = importlib.import_module("avatar_plugins.timer")
            except Exception:
                try:
                    from avatar.plugins import registry
                    registry.load()
                    mod = importlib.import_module("avatar_plugins.timer")
                except Exception:
                    continue
            try:
                self._announce_whatsapp()
            except Exception as err:
                print(f"[whatsapp] {err}")
            try:
                for item in mod.due():
                    testo = str(item.get("testo", ""))
                    if item.get("comando"):
                        self.ui.write_log(f"SYS: Attività programmata: {testo}")
                        self.handle_text(testo)
                    else:
                        self.ui.write_log(f"SYS: Timer: {testo}")
                        self.say(f"Promemoria: {testo}")
            except Exception as err:
                print(f"[scheduler] {err}")

    def _warmup(self) -> None:
        self.ui.set_state("PROCESSING")
        self.ui.write_log(f"SYS: {self.name} si sta avviando: carico voce e riconoscimento vocale…")
        try:
            self.voice.load()
        except Exception as err:
            self.ui.write_log(f"ERR: Voce non disponibile — {err}")
        try:
            self.stt.load()
        except Exception as err:
            self.ui.write_log(f"ERR: Riconoscimento vocale non disponibile — {err}")
        self.reopen_audio()
        self._ensure_engine()
        self._start_whatsapp_bridge()
        try:
            from avatar import monitor as _mon
            _mon.monitor = _mon.Monitor(self.ui, self.say, self.settings)
            _mon.monitor.start()
        except Exception as err:
            self.ui.write_log(f"ERR: Monitor — {err}")
        self.ui.set_state("LISTENING")
        self.ui.write_log(f"SYS: {self.name} è pronta. Parla o scrivi.")

    def reopen_audio(self) -> None:
        try:
            self.player.open(cm.get_output_device())
        except Exception as err:
            self.ui.write_log(f"ERR: Uscita audio — {err}")
        try:
            self.mic.start(cm.get_input_device())
        except Exception as err:
            self.ui.write_log(f"ERR: Microfono — {err}")

    def _note(self, msg: str | None) -> None:
        if msg:
            self.ui.write_log(f"SYS: {msg}")

    # ── Motore ───────────────────────────────────────────────────────────
    def _ensure_engine(self):
        s = self.settings
        provider = s.get("provider")
        self.name, self.user_name = _identity()
        key = (provider, s.get("effort"), s.get("local_base_url"), s.get("local_model"), s.get("mlx_model"), s.get("mlx_thinking"), s.get("search_api_key"),
               s.get("claudecode_model"), s.get("claudecode_access"), s.get("claudecode_config_dir"),
               s.get("claudecode_path"), self.name, self.user_name, s.get_secret("anthropic_api_key")[-6:],
               s.get_secret("local_api_key")[-4:])
        if self._engine and self._engine_key == key:
            return self._engine
        old, self._engine, self._engine_key = self._engine, None, key
        if old is not None and hasattr(old, "close"):
            old.close()   # libera il modello interno dalla memoria
        if provider == "mlx":
            self._engine = MLXEngine(s.get("mlx_model") or "", s.get("search_api_key"), self.name, self.user_name, s.get("mlx_thinking") or "auto")
        elif provider == "local":
            if s.get("local_base_url") and s.get("local_model"):
                self._engine = OpenAICompatEngine(s.get("local_base_url"), s.get("local_model"), s.get_secret("local_api_key"),
                                                  s.get("search_api_key"), self.name, self.user_name)
        elif provider == "claudecode":
            self._engine = ClaudeCodeEngine(s.get("claudecode_model") or "sonnet", s.get("claudecode_access") or "chat",
                                            s.get("claudecode_path") or "", s.get("claudecode_config_dir") or "",
                                            self.name, self.user_name, s.get("effort"))
        else:
            api_key = s.get_secret("anthropic_api_key")
            if api_key:
                self._engine = AnthropicEngine(api_key, self.name, self.user_name, s.get("effort"))
        return self._engine

    def reconfigure(self) -> None:
        """Dopo un salvataggio delle impostazioni."""
        self.interrupt(silent=True)
        self._ensure_engine()
        self.reload_voice()
        model = str(self.settings.get("stt_model"))
        if model != self.stt.model:
            self.stt = Transcriber(model, on_status=self._note)
            threading.Thread(target=self.stt.load, daemon=True).start()
        self.mic.threshold = float(self.settings.get("vad_threshold", 0.08))
        try:
            view = getattr(self.ui._win, "avatar3d", None)
            if view is not None:
                view.set_model(str(self.settings.get("avatar_model") or ""))
        except Exception as err:
            self.ui.write_log(f"ERR: Avatar 3D — {err}")
        self.ui.write_log(f"SYS: Impostazioni applicate — motore {self.settings.get('provider')}.")

    def reload_voice(self, from_customise: bool = False) -> None:
        """Ricarica la voce. `from_customise`: la scelta arriva dal pannello Customise
        (Sara / Nicola / Sistema) e va copiata nelle impostazioni; altrimenti comandano
        le impostazioni di "Motore e Voce" e il pannello viene allineato."""
        mapping = {"Sara": ("kokoro", "if_sara"), "Nicola": ("kokoro", "im_nicola"), "Sistema": ("system", None)}
        if from_customise:
            chosen = (cm.get_voice() or "").strip()
            if chosen in mapping:
                engine, voice = mapping[chosen]
                self.settings.set("tts_engine", engine)
                if voice:
                    self.settings.set("kokoro_voice", voice)
                self.settings.save()
        else:
            eng = self.settings.get("tts_engine")
            label = "Sistema" if eng == "system" else ("Sara" if eng in ("chatterbox", "elevenlabs", "voicebox") else
                {"if_sara": "Sara", "im_nicola": "Nicola"}.get(self.settings.get("kokoro_voice"), "Sara"))
            try:
                if cm.get_voice() != label:
                    cm.save_voice(label)
            except Exception:
                pass
        new = make_voice(self.settings, on_status=self._note)
        if type(new) is type(self.voice) and getattr(new, "voice", None) == getattr(self.voice, "voice", None):
            return
        self.voice = new
        self.ui.write_log(f"SYS: Voce: {getattr(new, 'voice', '') or 'sistema'} ({'Kokoro' if self.settings.get('tts_engine') == 'kokoro' else 'macOS'}).")
        threading.Thread(target=self._safe_load_voice, daemon=True).start()

    def _safe_load_voice(self) -> None:
        try:
            self.voice.load()
        except Exception as err:
            self.ui.write_log(f"ERR: Voce non disponibile — {err}")

    def reset_conversation(self) -> None:
        self.interrupt(silent=True)
        if self._engine:
            self._engine.reset()
        self.ui.write_log("SYS: Nuova conversazione.")

    # ── Ascolto ──────────────────────────────────────────────────────────
    def _can_listen(self) -> bool:
        if self.ui.muted or self._busy or self._speaking or not self._awake:
            return False
        if time.monotonic() < self._tail_until:
            return False
        if self._ptt_enabled and not self._ptt_held:
            return False
        return True

    def _mic_level(self, level: float) -> None:
        self.ui.set_audio_level(level)

    def _on_mic_frame(self, frame: np.ndarray) -> None:
        if self._wake is not None and self._wake_enabled and not self._awake:
            try:
                self._wake.feed(frame)
            except Exception:
                pass
        if self._wake_enabled and self._awake and not self._busy and not self._speaking:
            if time.monotonic() - self._last_speech > 120:
                self._sleep("silenzio")

    def _on_utterance(self, audio: np.ndarray) -> None:
        self._last_speech = time.monotonic()
        self.ui.set_state("PROCESSING")
        try:
            text = self.stt.transcribe(audio)
        except Exception as err:
            self.ui.write_log(f"ERR: Trascrizione — {err}")
            self.ui.set_state("LISTENING")
            return
        text = text.strip()
        if len(text) < 2 or _is_hallucination(text):
            self.ui.set_state("LISTENING")
            return
        self.ui.write_log(f"You: {text}")
        self.handle_text(text)

    # ── Turno di conversazione ───────────────────────────────────────────
    def handle_text(self, text: str) -> None:
        """Chiamabile da qualunque thread (UI, microfono, quiz…)."""
        text = (text or "").strip()
        if not text:
            return
        if self._busy or self._speaking:
            self.interrupt(silent=True)
        threading.Thread(target=self._run_turn, args=(text,), daemon=True).start()

    def say(self, text: str) -> None:
        """Pronuncia un testo senza passare dal motore."""
        for s in SentenceSplitter().push(text + "\n") + []:
            self._speech_q.put((self._turn, s))
        self._speech_q.put((self._turn, END))

    def _run_turn(self, text: str) -> None:
        with self._turn_lock:
            engine = self._ensure_engine()
            if engine is None:
                self.ui.write_log("ERR: Motore non configurato: apri Motore & Voce e completa le impostazioni.")
                self.ui.set_state("LISTENING")
                return
            self._abort.clear()
            self._busy = True
            self._turn += 1
            turn = self._turn
            self.ui.set_state("THINKING")
            splitter = SentenceSplitter()

            def emit(ev: dict) -> None:
                if turn != self._turn:
                    return
                t = ev.get("type")
                if t == "status":
                    if not self._speaking:
                        self.ui.set_state(STATUS_LABELS.get(ev["status"], "THINKING"))
                    if ev["status"] == "searching":
                        self.ui.write_log("SYS: Cerco sul web…")
                    elif ev["status"] == "working":
                        self.ui.write_log(f"SYS: Lavoro sul Mac ({ev.get('detail', '')})…")
                elif t == "text":
                    for s in splitter.push(ev["delta"]):
                        self._speech_q.put((turn, s))
                elif t == "memory_saved":
                    self.ui.write_log(f"SYS: Memoria salvata — {ev['text']}")
                elif t == "memory_removed":
                    self.ui.write_log("SYS: Memoria cancellata.")
                elif t == "done":
                    for s in splitter.flush():
                        self._speech_q.put((turn, s))
                    self.ui.write_log(f"{self.name}: {ev['text']}")
                    if ev.get("sources"):
                        self.ui.show_content("Fonti", "\n".join(f"• {s['title']}\n  {s['url']}" for s in ev["sources"]))
                elif t == "error":
                    if not ev.get("aborted"):
                        self.ui.write_log(f"ERR: {ev['message']}")
                        self._speech_q.put((turn, clean_for_speech("Scusa, c'è stato un problema: " + ev["message"])[:300]))

            # Allegato dalla zona "File upload" (una volta per file)
            engine_text, image, attach_path = text, None, ""
            try:
                path = self.ui.current_file
                if path:
                    from pathlib import Path as _P
                    p = _P(path)
                    key = (str(p), p.stat().st_mtime)
                    if p.exists() and key != self._last_attachment:
                        self._last_attachment = key
                        from avatar.attachments import describe
                        self.ui.write_log(f"FILE: {p.name}")
                        ctx_text, image = describe(p)
                        engine_text = f"{text}\n\n{ctx_text}"
                        attach_path = str(p)
            except Exception as err:
                self.ui.write_log(f"ERR: Allegato — {err}")
            try:
                engine.send(engine_text, emit, self._abort, image=image, attach_path=attach_path)
            finally:
                self._busy = False
                self._speech_q.put((turn, END))

    # ── Voce: sintesi in anticipo e riproduzione ─────────────────────────
    def _synth_loop(self) -> None:
        while True:
            turn, item = self._speech_q.get()
            if turn != self._turn:
                continue
            if item is END:
                self._audio_q.put((turn, END, None))
                continue
            try:
                audio = self.voice.synthesize(item)
            except Exception as err:
                self.ui.write_log(f"ERR: Sintesi vocale — {err}")
                continue
            if turn == self._turn:
                self._audio_q.put((turn, item, audio))

    def _play_loop(self) -> None:
        while True:
            turn, text, audio = self._audio_q.get()
            if turn != self._turn:
                continue
            if text is END:
                self.player.drain()
                self._speaking = False
                self._tail_until = time.monotonic() + 0.5
                if not self._busy and turn == self._turn:
                    self.ui.set_state("LISTENING" if self._awake else "SLEEPING")
                continue
            if audio is None or len(audio) == 0:
                continue
            self._speaking = True
            self.ui.set_state("SPEAKING")
            self.player.play(audio, text, self._abort)
            self._last_speech = time.monotonic()

    def interrupt(self, silent: bool = False) -> None:
        self._abort.set()
        self._turn += 1
        for q in (self._speech_q, self._audio_q):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
        self.player.abort()
        self._speaking = False
        self._busy = False
        self._tail_until = time.monotonic() + 0.4
        if not silent:
            self.ui.write_log("SYS: Interrotta. Ti ascolto.")
        self.ui.set_state("LISTENING" if self._awake else "SLEEPING")

    # ── Push-to-talk e wake word ─────────────────────────────────────────
    def set_push_to_talk(self, enabled: bool) -> str:
        self._ptt_enabled = bool(enabled)
        self._ptt_held = False
        return "window"

    def ptt_hold(self, held: bool) -> None:
        self._ptt_held = bool(held)
        if held and not self._awake:
            self._wake_up("tasto")
        self.ui.set_state("LISTENING" if held else ("LISTENING" if not self._ptt_enabled else "SLEEPING"))

    def wake_get_state(self) -> dict:
        ready = False
        try:
            from core.wake_word import is_ready
            ready = is_ready()
        except Exception:
            pass
        return {"enabled": self._wake_enabled, "awake": self._awake, "ready": ready}

    def on_wake_toggle(self, enable: bool) -> str:
        if enable:
            try:
                from core.wake_word import WakeWordDetector, is_ready
                if not is_ready():
                    return "Il modello della parola di attivazione non è installato."
                self._wake = WakeWordDetector(on_detect=lambda: self._wake_up("parola di attivazione"))
                if not self._wake.start():
                    self._wake = None
                    return "Impossibile avviare la parola di attivazione."
            except Exception as err:
                self._wake = None
                return f"Parola di attivazione non disponibile: {err}"
            self._wake_enabled = True
            self._last_speech = time.monotonic()
            self.ui.write_log("SYS: Parola di attivazione attiva: di' «Hey Jarvis» per svegliarmi.")
            return "on"
        self._wake_enabled = False
        if self._wake:
            try:
                self._wake.stop()
            except Exception:
                pass
            self._wake = None
        self._wake_up("disattivata")
        return "off"

    def on_wake_manual(self) -> None:
        if self._awake:
            self._sleep("manuale")
        else:
            self._wake_up("manuale")

    def _sleep(self, reason: str) -> None:
        if not self._awake:
            return
        self._awake = False
        self.ui.set_state("SLEEPING")
        self.ui.write_log(f"SYS: In pausa ({reason}).")

    def _wake_up(self, reason: str) -> None:
        self._last_speech = time.monotonic()
        if self._awake:
            return
        self._awake = True
        self.ui.set_state("LISTENING")
        self.ui.write_log(f"SYS: Sveglia ({reason}).")
