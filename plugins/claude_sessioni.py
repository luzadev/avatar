"""Sessioni di Claude Code (come l'app GestioneClaude): progetti e sessioni da ~/.claude*/projects, lettura,
ricerca, ripresa nel Terminale e continuazione diretta da LuZa (claude -p --resume) con permessi a scelta.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import confirm_or_param, CONFERMATO  # noqa: E402
from avatar.engines.claude_code import resolve_claude, claude_env  # noqa: E402

HOME = Path.home()
_cache: dict = {"t": 0.0, "sessions": []}
LIVELLI = {
    "lettura": {"tools": "Read,Glob,Grep,WebSearch,WebFetch", "allowed": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"], "mode": None},
    "modifica": {"tools": "default", "allowed": ["Bash", "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep", "WebSearch", "WebFetch", "Agent"], "mode": "acceptEdits"},
}


def _roots() -> list[Path]:
    out = []
    for d in sorted(HOME.iterdir()):
        if d.name.startswith(".claude") and (d / "projects").is_dir():
            out.append(d)
    return out


def _decode_dir(name: str) -> str:
    return name.replace("-", "/") if name.startswith("-") else name


def _text_of(msg) -> str:
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _head(path: Path) -> dict:
    """cwd, primo prompt e data dalle prime righe del file."""
    cwd = prompt = ts = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 80 or (cwd and prompt and ts):
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                cwd = cwd or o.get("cwd")
                ts = ts or o.get("timestamp")
                if not prompt and o.get("type") == "user" and not o.get("isSidechain"):
                    t = _text_of(o.get("message")).strip()
                    if t and not t.startswith(("<command-", "<local-command", "Caveat:", "[Request interrupted")):
                        prompt = " ".join(t.split())[:160]
    except Exception:
        pass
    return {"cwd": cwd, "prompt": prompt, "ts": ts}


def scan(force: bool = False) -> list[dict]:
    if not force and time.time() - _cache["t"] < 60 and _cache["sessions"]:
        return _cache["sessions"]
    out = []
    for root in _roots():
        for pdir in (root / "projects").iterdir():
            if not pdir.is_dir():
                continue
            for f in pdir.glob("*.jsonl"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                if st.st_size < 200:
                    continue
                h = _head(f)
                out.append({"id": f.stem, "file": f, "root": root.name, "cwd": h["cwd"] or _decode_dir(pdir.name), "prompt": h["prompt"] or "(senza prompt)",
                            "start": h["ts"], "mtime": st.st_mtime, "size": st.st_size})
    out.sort(key=lambda s: -s["mtime"])
    _cache.update(t=time.time(), sessions=out)
    return out


def _projects(sessions: list[dict]) -> list[dict]:
    by: dict[str, dict] = {}
    for s in sessions:
        p = by.setdefault(s["cwd"], {"path": s["cwd"], "name": Path(s["cwd"]).name or s["cwd"], "n": 0, "last": 0, "size": 0, "roots": set(), "exists": Path(s["cwd"]).is_dir()})
        p["n"] += 1; p["last"] = max(p["last"], s["mtime"]); p["size"] += s["size"]; p["roots"].add(s["root"])
    return sorted(by.values(), key=lambda p: -p["last"])


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def _fmt_dt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")


def _find_project(nome: str, sessions: list[dict]) -> dict | None:
    n = _norm(nome)
    if not n:
        return None
    projs = _projects(sessions)
    for p in projs:
        if _norm(p["name"]) == n or _norm(p["path"]) == n:
            return p
    for p in projs:
        if n in _norm(p["name"]) or n in _norm(p["path"]):
            return p
    return None


def _find_session(rif: str, progetto: str, sessions: list[dict]) -> dict | None:
    rif = (rif or "").strip().lower()
    pool = sessions
    if progetto:
        p = _find_project(progetto, sessions)
        if p:
            pool = [s for s in sessions if s["cwd"] == p["path"]]
    if not rif or rif in ("ultima", "recente", "last"):
        return pool[0] if pool else None
    for s in pool:
        if s["id"].lower() == rif or s["id"].lower().startswith(rif):
            return s
    for s in pool:
        if rif in s["prompt"].lower():
            return s
    return None


def progetti(params: dict, ctx: dict) -> str:
    sessions = scan(force=True)
    filtro = _norm(str(params.get("filtro") or ""))
    projs = [p for p in _projects(sessions) if not filtro or filtro in _norm(p["name"]) or filtro in _norm(p["path"])]
    if not projs:
        return "Nessun progetto Claude Code trovato."
    lim = max(3, min(int(params.get("limite") or 20), 60))
    lines = [f"- {p['name']} — {p['n']} sessioni, ultima {_fmt_dt(p['last'])}, {p['size'] / 1e6:.1f} MB{'' if p['exists'] else ' (cartella non più presente)'} [{p['path']}]" for p in projs[:lim]]
    return f"Progetti Claude Code ({len(projs)}, {len(sessions)} sessioni in {len(_roots())} profili):\n" + "\n".join(lines)


def sessioni(params: dict, ctx: dict) -> str:
    sessions = scan()
    nome = str(params.get("progetto") or "")
    p = _find_project(nome, sessions) if nome else None
    if nome and not p:
        return f"Progetto '{nome}' non trovato. Usa claude_progetti per l'elenco."
    pool = [s for s in sessions if not p or s["cwd"] == p["path"]]
    lim = max(3, min(int(params.get("numero") or 10), 40))
    lines = [f"- {s['id'][:8]} · {_fmt_dt(s['mtime'])} · {s['size'] / 1e3:.0f} KB · {s['prompt'][:90]}" + ("" if p else f" [{Path(s['cwd']).name}]") for s in pool[:lim]]
    return (f"Sessioni di {p['name']}" if p else "Sessioni recenti") + f" ({len(pool)}):\n" + "\n".join(lines) + "\nRiferisciti a una sessione con le prime cifre dell'id o con 'ultima'."


def _messages(path: Path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("isSidechain") or o.get("type") not in ("user", "assistant"):
                continue
            t = _text_of(o.get("message")).strip()
            if not t or t.startswith(("<command-", "<local-command", "Caveat:")):
                continue
            yield o["type"], t, o.get("timestamp", "")


def leggi(params: dict, ctx: dict) -> str:
    sessions = scan()
    s = _find_session(str(params.get("sessione") or ""), str(params.get("progetto") or ""), sessions)
    if not s:
        return "Sessione non trovata: indica progetto e le prime cifre dell'id (vedi claude_sessioni)."
    n = max(4, min(int(params.get("numero") or 20), 80))
    per = max(200, min(int(params.get("caratteri") or 700), 3000))
    msgs = list(_messages(s["file"]))
    parte = msgs[:n] if str(params.get("parte") or "fine") == "inizio" else msgs[-n:]
    out = [f"Sessione {s['id'][:8]} di {Path(s['cwd']).name} ({len(msgs)} messaggi, {'inizio' if parte is msgs[:n] else 'fine'}):"]
    for role, t, ts in parte:
        out.append(f"[{'Utente' if role == 'user' else 'Claude'} {ts[11:16] if ts else ''}] {t[:per]}{'…' if len(t) > per else ''}")
    return "\n".join(out)


def cerca(params: dict, ctx: dict) -> str:
    q = str(params.get("query") or "").strip().lower()
    if not q:
        return "Errore: serve il testo da cercare."
    sessions = scan()
    p = _find_project(str(params.get("progetto") or ""), sessions) if params.get("progetto") else None
    hits, checked = [], 0
    for s in sessions:
        if p and s["cwd"] != p["path"]:
            continue
        if s["size"] > 60_000_000:
            continue
        checked += 1
        try:
            for role, t, ts in _messages(s["file"]):
                if q in t.lower():
                    i = t.lower().find(q)
                    hits.append(f"- {Path(s['cwd']).name} · {s['id'][:8]} · {_fmt_dt(s['mtime'])} [{'Utente' if role == 'user' else 'Claude'}]: …{' '.join(t[max(0, i - 80):i + 120].split())}…")
                    if len([h for h in hits if s['id'][:8] in h]) >= 2:
                        break
        except Exception:
            continue
        if len(hits) >= 25:
            break
    if not hits:
        return f"Nessun risultato per '{q}' in {checked} sessioni."
    return f"Risultati per '{q}' ({len(hits)}):\n" + "\n".join(hits)


def _terminal(cmd: str) -> None:
    script = f'tell application "Terminal"\nactivate\ndo script {json.dumps(cmd)}\nend tell'
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=20)


def riprendi(params: dict, ctx: dict) -> str:
    sessions = scan()
    s = _find_session(str(params.get("sessione") or ""), str(params.get("progetto") or ""), sessions)
    if not s:
        return "Sessione non trovata."
    env = f"CLAUDE_CONFIG_DIR={json.dumps(str(HOME / s['root']))} " if s["root"] != ".claude" else ""
    _terminal(f"cd {json.dumps(s['cwd'])} && {env}claude --resume {s['id']}")
    return f"Aperto il Terminale nel progetto {Path(s['cwd']).name} con la sessione {s['id'][:8]} ripresa."


def nuova(params: dict, ctx: dict) -> str:
    sessions = scan()
    p = _find_project(str(params.get("progetto") or ""), sessions)
    path = p["path"] if p else str(Path(str(params.get("progetto") or "")).expanduser())
    if not Path(path).is_dir():
        return f"Cartella non trovata: {path}"
    root = next(iter(sorted(p["roots"])), ".claude") if p else ".claude"
    env = f"CLAUDE_CONFIG_DIR={json.dumps(str(HOME / root))} " if root != ".claude" else ""
    _terminal(f"cd {json.dumps(path)} && {env}claude")
    return f"Aperto il Terminale in {Path(path).name} con una nuova sessione di Claude Code."


def continua(params: dict, ctx: dict) -> str:
    """Manda una richiesta a Claude Code dentro una sessione esistente (o nuova) del progetto, senza Terminale."""
    sessions = scan()
    richiesta = str(params.get("richiesta") or "").strip()
    if not richiesta:
        return "Errore: serve la richiesta da fare a Claude."
    s = _find_session(str(params.get("sessione") or ""), str(params.get("progetto") or ""), sessions)
    p = _find_project(str(params.get("progetto") or ""), sessions) if params.get("progetto") else None
    if not s and not p:
        return "Indica la sessione (prime cifre dell'id o 'ultima') o il progetto."
    cwd = s["cwd"] if s else p["path"]
    root = s["root"] if s else next(iter(sorted(p["roots"])), ".claude")
    livello = "modifica" if str(params.get("permessi") or "lettura").lower().startswith("mod") else "lettura"
    binary = resolve_claude("")
    if not binary:
        return "Non trovo il comando claude."

    def do() -> str:
        flags = LIVELLI[livello]
        args = [binary, "-p", "--output-format", "json", "--tools", flags["tools"], "--allowedTools", ",".join(flags["allowed"])]
        if flags["mode"]:
            args += ["--permission-mode", flags["mode"]]
        args += ["--resume", s["id"]] if s else []
        player = ctx.get("player")
        if player is not None and hasattr(player, "set_state"):
            player.set_state(f"PROCESSING · Claude Code su {Path(cwd).name}")
        env = claude_env(str(HOME / root) if root != ".claude" else "")
        try:
            r = subprocess.run(args, input=richiesta, cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            return "Claude Code non ha risposto entro 15 minuti."
        try:
            d = json.loads(r.stdout.strip().splitlines()[-1])
            text = d.get("result") or ""
            sid = d.get("session_id", "")
            if d.get("is_error"):
                return f"Claude Code ha segnalato un errore: {text or r.stderr[-300:]}"
        except Exception:
            text, sid = (r.stdout or r.stderr)[-2000:], ""
        _cache["t"] = 0
        return f"Risposta di Claude Code ({Path(cwd).name}, sessione {(sid or (s['id'] if s else ''))[:8]}, permessi: {livello}):\n{text}"

    if livello == "modifica":
        return confirm_or_param(ctx, params, "claude_continua", f"Far modificare il progetto {Path(cwd).name} a Claude Code?", richiesta[:300], do)
    return do()


TOOLS = [
    {"name": "claude_progetti", "description": "Elenca i progetti con sessioni di Claude Code (tutti i profili ~/.claude*): numero di sessioni, ultima attività, spazio, percorso. Filtro opzionale per nome.",
     "parameters": {"type": "object", "properties": {"filtro": {"type": "string"}, "limite": {"type": "integer"}}}, "run": progetti},
    {"name": "claude_sessioni", "description": "Elenca le sessioni di Claude Code di un progetto (o le più recenti di tutti), con id abbreviato, data, dimensione e prima richiesta.",
     "parameters": {"type": "object", "properties": {"progetto": {"type": "string"}, "numero": {"type": "integer"}}}, "run": sessioni},
    {"name": "claude_leggi", "description": "Legge i messaggi di una sessione di Claude Code (utente e Claude), dalla fine o dall'inizio, per riassumerla o riprendere il filo. sessione = prime cifre dell'id oppure 'ultima' (con progetto).",
     "parameters": {"type": "object", "properties": {"sessione": {"type": "string"}, "progetto": {"type": "string"}, "numero": {"type": "integer"}, "parte": {"type": "string", "enum": ["fine", "inizio"]}, "caratteri": {"type": "integer"}}}, "run": leggi},
    {"name": "claude_cerca", "description": "Cerca un testo nei messaggi di tutte le sessioni di Claude Code (o di un progetto): utile per ritrovare dove si è parlato di qualcosa.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "progetto": {"type": "string"}}, "required": ["query"]}, "run": cerca},
    {"name": "claude_continua", "description": "Fa lavorare Claude Code su un progetto senza aprire il Terminale: manda la richiesta dentro una sessione esistente (sessione = prime cifre dell'id o 'ultima') o in una nuova sessione del progetto, e riporta la risposta. permessi=lettura (default: legge e cerca nel codice) oppure modifica (può cambiare file ed eseguire comandi: chiede conferma sullo schermo). Può richiedere minuti.",
     "parameters": {"type": "object", "properties": {"richiesta": {"type": "string"}, "sessione": {"type": "string"}, "progetto": {"type": "string"}, "permessi": {"type": "string", "enum": ["lettura", "modifica"]}, "confermato": CONFERMATO}, "required": ["richiesta"]}, "run": continua},
    {"name": "claude_riprendi", "description": "Apre il Terminale nel progetto e riprende una sessione di Claude Code (claude --resume) perché l'utente continui a mano.",
     "parameters": {"type": "object", "properties": {"sessione": {"type": "string"}, "progetto": {"type": "string"}}}, "run": riprendi},
    {"name": "claude_nuova", "description": "Apre il Terminale in un progetto e avvia una nuova sessione di Claude Code.",
     "parameters": {"type": "object", "properties": {"progetto": {"type": "string"}}, "required": ["progetto"]}, "run": nuova},
]
