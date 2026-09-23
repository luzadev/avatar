"""Livello audio e forme della bocca dallo spettro (adattato da Mark-LIV, CC BY-NC 4.0)."""
from __future__ import annotations

import numpy as np

# Soglie sull'ampiezza int16 (come nell'originale).
_LEVEL_FLOOR = 60.0
_LEVEL_FULL = 2600.0
VIS_WIN = 1024   # ~43 ms a 24 kHz
VIS_HOP = 480    # 20 ms → 50 forme al secondo
HOP_SECONDS = VIS_HOP / 24000.0


def pcm_level(samples) -> float:
    """Volume 0..1 da campioni in scala int16 (float o int)."""
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0
    if rms <= _LEVEL_FLOOR:
        return 0.0
    return min(1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))


def float_to_pcm_scale(audio: np.ndarray) -> np.ndarray:
    """Audio float32 -1..1 → scala int16 (senza cambiare tipo)."""
    return np.asarray(audio, dtype=np.float32) * 32767.0


def pcm_visemes(samples, sr: int = 24000):
    """Frame (livello, apertura, larghezza) ogni 20 ms da un blocco audio in scala int16."""
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size < VIS_WIN:
            return []
        win = np.hanning(VIS_WIN).astype(np.float32)
        freqs = np.fft.rfftfreq(VIS_WIN, 1.0 / sr)
        b_f1_lo = (freqs >= 150) & (freqs < 450)
        b_f1_hi = (freqs >= 450) & (freqs < 1100)
        b_f2_bk = (freqs >= 600) & (freqs < 1300)
        b_f2_fr = (freqs >= 1700) & (freqs < 3200)
        b_hiss = (freqs >= 3800) & (freqs < 8000)
        out = []
        for start in range(0, x.size, VIS_HOP):
            level = pcm_level(x[start:start + VIS_HOP])
            seg = x[start:start + VIS_WIN]
            if seg.size < VIS_WIN:
                seg = np.concatenate([seg, np.zeros(VIS_WIN - seg.size, dtype=np.float32)])
            if level <= 0.0:
                out.append((0.0, 0.0, 0.0))
                continue
            mag = np.abs(np.fft.rfft((seg - seg.mean()) * win))
            f1l, f1h = float(mag[b_f1_lo].sum()), float(mag[b_f1_hi].sum())
            f2b, f2f = float(mag[b_f2_bk].sum()), float(mag[b_f2_fr].sum())
            hiss = float(mag[b_hiss].sum())
            openness = f1h / (f1l + f1h + 1e-6)
            width = (f2f - f2b) / (f2f + f2b + 1e-6)
            width *= (1.0 - openness) ** 0.8
            h = hiss / (f1l + f1h + f2b + f2f + hiss + 1e-6)
            openness *= 1.0 - 0.65 * min(1.0, h * 2.5)
            out.append((level, float(min(1.0, max(0.0, openness))), float(min(1.0, max(-1.0, width)))))
        return out
    except Exception:
        return []
