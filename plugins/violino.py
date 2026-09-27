"""Maestro di violino: ascolta dal microfono, misura intonazione, ritmo, dinamica e pulizia del suono e riferisce
i dati al modello, che li trasforma in consigli. Analisi con librosa (pYIN), registrazioni salvate in data/violino/.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.settings import DATA_DIR  # noqa: E402

SR = 44100
OUT_DIR = DATA_DIR / "violino"
CORDE = {"Sol": 196.00, "Re": 293.66, "La": 440.00, "Mi": 659.25}
NOTE_IT = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]
NOTE_IDX = {"do": 0, "re": 2, "mi": 4, "fa": 5, "sol": 7, "la": 9, "si": 11}
SCALE_MAJ = [0, 2, 4, 5, 7, 9, 11]
SCALE_MIN = [0, 2, 3, 5, 7, 8, 10]


def _device():
    """Stesso microfono dell'assistente; vuoto = predefinito di sistema."""
    try:
        from memory import config_manager as cm
        d = cm.get_input_device()
        return d if d not in ("", None, "default") else None
    except Exception:
        return None


def _progress(ctx: dict, text: str) -> None:
    p = ctx.get("player")
    if p is not None and hasattr(p, "set_state"):
        try:
            p.set_state(f"PROCESSING · {text}")
        except Exception:
            pass


def registra(secondi: float, ctx: dict, attesa: float = 3.0) -> np.ndarray:
    """Registra dal microfono; prima un breve conto alla rovescia (LuZa sta ancora parlando)."""
    import sounddevice as sd
    for i in range(int(attesa), 0, -1):
        _progress(ctx, f"preparati · {i}")
        time.sleep(1)
    buf = sd.rec(int(secondi * SR), samplerate=SR, channels=1, dtype="float32", device=_device())
    t0 = time.time()
    while time.time() - t0 < secondi:
        _progress(ctx, f"ascolto il violino · {int(secondi - (time.time() - t0))} s")
        time.sleep(0.5)
    sd.wait()
    _progress(ctx, "analizzo")
    return buf[:, 0].copy()


def _nome(midi: int) -> str:
    return f"{NOTE_IT[midi % 12]}{midi // 12 - 1}"


def _scala(testo: str) -> set[int] | None:
    t = (testo or "").lower().strip()
    if not t:
        return None
    parts = t.replace("#", " #").replace("b", " b").split()
    if not parts or parts[0] not in NOTE_IDX:
        return None
    root = NOTE_IDX[parts[0]]
    if "#" in parts[1:2]:
        root += 1
    elif "b" in parts[1:2]:
        root -= 1
    minor = "min" in t
    return {(root + x) % 12 for x in (SCALE_MIN if minor else SCALE_MAJ)}


