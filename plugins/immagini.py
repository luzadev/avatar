"""Generazione di immagini in locale con mflux (MLX su Apple Silicon): Z-Image Turbo, FLUX e altri.

Gira in un ambiente separato (.venv-mflux) come processo esterno, così la memoria viene liberata a fine generazione.
Il modello si sceglie nelle impostazioni (scheda Strumenti); il primo uso scarica i pesi da Hugging Face.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.settings import BASE_DIR, DATA_DIR, Settings  # noqa: E402

VENV = BASE_DIR / ".venv-mflux" / "bin"
OUT_DIR = DATA_DIR / "immagini"
# comando mflux, passi e guida consigliati per ciascuna famiglia
MODELLI = {
    "z-image-turbo": {"cmd": "mflux-generate-z-image-turbo", "steps": 8, "guidance": None, "default_repo": "mflux-community/z-image-turbo-mflux-q4"},
    "schnell": {"cmd": "mflux-generate", "steps": 4, "guidance": None, "default_repo": "dhairyashil/FLUX.1-schnell-mflux-4bit", "base": "schnell"},
    "dev": {"cmd": "mflux-generate", "steps": 20, "guidance": 3.5, "default_repo": "", "base": "dev"},
    "qwen": {"cmd": "mflux-generate-qwen", "steps": 20, "guidance": 4.0, "default_repo": ""},
}
FORMATI = {"quadrata": (1024, 1024), "orizzontale": (1280, 768), "verticale": (768, 1280), "schermo": (1536, 864), "piccola": (768, 768)}
_last: dict = {"path": ""}


def _cfg() -> tuple[str, str]:
    s = Settings()
    fam = str(s.get("immagini_famiglia") or "z-image-turbo")
    repo = str(s.get("immagini_modello") or "").strip() or MODELLI.get(fam, MODELLI["z-image-turbo"])["default_repo"]
    return fam, repo


def genera(params: dict, ctx: dict) -> str:
    prompt = str(params.get("descrizione", "")).strip()
    if not prompt:
        return "Errore: serve la descrizione dell'immagine (in inglese, dettagliata)."
    if not (VENV / "mflux-generate").exists():
        return "Generazione immagini non disponibile: manca l'ambiente .venv-mflux (uv venv .venv-mflux && uv pip install mflux)."
    fam, repo = _cfg()
    m = MODELLI.get(fam, MODELLI["z-image-turbo"])
    w, h = FORMATI.get(str(params.get("formato", "quadrata")).lower(), FORMATI["quadrata"])
    steps = int(params.get("passi") or m["steps"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:40].strip("-") or "immagine"
    out = OUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}.png"
    cmd = [str(VENV / m["cmd"]), "--prompt", prompt, "--steps", str(steps), "--width", str(w), "--height", str(h), "--output", str(out)]
    if repo:
        cmd += ["--model", repo]
        if m.get("base") and "/" in repo:
            cmd += ["--base-model", m["base"]]
    if m["guidance"] is not None:
        cmd += ["--guidance", str(params.get("guida") or m["guidance"])]
    if params.get("seme") is not None:
        cmd += ["--seed", str(int(params["seme"]))]
    if params.get("immagine_base"):
        cmd += ["--image-path", str(params["immagine_base"]), "--image-strength", str(params.get("forza") or 0.5)]
    if ctx.get("log"):
        ctx["log"](f"[immagini] {fam} {w}x{h} {steps} passi: {prompt[:80]}")
    t = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return "Generazione interrotta: troppo lenta (oltre 30 minuti)."
    if proc.returncode != 0 or not out.exists():
        err = (proc.stderr or proc.stdout).strip().splitlines()
        tail = " | ".join(err[-3:]) if err else f"codice {proc.returncode}"
        if "not found" in tail.lower() or "401" in tail or "403" in tail:
            tail += " (controlla il nome del modello nelle impostazioni, o accetta la licenza su Hugging Face)"
        return f"Generazione fallita: {tail[:400]}"
    _last["path"] = str(out)
    secs = time.time() - t
    if params.get("mostra", True) not in (False, "false", "no"):
        subprocess.Popen(["open", str(out)])
    return f"Immagine generata in {secs:.0f} s e salvata in {out} ({w}x{h}, {fam}).\nIMMAGINE: {out}"


def ultima(params: dict, ctx: dict) -> str:
    files = sorted(OUT_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True) if OUT_DIR.exists() else []
    if not files:
        return "Nessuna immagine generata finora."
    n = max(1, min(int(params.get("numero") or 5), 20))
    if str(params.get("apri", "")).lower() in ("true", "1", "sì", "si"):
        subprocess.Popen(["open", str(files[0])])
    return "Ultime immagini:\n" + "\n".join(f"- {p.name}" for p in files[:n]) + f"\nIMMAGINE: {files[0]}"


TOOLS = [
    {"name": "immagine_genera", "description": "Genera un'immagine in locale (MLX) da una descrizione. Scrivi la descrizione in INGLESE, dettagliata (soggetto, ambiente, luce, stile, inquadratura), anche se l'utente parla italiano. formato: quadrata (default), orizzontale, verticale, schermo, piccola. L'immagine viene salvata e aperta in Anteprima; richiede 10-60 secondi, il primo uso scarica il modello. Con immagine_base (percorso) e forza (0-1) parte da un'immagine esistente.",
     "parameters": {"type": "object", "properties": {"descrizione": {"type": "string"}, "formato": {"type": "string", "enum": ["quadrata", "orizzontale", "verticale", "schermo", "piccola"]},
                    "passi": {"type": "integer"}, "seme": {"type": "integer"}, "immagine_base": {"type": "string"}, "forza": {"type": "number"}, "mostra": {"type": "boolean"}},
                    "required": ["descrizione"]}, "run": genera},
    {"name": "immagine_ultime", "description": "Elenca le ultime immagini generate (cartella data/immagini); con apri=true apre la più recente.",
     "parameters": {"type": "object", "properties": {"numero": {"type": "integer"}, "apri": {"type": "boolean"}}}, "run": ultima},
]
