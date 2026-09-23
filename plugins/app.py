"""Apre e chiude applicazioni, apre siti e file."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import osa  # noqa: E402


def apri(params: dict, ctx: dict) -> str:
    nome = str(params.get("nome", "")).strip()
    url = str(params.get("url", "")).strip()
    if url:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        cmd = ["open", url] if not nome else ["open", "-a", nome, url]
        subprocess.run(cmd, check=False, timeout=15)
        return f"Apro {url}" + (f" con {nome}." if nome else ".")
    if not nome:
        return "Errore: serve il nome dell'app o un indirizzo."
    p = Path(nome).expanduser()
    if p.exists():
        subprocess.run(["open", str(p)], check=False, timeout=15)
        return f"Apro {p.name}."
    res = subprocess.run(["open", "-a", nome], capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        return f"Non trovo un'app chiamata '{nome}'."
    return f"Apro {nome}."


def chiudi(params: dict, ctx: dict) -> str:
    nome = str(params.get("nome", "")).strip()
    if not nome:
        return "Errore: serve il nome dell'app."
    try:
        osa('on run argv\ntell application (item 1 of argv) to quit\nend run', nome, timeout=20)
        return f"Chiudo {nome}."
    except Exception as err:
        return f"Non riesco a chiudere {nome}: {err}"


def in_esecuzione(params: dict, ctx: dict) -> str:
    out = osa('tell application "System Events" to get name of every process whose background only is false')
    return "App aperte: " + out


TOOLS = [
    {"name": "app_apri", "description": "Apre un'applicazione (per nome, es. Safari, Xcode), un sito (url) o un file. Se dai sia app che url, apre l'url con quell'app.",
     "parameters": {"type": "object", "properties": {"nome": {"type": "string"}, "url": {"type": "string"}}}, "run": apri},
    {"name": "app_chiudi", "description": "Chiude un'applicazione per nome.",
     "parameters": {"type": "object", "properties": {"nome": {"type": "string"}}, "required": ["nome"]}, "run": chiudi},
    {"name": "app_aperte", "description": "Elenca le applicazioni aperte.", "parameters": {"type": "object", "properties": {}}, "run": in_esecuzione},
]