def analizza(y: np.ndarray, sr: int = SR, a4: float = 440.0, scala: str = "", fmin: float = 180.0, fmax: float = 2800.0) -> dict:
    import librosa
    y = y.astype(np.float32)
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    if db.max() < -45:
        return {"silenzio": True}
    f0, voiced, prob = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=sr, frame_length=2048, hop_length=hop)
    flat = librosa.feature.spectral_flatness(y=y, n_fft=2048, hop_length=hop)[0]
    n = min(len(f0), len(db), len(flat))
    f0, voiced, db, flat = f0[:n], voiced[:n], db[:n], flat[:n]
    from scipy.signal import medfilt
    ok = voiced & np.isfinite(f0) & (prob >= 0.6) & (db >= db.max() - 32)
    midi = np.where(ok, 69 + 12 * np.log2(np.maximum(np.nan_to_num(f0, nan=1.0), 1) / a4), np.nan)
    # stabilizza il contorno: mediana su 5 frame, poi segmentazione con isteresi (3 frame fuori dal ±60 cent per cambiare nota)
    sm = midi.copy()
    fin = np.isfinite(midi)
    if fin.sum() > 5:
        filled = np.interp(np.arange(n), np.flatnonzero(fin), midi[fin])
        sm = np.where(fin, medfilt(filled, 5), np.nan)
    segs, cur, gap, out_cnt = [], [], 0, 0
    for i in range(n):
        if np.isnan(sm[i]):
            gap += 1
            if gap >= 5 and cur:
                segs.append(cur); cur = []
            continue
        gap = 0
        if cur and abs(sm[i] - np.nanmedian(sm[cur[-10:]])) * 100 > 60:
            out_cnt += 1
            if out_cnt >= 3:
                segs.append(cur[:-2] if len(cur) > 2 else cur); cur = cur[-2:] + [i]; out_cnt = 0
                continue
        else:
            out_cnt = 0
        cur.append(i)
    if cur:
        segs.append(cur)
    # note: scarta i frammenti brevi, unisci segmenti contigui con la stessa nota
    notes = []
    for idx in segs:
        if len(idx) < 8:
            continue
        idx = np.array(idx)
        m = float(np.nanmedian(sm[idx]))
        cents_arr = (sm[idx] - round(m)) * 100
        note = {"start": idx[0] * hop / sr, "dur": len(idx) * hop / sr, "midi": int(round(m)), "midi_f": m, "cents": float(np.nanmedian(cents_arr)),
                "vib": float(np.nanstd(cents_arr)), "db": float(np.mean(db[idx])), "flat": float(np.mean(flat[idx]))}
        # stessa nota ripetuta: resta separata se tra le due il volume cala (nuovo attacco d'arco o di voce)
        gap_lo = int((notes[-1]["start"] + notes[-1]["dur"]) * sr / hop) if notes else 0
        dip = float(notes[-1]["db"] - db[gap_lo:idx[0] + 1].min()) if notes and idx[0] > gap_lo else 0.0
        if notes and notes[-1]["midi"] == note["midi"] and note["start"] - (notes[-1]["start"] + notes[-1]["dur"]) < 0.12 and dip < 8:
            prev = notes[-1]; w1, w2 = prev["dur"], note["dur"]
            prev["cents"] = (prev["cents"] * w1 + note["cents"] * w2) / (w1 + w2)
            prev["dur"] = note["start"] + note["dur"] - prev["start"]
            prev["vib"] = max(prev["vib"], note["vib"]); prev["flat"] = (prev["flat"] * w1 + note["flat"] * w2) / (w1 + w2)
            continue
        notes.append(note)
    if not notes:
        return {"silenzio": False, "note": [], "msg": "Ho sentito suono ma nessuna nota stabile: forse rumore o troppe corde insieme."}
    cents = np.array([x["cents"] for x in notes])
    durs = np.array([x["dur"] for x in notes])
    onsets = np.array([x["start"] for x in notes])
    ioi = np.diff(onsets) if len(onsets) > 2 else np.array([])
    sc = _scala(scala)
    fuori = [x for x in notes if sc is not None and x["midi"] % 12 not in sc]
    rep = {
        "silenzio": False, "n_note": len(notes), "durata": float(len(y) / sr),
        "intonazione_media_abs": float(np.mean(np.abs(cents))), "tendenza": float(np.mean(cents)),
        "note_intonate_pct": float(np.mean(np.abs(cents) <= 15) * 100),
        "peggiori": sorted(notes, key=lambda x: -abs(x["cents"]))[:3],
        "vibrato_note": int(sum(1 for x in notes if x["vib"] > 15 and x["dur"] > 0.35)),
        "ritmo_cv": float(np.std(ioi) / np.mean(ioi)) if len(ioi) >= 3 else None,
        "dinamica_range_db": float(np.max([x["db"] for x in notes]) - np.min([x["db"] for x in notes])),
        "graffio_pct": float(np.mean([x["flat"] > 0.25 for x in notes]) * 100),
        "note_corte_pct": float(np.mean(durs < 0.15) * 100),
        "fuori_scala": fuori, "scala": scala, "note": notes,
    }
    return rep


