"""Sintesi vocale: Kokoro in locale (voci italiane) oppure la voce di sistema di macOS."""
from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np

KOKORO_VOICES = {"if_sara": "Sara (femminile)", "im_nicola": "Nicola (maschile)"}
OUT_RATE = 24000


def clean_for_speech(text: str) -> str:
    """Toglie Markdown, link ed emoji prima di leggere."""
    t = re.sub(r"```[\s\S]*?```", " ", text)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+\.\s+", "", t, flags=re.M)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)
    t = re.sub(r"_([^_]+)_", r"\1", t)
    t = re.sub(r"^\|.*\|$", "", t, flags=re.M)
    t = re.sub(r"[*_#>|]", "", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", "", t)
    return re.sub(r"\s+", " ", t).strip()


class SentenceSplitter:
    """Riceve testo incrementale e restituisce le frasi complete."""

    _RE = re.compile(r'[^.!?\n]+[.!?]+["»”)]?\s+|[^\n]+\n+')

    def __init__(self) -> None:
        self._buf = ""

    def push(self, delta: str) -> list[str]:
        self._buf += delta
        if self._buf.count("```") % 2 == 1:
            return []
        out, last = [], 0
        for m in self._RE.finditer(self._buf):
            out.append(m.group(0))
            last = m.end()
        self._buf = self._buf[last:]
        return [s for s in (clean_for_speech(x) for x in out) if s]

    def flush(self) -> list[str]:
        rest, self._buf = clean_for_speech(self._buf), ""
        return [rest] if rest else []


class KokoroVoice:
    """Kokoro-82M via il pacchetto `kokoro`; fonetica italiana con espeak-ng."""

    def __init__(self, voice: str = "if_sara", speed: float = 1.0, on_status=None) -> None:
        self.voice = voice
        self.speed = speed
        self._on_status = on_status or (lambda m: None)
        self._pipe = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._pipe is not None:
                return
            self._on_status("Carico la voce Kokoro…")
            try:
                from kokoro import KPipeline
                self._pipe = KPipeline(lang_code="i", repo_id="hexgrad/Kokoro-82M")
                for _ in self._pipe("ciao", voice=self.voice, speed=self.speed):
                    pass
            finally:
                self._on_status(None)

    def synthesize(self, text: str) -> np.ndarray:
        if self._pipe is None:
            self.load()
        chunks = []
        with self._lock:
            for _, _, audio in self._pipe(text, voice=self.voice, speed=self.speed):
                if audio is not None:
                    arr = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
                    chunks.append(arr.astype(np.float32).reshape(-1))
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)


class SystemVoice:
    """Voce di macOS tramite `say`, resa in un file audio e riprodotta dall'app."""

    def __init__(self, voice: str = "") -> None:
        self.voice = voice

    @staticmethod
    def list_voices() -> list[str]:
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return []
        names = []
        for line in out.splitlines():
            if "it_IT" in line:
                names.append(line.split("  ")[0].strip())
        return sorted(set(names))

    def load(self) -> None:
        pass

    def synthesize(self, text: str) -> np.ndarray:
        import soundfile as sf
        voice = self.voice or next(iter(v for v in self.list_voices() if v.startswith("Alice")), "") or ""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "say.wav"
            cmd = ["say", "-o", str(path), "--data-format=LEI16@24000"]
            if voice:
                cmd += ["-v", voice]
            cmd.append(text)
            subprocess.run(cmd, check=True, timeout=120)
            data, rate = sf.read(str(path), dtype="float32")
        if data.ndim > 1:
            data = data[:, 0]
        if rate != OUT_RATE:
            data = np.interp(np.arange(0, data.size, rate / OUT_RATE), np.arange(data.size), data).astype(np.float32)
        return data


