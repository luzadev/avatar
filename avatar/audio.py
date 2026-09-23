"""Microfono con rilevamento della voce e riproduzione con sincronizzazione labiale."""
from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Callable

import numpy as np
import sounddevice as sd

from core import audio_devices
from core.viseme import VisemeStream
from .lipsync import HOP_SECONDS, float_to_pcm_scale, pcm_level, pcm_visemes

MIC_RATE = 16000
OUT_RATE = 24000
MIC_BLOCK = 480          # 30 ms
PRE_ROLL_BLOCKS = 10     # 300 ms prima dell'inizio del parlato
START_BLOCKS = 3         # 90 ms di voce per iniziare
END_SILENCE_S = 0.8
MIN_SPEECH_S = 0.35
MAX_UTTERANCE_S = 30.0


class Microphone:
    """Cattura continua; segmenta le frasi con una soglia di volume adattiva."""

    def __init__(
        self,
        on_utterance: Callable[[np.ndarray], None],
        on_level: Callable[[float], None],
        can_listen: Callable[[], bool],
        threshold: float = 0.08,
        on_frame: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        self._on_utterance = on_utterance
        self._on_level = on_level
        self._can_listen = can_listen
        self._on_frame = on_frame
        self.threshold = threshold
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._device_name = ""

    def start(self, device_name: str = "") -> None:
        self.stop()
        self._device_name = device_name
        try:
            device = audio_devices.resolve(device_name, "input") if device_name else None
        except Exception:
            device = None
        self._stream = sd.InputStream(
            samplerate=MIC_RATE, channels=1, dtype="int16", blocksize=MIC_BLOCK,
            device=device, callback=self._callback,
        )
        self._stream.start()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="mic-vad", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _callback(self, indata, frames, t, status) -> None:
        self._q.put(indata[:, 0].copy())

    def _loop(self) -> None:
        pre = deque(maxlen=PRE_ROLL_BLOCKS)
        collecting: list[np.ndarray] = []
        voiced_run = 0
        silence_s = 0.0
        speech_s = 0.0
        block_s = MIC_BLOCK / MIC_RATE
        floor = 0.0
        while self._running:
            try:
                frame = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._on_frame:
                try:
                    self._on_frame(frame)
                except Exception:
                    pass
            level = pcm_level(frame)
            # Rumore di fondo: media lenta dei livelli bassi.
            if level < self.threshold:
                floor = floor * 0.98 + level * 0.02
            if not self._can_listen():
                # Se stavamo raccogliendo (per esempio il tasto premi-per-parlare
                # è stato rilasciato) chiudi la frase invece di perderla.
                if collecting and speech_s >= MIN_SPEECH_S:
                    audio = np.concatenate(collecting)
                    collecting = []
                    try:
                        self._on_utterance(audio)
                    except Exception:
                        pass
                pre.clear()
                collecting = []
                voiced_run = 0
                continue
            self._on_level(level)
            voiced = level > max(self.threshold, floor * 2.5 + 0.02)
            if not collecting:
                pre.append(frame)
                voiced_run = voiced_run + 1 if voiced else 0
                if voiced_run >= START_BLOCKS:
                    collecting = list(pre)
                    silence_s = 0.0
                    speech_s = voiced_run * block_s
                    voiced_run = 0
                continue
            collecting.append(frame)
            if voiced:
                silence_s = 0.0
                speech_s += block_s
            else:
                silence_s += block_s
            total_s = len(collecting) * block_s
            if silence_s >= END_SILENCE_S or total_s >= MAX_UTTERANCE_S:
                audio = np.concatenate(collecting)
                collecting = []
                pre.clear()
                if speech_s >= MIN_SPEECH_S:
                    try:
                        self._on_utterance(audio)
                    except Exception:
                        pass


class Player:
    """Riproduce audio a 24 kHz e programma le forme della bocca sul tempo reale."""

    def __init__(self, on_visemes: Callable, on_level: Callable[[float], None]) -> None:
        self._on_visemes = on_visemes
        self._on_level = on_level
        self._visemes = VisemeStream()
        self._stream: sd.OutputStream | None = None
        self._lock = threading.Lock()
        self._device_name = ""
        self._cursor = 0.0

    def open(self, device_name: str = "") -> None:
        self.close()
        self._device_name = device_name
        try:
            device = audio_devices.resolve(device_name, "output") if device_name else None
        except Exception:
            device = None
        self._stream = sd.OutputStream(samplerate=OUT_RATE, channels=1, dtype="float32", device=device)
        self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    @property
    def latency(self) -> float:
        try:
            return float(self._stream.latency) if self._stream else 0.05
        except Exception:
            return 0.05

    def play(self, audio: np.ndarray, text: str, stop: threading.Event) -> None:
        """Bloccante: suona `audio` (float32 mono 24 kHz) mentre anima la bocca."""
        if self._stream is None:
            self.open(self._device_name)
        assert self._stream is not None
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return
        with self._lock:
            try:
                self._visemes.feed_text(text)
            except Exception:
                pass
            # Il cursore è il momento in cui il prossimo blocco inizierà a suonare.
            now = time.time() + self.latency + 0.04
            if self._cursor < now:
                self._cursor = now
            block = int(OUT_RATE * 0.2)
            for i in range(0, audio.size, block):
                if stop.is_set():
                    break
                chunk = audio[i:i + block]
                frames = pcm_visemes(float_to_pcm_scale(chunk), OUT_RATE)
                if frames:
                    try:
                        frames = self._visemes.frames(frames, HOP_SECONDS)
                    except Exception:
                        pass
                    self._on_visemes(frames, HOP_SECONDS, self._cursor)
                    self._on_level(max(f[0] for f in frames))
                else:
                    self._on_level(pcm_level(float_to_pcm_scale(chunk)))
                self._cursor += chunk.size / OUT_RATE
                try:
                    self._stream.write(chunk.reshape(-1, 1))
                except Exception:
                    break
            if stop.is_set():
                self.abort()

    def abort(self) -> None:
        """Ferma subito, scartando l'audio in coda."""
        try:
            self._visemes.reset()
        except Exception:
            pass
        self._cursor = 0.0
        if self._stream is not None:
            try:
                self._stream.abort()
                self._stream.start()
            except Exception:
                self.open(self._device_name)
        self._on_level(0.0)

    def drain(self) -> None:
        """Aspetta che l'audio scritto abbia finito di suonare."""
        wait = max(0.0, self._cursor - time.time())
        if wait > 0:
            time.sleep(min(wait, 5.0))
        self._on_level(0.0)
