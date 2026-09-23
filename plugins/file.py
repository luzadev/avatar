"""File e documenti: cerca con Spotlight, elenca cartelle, legge testo, PDF e Word."""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

HOME = Path.home()
MAX_CHARS = 6000
FOLDERS = {"desktop": HOME / "Desktop", "scrivania": HOME / "Desktop", "documenti": HOME / "Documents", "download": HOME / "Downloads",
           "immagini": HOME / "Pictures", "musica": HOME / "Music", "home": HOME}


def _folder(name: str) -> Path:
    n = (name or "").strip().lower()
    if n in FOLDERS:
        return FOLDERS[n]
    p = Path(name).expanduser()
    return p if p.exists() else HOME


def cerca(params: dict, ctx: dict) -> str:
    q = str(params.get("query", "")).strip()
    if not q:
        return "Errore: serve il campo 'query'."
    folder = _folder(str(params.get("cartella", "home")))
    kind = str(params.get("tipo", "")).lower()
    query = f'kMDItemFSName == "*{q}*"cd || kMDItemTextContent == "*{q}*"cd'
    if kind == "pdf":
        query = f'({query}) && kMDItemContentType == "com.adobe.pdf"'
    elif kind in ("immagine", "immagini"):
        query = f'({query}) && kMDItemContentTypeTree == "public.image"'
    elif kind in ("documento", "documenti"):
        query = f'({query}) && (kMDItemContentTypeTree == "public.text" || kMDItemContentType == "com.adobe.pdf" || kMDItemContentType == "org.openxmlformats.wordprocessingml.document")'
    res = subprocess.run(["mdfind", "-onlyin", str(folder), query], capture_output=True, text=True, timeout=30)
    paths = [Path(p) for p in res.stdout.splitlines() if p and "/Library/" not in p][:200]
    if not paths:
        return f"Nessun file trovato per '{q}' in {folder.name or 'home'}."
    paths.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    lines = [f"- {p.name} ({datetime.fromtimestamp(p.stat().st_mtime):%d/%m/%Y}) — {p.parent}" for p in paths[:12] if p.exists()]
    return f"Trovati {len(paths)} file per '{q}':\n" + "\n".join(lines)


def elenca(params: dict, ctx: dict) -> str:
    folder = _folder(str(params.get("cartella", "desktop")))
    items = sorted([p for p in folder.iterdir() if not p.name.startswith(".")], key=lambda p: p.stat().st_mtime, reverse=True)
    if not items:
        return f"La cartella {folder.name} è vuota."
    lines = [f"- {p.name}{'/' if p.is_dir() else ''} ({datetime.fromtimestamp(p.stat().st_mtime):%d/%m %H:%M})" for p in items[:25]]
    return f"{folder.name} ({len(items)} elementi, i più recenti):\n" + "\n".join(lines)


def _read(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:30])
    if ext == ".docx":
        import docx
        d = docx.Document(str(path))
        return "\n".join(p.text for p in d.paragraphs)
    if ext in (".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".rtf", ".log"):
        return path.read_text(encoding="utf-8", errors="replace")
    try:
        return subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


def leggi(params: dict, ctx: dict) -> str:
    name = str(params.get("percorso", "")).strip()
    if not name:
        return "Errore: serve il percorso o il nome del file."
    path = Path(name).expanduser()
    if not path.exists():
        res = subprocess.run(["mdfind", "-onlyin", str(HOME), f'kMDItemFSName == "*{path.name}*"cd'], capture_output=True, text=True, timeout=20)
        cands = [Path(p) for p in res.stdout.splitlines() if p and "/Library/" not in p]
        if not cands:
            return f"File non trovato: {name}"
        cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        path = cands[0]
    try:
        text = _read(path)
    except Exception as err:
        return f"Non riesco a leggere {path.name}: {err}"
    text = " ".join(text.split())
    if not text:
        return f"{path.name}: nessun testo estraibile."
    return f"{path.name} ({len(text)} caratteri):\n{text[:MAX_CHARS]}{' […]' if len(text) > MAX_CHARS else ''}"


TOOLS = [
    {"name": "file_cerca", "description": "Cerca file per nome o contenuto con Spotlight, in tutta la home o in una cartella (desktop, documenti, download…). Tipo: pdf, immagine, documento.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "cartella": {"type": "string"}, "tipo": {"type": "string"}}, "required": ["query"]}, "run": cerca},
    {"name": "file_elenca", "description": "Elenca i file di una cartella (desktop, documenti, download o un percorso), i più recenti prima.",
     "parameters": {"type": "object", "properties": {"cartella": {"type": "string"}}}, "run": elenca},
    {"name": "file_leggi", "description": "Legge il testo di un file (txt, md, PDF, Word, pagine, rtf) per riassumerlo o rispondere a domande. Accetta un percorso o solo il nome (lo cerca).",
     "parameters": {"type": "object", "properties": {"percorso": {"type": "string"}}, "required": ["percorso"]}, "run": leggi},
]