class ChatterboxVoice:
    """Chatterbox (Resemble AI) in un ambiente separato, avviato come servizio locale."""

    PORT = 8792
    _proc = None

    def __init__(self, exaggeration: float = 0.6, cfg: float = 0.3, ref_audio: str = "", on_status=None) -> None:
        self.exaggeration, self.cfg, self.ref_audio = exaggeration, cfg, ref_audio
        self.voice = f"chatterbox:{exaggeration}:{cfg}:{ref_audio}"
        self._on_status = on_status or (lambda m: None)

    @classmethod
    def _start(cls) -> None:
        import subprocess
        from pathlib import Path as _P
        if cls._proc is not None and cls._proc.poll() is None:
            return
        base = _P(__file__).resolve().parent.parent
        python = base / ".venv-chatterbox" / "bin" / "python"
        if not python.exists():
            raise RuntimeError("Chatterbox non installato: esegui `uv venv .venv-chatterbox --python 3.12 && uv pip install --python .venv-chatterbox/bin/python chatterbox-tts 'setuptools<81'`.")
        log = open(base / "data" / "chatterbox.log", "a")
        cls._proc = subprocess.Popen([str(python), str(base / "tts_chatterbox" / "server.py"), "--port", str(cls.PORT)],
                                     stdout=log, stderr=subprocess.STDOUT, cwd=str(base))

    def load(self) -> None:
        import requests
        import time as _t
        self._start()
        self._on_status("Avvio Chatterbox (la prima volta scarica il modello, circa 2 GB)…")
        try:
            for _ in range(600):   # fino a 10 minuti (download iniziale)
                try:
                    st = requests.get(f"http://127.0.0.1:{self.PORT}/", timeout=3).json()
                    if st.get("ready"):
                        return
                    if st.get("error"):
                        raise RuntimeError(st["error"])
                except requests.RequestException:
                    pass
                if self._proc is not None and self._proc.poll() is not None:
                    raise RuntimeError("Il servizio Chatterbox si è chiuso: vedi data/chatterbox.log.")
                _t.sleep(1)
            raise RuntimeError("Chatterbox non è pronto.")
        finally:
            self._on_status(None)

    REFERENCE_TEXT = ("Ciao, sono la voce del tuo assistente. Oggi ti racconto come è andata la giornata, con calma e con un po' di allegria. "
                      "Se hai bisogno di qualcosa, basta chiedere: sono qui per aiutarti, e mi fa piacere farlo.")
    PRESETS = {"preset:femminile": "if_sara", "preset:maschile": "im_nicola"}

    def _reference_path(self) -> str | None:
        """File wav di riferimento: percorso dell'utente, oppure preset generato con Kokoro (una volta)."""
        ref = (self.ref_audio or "").strip()
        if not ref:
            return None
        if ref in self.PRESETS:
            from pathlib import Path as _P
            import soundfile as sf
            out = _P(__file__).resolve().parent.parent / "data" / "voices" / f"{ref.split(':')[1]}.wav"
            if not out.exists():
                out.parent.mkdir(parents=True, exist_ok=True)
                self._on_status("Preparo il campione di voce per Chatterbox…")
                k = KokoroVoice(self.PRESETS[ref], speed=0.95)
                k.load()
                sf.write(str(out), k.synthesize(self.REFERENCE_TEXT), OUT_RATE)
                self._on_status(None)
            return str(out)
        return ref

    def synthesize(self, text: str) -> np.ndarray:
        import requests
        r = requests.post(f"http://127.0.0.1:{self.PORT}/tts", json={"text": text, "language": "it", "exaggeration": self.exaggeration,
                                                                     "cfg": self.cfg, "ref": self._reference_path()}, timeout=600)
        if r.status_code != 200:
            raise RuntimeError(r.json().get("error", r.text))
        sr = int(r.headers.get("X-Sample-Rate", "24000"))
        data = np.frombuffer(r.content, dtype=np.float32)
        if sr != OUT_RATE:
            data = np.interp(np.arange(0, data.size, sr / OUT_RATE), np.arange(data.size), data).astype(np.float32)
        return data

    @classmethod
    def stop(cls) -> None:
        if cls._proc is not None and cls._proc.poll() is None:
            cls._proc.terminate()
        cls._proc = None


class ElevenLabsVoice:
    """ElevenLabs (cloud): voci molto espressive, prima parola in circa mezzo secondo."""

    API = "https://api.elevenlabs.io/v1"

    def __init__(self, api_key: str, voice_id: str, model: str = "eleven_flash_v2_5", stability: float = 0.45, similarity: float = 0.8, style: float = 0.3) -> None:
        self.api_key, self.voice_id, self.model = api_key, voice_id, model
        self.stability, self.similarity, self.style = stability, similarity, style
        self.voice = f"elevenlabs:{voice_id}:{model}:{stability}:{style}"

    @classmethod
    def list_voices(cls, api_key: str) -> list[tuple[str, str]]:
        import requests
        r = requests.get(f"{cls.API}/voices", headers={"xi-api-key": api_key}, timeout=15)
        r.raise_for_status()
        out = []
        for v in r.json().get("voices", []):
            labels = v.get("labels") or {}
            desc = ", ".join(x for x in (labels.get("gender"), labels.get("accent"), labels.get("language"), v.get("category")) if x)
            out.append((v["voice_id"], f"{v['name']}" + (f" ({desc})" if desc else "")))
        return out

    def load(self) -> None:
        if not self.api_key:
            raise RuntimeError("ElevenLabs: manca la chiave API (Motore e Voce).")
        if not self.voice_id:
            raise RuntimeError("ElevenLabs: scegli una voce in Motore e Voce (pulsante «Carica voci»).")

    def synthesize(self, text: str) -> np.ndarray:
        import requests
        r = requests.post(f"{self.API}/text-to-speech/{self.voice_id}", params={"output_format": "pcm_24000"},
                          headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                          json={"text": text, "model_id": self.model, "language_code": "it",
                                "voice_settings": {"stability": self.stability, "similarity_boost": self.similarity, "style": self.style, "use_speaker_boost": True}},
                          timeout=60)
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", {})
                msg = detail.get("message") if isinstance(detail, dict) else str(detail)
            except Exception:
                msg = r.text[:200]
            raise RuntimeError(f"ElevenLabs {r.status_code}: {msg}")
        return np.frombuffer(r.content, dtype=np.int16).astype(np.float32) / 32768.0


def make_voice(settings, on_status=None):
    if settings.get("tts_engine") == "elevenlabs":
        return ElevenLabsVoice(settings.get_secret("elevenlabs_api_key"), str(settings.get("elevenlabs_voice_id") or ""),
                               str(settings.get("elevenlabs_model") or "eleven_flash_v2_5"),
                               float(settings.get("elevenlabs_stability", 0.45)), 0.8, float(settings.get("elevenlabs_style", 0.3)))
    if settings.get("tts_engine") == "chatterbox":
        return ChatterboxVoice(float(settings.get("chatterbox_exaggeration", 0.6)), float(settings.get("chatterbox_cfg", 0.3)),
                               str(settings.get("chatterbox_ref", "") or ""), on_status=on_status)
    if settings.get("tts_engine") == "system":
        return SystemVoice(settings.get("system_voice", ""))
    return KokoroVoice(settings.get("kokoro_voice", "if_sara"), on_status=on_status)
