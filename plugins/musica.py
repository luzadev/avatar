"""Musica: controlla Apple Music (o Spotify, se aperto): riproduci, pausa, brano, playlist, volume."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import osa  # noqa: E402


def _running(name: str) -> bool:
    return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0


def _app(launch: bool = False) -> str | None:
    """App musicale in uso: Spotify se è l'unica aperta, altrimenti Musica (avviata se launch=True)."""
    if _running("Spotify") and not _running("Music"):
        return "Spotify"
    if _running("Music"):
        return "Music"
    if launch:
        subprocess.run(["open", "-a", "Music"], check=False, timeout=15)
        for _ in range(20):
            if _running("Music"):
                time.sleep(2)
                return "Music"
            time.sleep(0.5)
    return None


def controllo(params: dict, ctx: dict) -> str:
    azione = str(params.get("azione", "")).lower()
    app = _app(launch=azione in ("play", "riproduci"))
    if app is None:
        return "Nessuna app musicale aperta: di' «riproduci» per avviare Musica, oppure apri Spotify."
    cmds = {"play": "play", "riproduci": "play", "pausa": "pause", "stop": "stop", "prossimo": "next track", "avanti": "next track",
            "precedente": "previous track", "indietro": "previous track"}
    if azione in cmds:
        osa(f'tell application "{app}" to {cmds[azione]}', timeout=20)
        return f"Fatto ({azione})."
    if azione in ("brano", "cosa suona", "info"):
        try:
            out = osa(f'tell application "{app}" to return (name of current track) & " — " & (artist of current track) & " (" & (album of current track) & ")"')
            stato = osa(f'tell application "{app}" to return player state as string')
            return f"{'In riproduzione' if stato == 'playing' else 'In pausa'}: {out}"
        except Exception:
            return "Nessun brano in riproduzione."
    if azione == "volume":
        v = max(0, min(100, int(params.get("valore") or 50)))
        osa(f'tell application "{app}" to set sound volume to {v}')
        return f"Volume musica al {v}%."
    return "Azione sconosciuta. Usa: riproduci, pausa, prossimo, precedente, brano, volume."


def riproduci(params: dict, ctx: dict) -> str:
    q = str(params.get("nome", "")).strip()
    tipo = str(params.get("tipo", "auto")).lower()
    if not q:
        return "Errore: serve il nome."
    app = _app(launch=True)
    if app == "Spotify":
        return "Con Spotify posso solo controllare la riproduzione, non cercare: avvia tu il brano e poi dimmi pausa, avanti, ecc."
    if tipo in ("playlist", "auto"):
        try:
            out = osa('on run argv\ntell application "Music"\nset p to first playlist whose name contains (item 1 of argv)\nplay p\nreturn name of p\nend tell\nend run', q)
            return f"Riproduco la playlist «{out}»."
        except Exception:
            if tipo == "playlist":
                return f"Nessuna playlist chiamata '{q}'."
    for prop in ("artist", "album", "name"):
        try:
            out = osa(f'''on run argv
tell application "Music"
  set trk to (first track of library playlist 1 whose {prop} contains (item 1 of argv))
  play trk
  return (name of trk) & " — " & (artist of trk)
end tell
end run''', q, timeout=60)
            return f"Riproduco: {out}."
        except Exception:
            continue
    return f"Non ho trovato '{q}' nella libreria di Musica."


TOOLS = [
    {"name": "musica_controllo", "description": "Controlla la musica: riproduci, pausa, prossimo, precedente, brano (cosa sta suonando), volume (0-100).",
     "parameters": {"type": "object", "properties": {"azione": {"type": "string", "enum": ["riproduci", "pausa", "prossimo", "precedente", "brano", "volume"]},
                                                     "valore": {"type": "integer", "description": "Per volume: 0-100."}}, "required": ["azione"]}, "run": controllo},
    {"name": "musica_riproduci", "description": "Cerca e riproduce una playlist, un artista, un album o un brano nella libreria di Apple Music.",
     "parameters": {"type": "object", "properties": {"nome": {"type": "string"}, "tipo": {"type": "string", "enum": ["auto", "playlist", "artista", "album", "brano"]}}, "required": ["nome"]},
     "run": riproduci},
]
