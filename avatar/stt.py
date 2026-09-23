"""Riconoscimento vocale in locale: MLX Whisper (Apple Silicon) con ripiego su faster-whisper."""
from __future__ import annotations

import threading

import numpy as np


class Transcriber:
    def __init__(self, model: str, on_status=None) -> None:
        self.model = model
        self._on_status = on_status or (lambda m: None)
        self._backend: str | None = None
        self._fw = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._backend:
                return
            try:
                import mlx_whisper  # noqa: F401
                self._backend = "mlx"
                self._on_status("Carico il modello vocale…")
                # Un giro a vuoto scarica il modello e scalda il grafo. Se la rete
                # fa i capricci ma il modello è già in cache, riprova offline.
                try:
                    self.transcribe(np.zeros(16000, dtype=np.float32))
                except Exception as first:
                    import os
                    print(f"[STT] primo caricamento fallito ({first}); riprovo in modalità offline")
                    os.environ["HF_HUB_OFFLINE"] = "1"
                    try:
                        self.transcribe(np.zeros(16000, dtype=np.float32))
                    finally:
                        os.environ.pop("HF_HUB_OFFLINE", None)
            except Exception as err:
                print(f"[STT] MLX Whisper non disponibile ({err}); uso faster-whisper")
                from faster_whisper import WhisperModel
                name = "small" if "small" in self.model else "base"
                self._fw = WhisperModel(name, device="cpu", compute_type="int8")
                self._backend = "faster"
            finally:
                self._on_status(None)

    def transcribe(self, audio16k: np.ndarray) -> str:
        """`audio16k`: float32 mono a 16 kHz oppure int16."""
        if audio16k.dtype != np.float32:
            audio16k = audio16k.astype(np.float32) / 32768.0
        if self._backend is None:
            self.load()
        if self._backend == "mlx":
            import mlx_whisper
            res = mlx_whisper.transcribe(audio16k, path_or_hf_repo=self.model, language="it", fp16=True)
            return str(res.get("text", "")).strip()
        segments, _ = self._fw.transcribe(audio16k, language="it", beam_size=1, vad_filter=True)
        return " ".join(s.text for s in segments).strip()
