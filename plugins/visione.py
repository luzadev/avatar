"""Visione: guarda dalla webcam, lo schermo o la finestra in primo piano (immagine + testo riconosciuto)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar import vision  # noqa: E402

NOTE = ("Descrivi ciò che vedi in modo naturale. Se il motore non può vedere le immagini, basati sul testo riconosciuto.")


def _result(path: Path, label: str, ctx: dict) -> str:
    text = vision.ocr_text(path)
    try:
        ui = ctx.get("player")
        if ui is not None and hasattr(ui, "show_camera_frame"):
            ui.show_camera_frame(path.read_bytes())
    except Exception:
        pass
    return (f"{label}\nIMMAGINE: {path}\n"
            f"Testo riconosciuto nell'immagine:\n{text or '(nessun testo)'}\n{NOTE}")


def webcam(params: dict, ctx: dict) -> str:
    path = vision.capture_webcam()
    return _result(path, "Fotogramma dalla webcam (l'utente e il suo ambiente).", ctx)


def schermo(params: dict, ctx: dict) -> str:
    path = vision.capture_screen(bool(params.get("tutti_gli_schermi")))
    return _result(path, "Cattura dello schermo del Mac (il computer dell'utente, non una foto dell'utente).", ctx)


def finestra(params: dict, ctx: dict) -> str:
    path, name = vision.capture_front_window()
    return _result(path, f"Cattura della finestra in primo piano: {name or 'sconosciuta'}.", ctx)


TOOLS = [
    {"name": "guarda_webcam", "description": "Scatta un fotogramma dalla webcam per vedere l'utente e l'ambiente attorno a lui. Usalo per 'cosa vedi?', 'guarda questo', 'come sto?'.",
     "parameters": {"type": "object", "properties": {}}, "run": webcam},
    {"name": "guarda_schermo", "description": "Cattura lo schermo del Mac per vedere cosa c'è aperto: 'cosa c'è sullo schermo?', 'leggi questo errore', 'riassumi questa pagina'.",
     "parameters": {"type": "object", "properties": {"tutti_gli_schermi": {"type": "boolean"}}}, "run": schermo},
    {"name": "guarda_finestra", "description": "Cattura solo la finestra dell'app in primo piano (più leggibile dello schermo intero).",
     "parameters": {"type": "object", "properties": {}}, "run": finestra},
]