def _testo(rep: dict) -> str:
    if rep.get("silenzio"):
        return "Non ho sentito il violino: il microfono ha registrato quasi silenzio. Controlla la distanza (ideale 1-2 metri) e riprova."
    if not rep.get("note"):
        return rep.get("msg", "Nessuna nota rilevata.")
    seq = " ".join(f"{_nome(x['midi'])}({x['cents']:+.0f})" for x in rep["note"][:40])
    out = [f"Registrazione di {rep['durata']:.0f} s, {rep['n_note']} note rilevate (nome e scarto in cent, + = crescente, - = calante):",
           seq + (" …" if rep["n_note"] > 40 else ""),
           f"Intonazione: scarto medio {rep['intonazione_media_abs']:.0f} cent, {rep['note_intonate_pct']:.0f}% delle note entro 15 cent, tendenza {'crescente' if rep['tendenza'] > 5 else 'calante' if rep['tendenza'] < -5 else 'centrata'} ({rep['tendenza']:+.0f} cent).",
           "Note più stonate: " + ", ".join(f"{_nome(x['midi'])} {x['cents']:+.0f} cent a {x['start']:.1f} s" for x in rep["peggiori"]) + "."]
    if rep["ritmo_cv"] is not None:
        out.append(f"Ritmo: regolarità {'buona' if rep['ritmo_cv'] < 0.25 else 'discreta' if rep['ritmo_cv'] < 0.45 else 'instabile'} (variazione {rep['ritmo_cv'] * 100:.0f}% tra una nota e l'altra).")
    rng = rep["dinamica_range_db"]
    giudizio = "molto uniforme" if rng < 6 else "espressiva" if rng < 18 else "sbalzi forti: pressione dell'arco irregolare?"
    out.append(f"Dinamica: escursione {rng:.0f} dB ({giudizio}).")
    out.append(f"Suono: {rep['graffio_pct']:.0f}% delle note con rumore d'arco evidente (graffio o arco troppo leggero/veloce); note troppo brevi {rep['note_corte_pct']:.0f}%; vibrato su {rep['vibrato_note']} note.")
    if rep["scala"]:
        if rep["fuori_scala"]:
            out.append(f"Scala {rep['scala']}: note fuori scala: " + ", ".join(f"{_nome(x['midi'])} a {x['start']:.1f} s" for x in rep["fuori_scala"][:6]) + " (dita in posizione sbagliata?).")
        else:
            out.append(f"Scala {rep['scala']}: tutte le note appartengono alla scala.")
    out.append("Interpreta questi dati per l'allieva: complimenti sinceri prima, poi al massimo due consigli concreti.")
    return "\n".join(out)


def ascolta(params: dict, ctx: dict) -> str:
    secondi = max(5, min(int(params.get("secondi") or 15), 60))
    a4 = float(params.get("nota_la") or 440)
    y = registra(secondi, ctx)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.wav"
    try:
        import soundfile as sf
        sf.write(str(path), y, SR)
    except Exception:
        pass
    rep = analizza(y, SR, a4, str(params.get("scala") or ""))
    return _testo(rep) + (f"\nRegistrazione salvata: {path.name}" if path.exists() else "")


def accorda(params: dict, ctx: dict) -> str:
    import librosa
    y = registra(max(3, min(int(params.get("secondi") or 4), 10)), ctx, attesa=2)
    rms = float(np.sqrt(np.mean(y ** 2)))
    if 20 * math.log10(max(rms, 1e-6)) < -45:
        return "Non sento la corda: suonala a vuoto, con l'arco, vicino al microfono."
    f0, voiced, _ = librosa.pyin(y, fmin=150.0, fmax=1200.0, sr=SR)
    f = float(np.nanmedian(f0[voiced])) if np.any(voiced) else 0.0
    if not f:
        return "Non riesco a isolare la nota: suona una corda sola, a vuoto."
    a4 = float(params.get("nota_la") or 440)
    nome, target = min(CORDE.items(), key=lambda kv: abs(math.log2(f / (kv[1] * a4 / 440))))
    target *= a4 / 440
    cents = 1200 * math.log2(f / target)
    verdetto = "intonata" if abs(cents) <= 5 else ("crescente: allenta un po'" if cents > 0 else "calante: tira un po'")
    return f"Corda {nome}: {f:.1f} Hz, {cents:+.0f} cent rispetto a {target:.1f} Hz → {verdetto}. (Oltre ±30 cent usa i piroli, altrimenti i tiracantini.)"


TOOLS = [
    {"name": "violino_ascolta", "description": "Ascolta il violino dal microfono per alcuni secondi (default 15, max 60) e misura intonazione di ogni nota in cent, tendenza crescente/calante, regolarità del ritmo, dinamica, rumore d'arco e vibrato. Con scala (es. 'Sol maggiore', 'Re minore') segnala le note fuori scala. Prima di chiamarlo di' all'allieva che stai per ascoltare: ci sono 3 secondi di preparazione. Restituisce dati da trasformare in consigli.",
     "parameters": {"type": "object", "properties": {"secondi": {"type": "integer"}, "scala": {"type": "string"}, "nota_la": {"type": "number", "description": "frequenza del La di riferimento, 440 o 442"}}}, "run": ascolta},
    {"name": "violino_accorda", "description": "Accordatore: ascolta una corda a vuoto (Sol, Re, La, Mi) e dice di quanti cent è crescente o calante e come correggere.",
     "parameters": {"type": "object", "properties": {"secondi": {"type": "integer"}, "nota_la": {"type": "number"}}}, "run": accorda},
]
