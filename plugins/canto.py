"""Maestra di canto: ascolta la voce, misura intonazione assoluta e relativa (intervalli), deriva di tonalità,
vibrato e sostegno, e propone l'esercizio "canta questa nota" con il suono di riferimento.
Riusa l'analizzatore del plugin violino (librosa/pYIN) con la gamma della voce.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar.settings import DATA_DIR  # noqa: E402
import violino  # noqa: E402

OUT_DIR = DATA_DIR / "canto"
FMIN, FMAX = 90.0, 1500.0


def _nota_midi(nome: str) -> int | None:
    """'La4', 'Do5', 'Fa#4' → numero MIDI."""
    t = (nome or "").strip().lower().replace(" ", "")
    for k in sorted(violino.NOTE_IDX, key=len, reverse=True):
        if t.startswith(k):
            rest = t[len(k):]
            alt = 0
            if rest.startswith("#"):
                alt, rest = 1, rest[1:]
            elif rest.startswith("b"):
                alt, rest = -1, rest[1:]
            try:
                octave = int(rest) if rest else 4
            except ValueError:
                return None
            return (octave + 1) * 12 + violino.NOTE_IDX[k] + alt
    return None


def _suona(midi: int, secondi: float = 1.5, a4: float = 440.0) -> None:
    import sounddevice as sd
    f = a4 * 2 ** ((midi - 69) / 12)
    t = np.linspace(0, secondi, int(violino.SR * secondi), endpoint=False)
    env = np.minimum(1, t / 0.05) * np.minimum(1, (secondi - t) / 0.2)
    tone = (0.5 * np.sin(2 * np.pi * f * t) + 0.15 * np.sin(4 * np.pi * f * t)) * env * 0.4
    sd.play(tone.astype(np.float32), violino.SR)
    sd.wait()


def _testo_canto(rep: dict) -> str:
    if rep.get("silenzio"):
        return "Non ho sentito la voce: canta un po' più forte o avvicinati al microfono (mezzo metro va bene)."
    notes = rep.get("note") or []
    if not notes:
        return rep.get("msg", "Nessuna nota stabile: prova con note tenute, una vocale aperta come 'a'.")
    cents = np.array([x["cents"] for x in notes])
    seq = " ".join(f"{violino._nome(x['midi'])}({x['cents']:+.0f})" for x in notes[:40])
    out = [f"Registrazione di {rep['durata']:.0f} s, {rep['n_note']} note (nome e scarto in cent):", seq + (" …" if len(notes) > 40 else "")]
    # intonazione assoluta e relativa
    ivl_err = []
    for a, b in zip(notes, notes[1:]):
        d = (b["midi_f"] - a["midi_f"]) * 100
        ivl_err.append(d - round(d / 100) * 100)
    ivl = np.abs(np.array(ivl_err)) if ivl_err else np.array([])
    out.append(f"Intonazione assoluta: scarto medio {np.mean(np.abs(cents)):.0f} cent, {np.mean(np.abs(cents) <= 20) * 100:.0f}% delle note entro 20 cent, tendenza {'crescente' if cents.mean() > 8 else 'calante' if cents.mean() < -8 else 'centrata'} ({cents.mean():+.0f} cent).")
    if len(ivl):
        out.append(f"Intonazione relativa (intervalli tra note consecutive): errore medio {ivl.mean():.0f} cent, {np.mean(ivl <= 25) * 100:.0f}% degli intervalli puliti; intervallo peggiore {ivl.max():.0f} cent.")
    # deriva di tonalità: regressione dei cent nel tempo
    if len(notes) >= 4:
        t = np.array([x["start"] for x in notes]); k = np.polyfit(t, cents, 1)[0]
        out.append(f"Deriva della tonalità: {k * 10:+.0f} cent ogni 10 secondi ({'stabile' if abs(k * 10) < 12 else 'scende piano piano' if k < 0 else 'sale piano piano'}).")
    lung = [x for x in notes if x["dur"] >= 0.6]
    if lung:
        vib = np.mean([x["vib"] for x in lung]); 
        out.append(f"Note tenute: {len(lung)}, oscillazione media {vib:.0f} cent ({'ferma' if vib < 10 else 'vibrato leggero' if vib < 25 else 'traballante: sostegno del fiato'}).")
    rng = rep["dinamica_range_db"]
    out.append(f"Volume: escursione {rng:.0f} dB {'(uniforme)' if rng < 6 else '(espressivo)' if rng < 18 else '(cali di fiato o note spinte)'}; note troppo brevi {rep['note_corte_pct']:.0f}%.")
    if rep.get("scala"):
        fuori = rep.get("fuori_scala") or []
        out.append(f"Tonalità {rep['scala']}: " + ("tutte le note appartengono alla scala." if not fuori else "note fuori: " + ", ".join(f"{violino._nome(x['midi'])} a {x['start']:.1f} s" for x in fuori[:6]) + "."))
    out.append("Interpreta per l'allieva: complimento specifico, poi al massimo due consigli (ascolta la nota prima di cantarla, fiato dal diaframma, vocale aperta), e un esercizio breve.")
    return "\n".join(out)


def ascolta(params: dict, ctx: dict) -> str:
    secondi = max(5, min(int(params.get("secondi") or 15), 60))
    a4 = float(params.get("nota_la") or 440)
    y = violino.registra(secondi, ctx)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.wav"
    try:
        import soundfile as sf
        sf.write(str(path), y, violino.SR)
    except Exception:
        pass
    rep = violino.analizza(y, violino.SR, a4, str(params.get("tonalita") or ""), FMIN, FMAX)
    return _testo_canto(rep) + (f"\nRegistrazione salvata: {path.name}" if path.exists() else "")


def nota(params: dict, ctx: dict) -> str:
    """Fa sentire una nota e ascolta se viene ricantata intonata."""
    import librosa
    nome = str(params.get("nota") or "La4")
    midi = _nota_midi(nome)
    if midi is None:
        return f"Nota '{nome}' non riconosciuta: usa nomi come Do4, Re4, Mi4, Fa#4, La4, Do5."
    a4 = float(params.get("nota_la") or 440)
    violino._progress(ctx, f"suono la nota {violino._nome(midi)}")
    _suona(midi, 1.5, a4)
    y = violino.registra(max(2, min(int(params.get("secondi") or 3), 8)), ctx, attesa=1)
    if 20 * math.log10(max(float(np.sqrt(np.mean(y ** 2))), 1e-6)) < -45:
        return "Non ho sentito la voce. Riproviamo: canta subito dopo il suono, tenendo la nota."
    f0, voiced, prob = librosa.pyin(y, fmin=FMIN, fmax=FMAX, sr=violino.SR)
    ok = voiced & (prob >= 0.6)
    if not np.any(ok):
        return "Ho sentito qualcosa ma non una nota tenuta: prova con una 'a' aperta e ferma."
    m = 69 + 12 * np.log2(np.nanmedian(f0[ok]) / a4)
    diff = (m - midi) * 100
    ottava = ""
    if abs(diff) > 700:   # cantata all'ottava (voce di bambina o maschile)
        diff = diff - round(diff / 1200) * 1200; ottava = " (in un'altra ottava, va bene)"
    giudizio = "perfetta" if abs(diff) <= 15 else "quasi: un pelo " + ("alta" if diff > 0 else "bassa") if abs(diff) <= 40 else ("decisamente alta" if diff > 0 else "decisamente bassa")
    return f"Nota richiesta {violino._nome(midi)}, cantata a {diff:+.0f} cent{ottava}: {giudizio}. Stabilità {np.nanstd((69 + 12 * np.log2(f0[ok] / a4) - midi) * 100):.0f} cent."


TOOLS = [
    {"name": "canto_ascolta", "description": "Ascolta la voce dal microfono per alcuni secondi (default 15, max 60) e misura intonazione assoluta e relativa (intervalli tra le note), deriva della tonalità, note tenute e vibrato, volume e fiato. Con tonalita (es. 'Do maggiore') segnala le note fuori scala. Annuncia prima che stai per ascoltare: ci sono 3 secondi di preparazione. Restituisce dati da trasformare in consigli.",
     "parameters": {"type": "object", "properties": {"secondi": {"type": "integer"}, "tonalita": {"type": "string"}, "nota_la": {"type": "number"}}}, "run": ascolta},
    {"name": "canto_nota", "description": "Esercizio 'canta questa nota': fa sentire una nota di riferimento dalle casse (es. La4, Do5, Mi4) per 1,5 secondi, poi ascolta 3 secondi e dice se è stata cantata intonata, quanto alta o bassa e quanto ferma.",
     "parameters": {"type": "object", "properties": {"nota": {"type": "string"}, "secondi": {"type": "integer"}, "nota_la": {"type": "number"}}}, "run": nota},
]
