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


def make_voice(settings, on_status=None):
    if settings.get("tts_engine") == "system":
        return SystemVoice(settings.get("system_voice", ""))
    return KokoroVoice(settings.get("kokoro_voice", "if_sara"), on_status=on_status)
