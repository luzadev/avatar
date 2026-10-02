"""Spotify: cerca e riproduce brani, album, artisti e playlist nell'app Spotify del Mac, controlla la riproduzione e
mostra il titolo del brano in corso sul pannello LED iPIXEL a ogni cambio.

Ricerca: API Web di Spotify con chiavi gratuite (developer.spotify.com, "client credentials", nessun login),
impostate nella scheda Strumenti. Senza chiavi: accetta link/URI Spotify oppure apre la ricerca nell'app.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.settings import Settings  # noqa: E402

_tok = {"v": "", "exp": 0.0}


def _osa(script: str, timeout: int = 15) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "-1743" in err:
            raise RuntimeError("Accesso a Spotify negato: consentilo in Impostazioni di Sistema > Privacy e sicurezza > Automazione.")
        raise RuntimeError(err.splitlines()[-1] if err else "errore AppleScript")
    return r.stdout.strip()


def _play(uri: str) -> None:
    """play track con un nuovo tentativo: a volte Spotify impiega molto a rispondere al primo comando."""
    for attempt in range(2):
        try:
            _osa(f'tell application "Spotify" to play track "{uri}"', timeout=30)
            return
        except subprocess.TimeoutExpired:
            if attempt:
                raise RuntimeError("Spotify non risponde: riprova tra poco")
            time.sleep(2)


def _running() -> bool:
    return _osa('tell application "System Events" to (name of processes) contains "Spotify"') == "true"


def _ensure_running() -> None:
    if not _running():
        subprocess.run(["open", "-g", "-a", "Spotify"])
        for _ in range(30):
            time.sleep(0.5)
            if _running():
                time.sleep(2); return


def _token() -> str:
    if _tok["v"] and time.time() < _tok["exp"]:
        return _tok["v"]
    s = Settings()
    cid, sec = str(s.get("spotify_client_id") or "").strip(), s.get_secret("spotify_client_secret")
    if not cid or not sec:
        return ""
    req = urllib.request.Request("https://accounts.spotify.com/api/token", data=b"grant_type=client_credentials", method="POST",
                                 headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode(), "Content-Type": "application/x-www-form-urlencoded"})
    d = json.load(urllib.request.urlopen(req, timeout=15))
    _tok.update(v=d["access_token"], exp=time.time() + int(d.get("expires_in", 3600)) - 60)
    return _tok["v"]


def _search(q: str, tipo: str) -> dict | None:
    tok = _token()
    if not tok:
        return None
    url = "https://api.spotify.com/v1/search?" + urllib.parse.urlencode({"q": q, "type": tipo, "limit": 5, "market": "IT"})
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"}), timeout=15))
    items = (d.get(tipo + "s") or {}).get("items") or []
    items = [i for i in items if i]
    return items[0] if items else None


def _uri_from_link(t: str) -> str:
    t = t.strip()
    if t.startswith("spotify:"):
        return t
    if "open.spotify.com/" in t:
        parts = urllib.parse.urlparse(t).path.strip("/").split("/")
        parts = [p for p in parts if not p.startswith("intl-")]
        if len(parts) >= 2:
            return f"spotify:{parts[0]}:{parts[1]}"
    return ""


def riproduci(params: dict, ctx: dict) -> str:
    q = str(params.get("cosa") or "").strip()
    tipo = str(params.get("tipo") or "track").lower()
    tipo = {"brano": "track", "canzone": "track", "album": "album", "artista": "artist", "playlist": "playlist"}.get(tipo, tipo)
    if tipo not in ("track", "album", "artist", "playlist"):
        tipo = "track"
    if not q:
        return "Errore: dimmi cosa riprodurre."
    _ensure_running()
    uri, desc = _uri_from_link(q), q
    if not uri:
        try:
            it = _search(q, tipo)
        except Exception as err:
            return f"Ricerca Spotify fallita: {err}"
        if it is None and _token() == "":
            subprocess.run(["open", f"spotify:search:{urllib.parse.quote(q)}"])
            return ("Ho aperto la ricerca in Spotify, ma per far partire la musica da sola mi servono le chiavi gratuite dell'API di Spotify "
                    "(scheda Strumenti delle impostazioni). Intanto puoi premere play sul primo risultato.")
        if it is None:
            return f"Non ho trovato «{q}» su Spotify."
        uri = it["uri"]
        artisti = ", ".join(a["name"] for a in it.get("artists", [])[:2])
        desc = f"{it['name']}" + (f" di {artisti}" if artisti else "") + ({"album": " (album)", "artist": " (artista)", "playlist": " (playlist)"}.get(tipo, ""))
    if uri.split(":")[1] in ("album", "artist", "playlist") and params.get("casuale"):
        _osa('tell application "Spotify" to set shuffling to true')
    _play(uri)
    _queue.clear()
    return f"In riproduzione su Spotify: {desc}."


def controllo(params: dict, ctx: dict) -> str:
    az = str(params.get("azione") or "").lower()
    cmds = {"play": "play", "riprendi": "play", "pausa": "pause", "stop": "pause", "avanti": "next track", "successivo": "next track",
            "indietro": "previous track", "precedente": "previous track"}
    if az in cmds:
        _osa(f'tell application "Spotify" to {cmds[az]}')
        time.sleep(0.6)
        return f"Fatto ({az}). " + (in_ascolto({}, ctx) if az not in ("pausa", "stop") else "")
    if az in ("volume",):
        v = max(0, min(int(params.get("valore") or 50), 100))
        _osa(f'tell application "Spotify" to set sound volume to {v}')
        return f"Volume di Spotify al {v}%."
    if az in ("casuale", "shuffle"):
        on = str(params.get("valore", "true")).lower() not in ("false", "0", "no")
        _osa(f'tell application "Spotify" to set shuffling to {"true" if on else "false"}')
        return "Riproduzione casuale " + ("attiva." if on else "disattivata.")
    return "Azioni: play, pausa, avanti, indietro, volume (valore 0-100), casuale."


def now_playing() -> dict | None:
    if not _running():
        return None
    out = _osa('tell application "Spotify" to if player state is playing then return (name of current track) & "|||" & (artist of current track) & "|||" & (id of current track)', timeout=8)
    if "|||" not in out:
        return None
    name, artist, tid = out.split("|||", 2)
    return {"titolo": name, "artista": artist, "id": tid}


def in_ascolto(params: dict, ctx: dict) -> str:
    n = now_playing()
    if not n:
        return "Spotify non sta suonando."
    return f"Sta suonando: {n['titolo']} di {n['artista']}."


# ── storico, coda e titolo sul pannello LED ──────────────────────────────────
from avatar.settings import DATA_DIR  # noqa: E402
ASCOLTI = DATA_DIR / "ascolti.jsonl"
_queue: list[tuple[str, str]] = []
_led = {"t": 0.0}      # (uri, descrizione) da suonare dopo il brano corrente


def _registra(n: dict) -> None:
    try:
        ASCOLTI.parent.mkdir(parents=True, exist_ok=True)
        with open(ASCOLTI, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M"), "titolo": n["titolo"], "artista": n["artista"], "id": n["id"]}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _position() -> tuple[float, float]:
    out = _osa('tell application "Spotify"\nset p to (get player position)\nset d to (get duration of current track)\nreturn (p as text) & "|" & (d as text)\nend tell', timeout=8)
    a, b = out.replace(",", ".").split("|")
    return float(a), float(b) / 1000.0


def _watch() -> None:
    last = ""
    while True:
        time.sleep(3)
        try:
            n = now_playing()
            if not n:
                if _led["t"]:
                    from avatar import ipixel
                    if ipixel.panel is not None:
                        ipixel.panel.clear_background()
                    _led["t"] = 0
                if _queue and _running() and _osa('tell application "Spotify" to player state', timeout=8) == "stopped":
                    uri, _d = _queue.pop(0); _play(uri)
                last = "" if not n else last
                continue
            from avatar import ipixel
            p = ipixel.panel
            if n["id"] != last:
                last = n["id"]
                _registra(n)
                _led["t"] = 0
            if p is not None and p.connected and p.settings.get("ipixel_musica", True) and time.time() - _led["t"] > 18:
                pos, dur = _position()
                data = ipixel.render_music(n["titolo"], n["artista"], pos, dur, p.size, 20)
                p.set_background(f"music:{n['id']}:{int(pos)}", data, True, 20)
                if _led.get("logged") != n["id"]:
                    _led["logged"] = n["id"]; print(f"[spotify-led] pannello: {n['titolo']} - {n['artista']}", flush=True)
                _led["t"] = time.time()
            if _queue:
                pos, dur = _position()
                if dur > 0 and pos >= dur - 3.5:
                    uri, _d = _queue.pop(0); time.sleep(max(0.0, dur - pos - 0.5)); _play(uri)
        except Exception as err:
            msg = str(err)[:200]
            if msg != _led.get("err"):
                _led["err"] = msg
                print(f"[spotify-led] {msg}", flush=True)


def gusti(params: dict, ctx: dict) -> str:
    """Riepilogo dei gusti per consigliare: storico degli ascolti + preferenze musicali in memoria."""
    righe = []
    try:
        lines = ASCOLTI.read_text(encoding="utf-8").splitlines()[-400:]
        items = [json.loads(l) for l in lines if l.strip()]
    except Exception:
        items = []
    if items:
        from collections import Counter
        art = Counter(i["artista"] for i in items); br = Counter(f"{i['titolo']} - {i['artista']}" for i in items)
        righe.append(f"Ascolti registrati: {len(items)} (dal {items[0]['ts'][:10]}).")
        righe.append("Artisti più ascoltati: " + ", ".join(f"{a} ({c})" for a, c in art.most_common(12)))
        righe.append("Brani più ascoltati: " + "; ".join(b for b, _ in br.most_common(10)))
        righe.append("Ultimi ascolti: " + "; ".join(f"{i['titolo']} - {i['artista']}" for i in items[-10:]))
    else:
        righe.append("Nessun ascolto registrato ancora (la registrazione parte ora).")
    try:
        from memory import memory_manager as mm
        r = mm.search_memory("musica dj genere artista brano ascolta producer", limit=8)
        if r:
            righe.append("Dalla memoria: " + " ".join(str(r).split())[:900])
    except Exception:
        pass
    righe.append("Ora proponi tu 5-10 brani reali e coerenti con questi gusti (mescola noti e scoperte, evita di ripetere i più ascoltati) "
                 "e, se l'utente vuole, falli partire con spotify_coda passando l'elenco 'Titolo - Artista'.")
    return "\n".join(righe)


def coda(params: dict, ctx: dict) -> str:
    """Cerca e mette in fila una lista di brani: parte il primo, gli altri seguono a fine brano."""
    raw = params.get("brani") or []
    if isinstance(raw, str):
        raw = [x for x in raw.replace(";", "\n").splitlines()]
    brani = [str(x).strip() for x in raw if str(x).strip()][:25]
    if not brani:
        return "Errore: serve l'elenco dei brani."
    _ensure_running()
    trovati, mancanti = [], []
    for b in brani:
        try:
            it = _search(b, "track")
        except Exception:
            it = None
        if it:
            trovati.append((it["uri"], f"{it['name']} - {', '.join(a['name'] for a in it.get('artists', [])[:2])}"))
        else:
            mancanti.append(b)
    if not trovati:
        return "Non ho trovato nessuno di questi brani su Spotify" + ("" if _token() else " (mancano le chiavi API)") + "."
    _queue.clear()
    _play(trovati[0][0])
    _queue.extend(trovati[1:])
    out = f"In riproduzione: {trovati[0][1]}. In coda: " + "; ".join(d for _u, d in trovati[1:]) if len(trovati) > 1 else f"In riproduzione: {trovati[0][1]}."
    return out + (f" Non trovati: {', '.join(mancanti)}." if mancanti else "")


if Path(sys.argv[0]).name != "memory_mcp.py":   # solo nell'app, non nel server MCP di Claude Code
    threading.Thread(target=_watch, daemon=True, name="spotify-led").start()


TOOLS = [
    {"name": "spotify_riproduci", "description": "Riproduce su Spotify un brano, album, artista o playlist cercandolo per nome (o da un link/URI Spotify). tipo: brano (default), album, artista, playlist. casuale=true per mescolare. Il titolo compare sul pannello LED.",
     "parameters": {"type": "object", "properties": {"cosa": {"type": "string"}, "tipo": {"type": "string", "enum": ["brano", "album", "artista", "playlist"]}, "casuale": {"type": "boolean"}}, "required": ["cosa"]}, "run": riproduci},
    {"name": "spotify_controllo", "description": "Controlla Spotify: play, pausa, avanti, indietro, volume (valore 0-100), casuale (valore true/false).",
     "parameters": {"type": "object", "properties": {"azione": {"type": "string"}, "valore": {"type": "string"}}, "required": ["azione"]}, "run": controllo},
    {"name": "spotify_gusti", "description": "Per consigliare musica: riepiloga i gusti dell'utente (artisti e brani più ascoltati su Spotify, ultimi ascolti, preferenze in memoria). Poi proponi tu brani adatti e, se vuole, falli partire con spotify_coda.",
     "parameters": {"type": "object", "properties": {}}, "run": gusti},
    {"name": "spotify_coda", "description": "Fa partire una sequenza di brani su Spotify: brani = elenco 'Titolo - Artista'. Parte il primo, gli altri seguono automaticamente a fine brano. Usalo per i consigli e le selezioni a tema (es. 'musica per cenare', 'qualcosa di simile a…').",
     "parameters": {"type": "object", "properties": {"brani": {"type": "array", "items": {"type": "string"}}}, "required": ["brani"]}, "run": coda},
    {"name": "spotify_in_ascolto", "description": "Dice quale brano sta suonando su Spotify.",
     "parameters": {"type": "object", "properties": {}}, "run": in_ascolto},
]
