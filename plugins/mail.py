"""Mail di macOS: posta non letta, ricerca, lettura (dall'indice di Mail) e invio (AppleScript)."""
from __future__ import annotations

import html
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

MAIL_DIR = Path.home() / "Library" / "Mail"
INDEX = MAIL_DIR / "V10" / "MailData" / "Envelope Index"
MAX_BODY = 1800

SELECT = """
SELECT m.ROWID, COALESCE(a.comment, ''), COALESCE(a.address, ''), COALESCE(s.subject, ''),
       m.date_received, m.read, mb.url, COALESCE(su.summary, '')
FROM messages m
JOIN mailboxes mb ON m.mailbox = mb.ROWID
LEFT JOIN addresses a ON m.sender = a.ROWID
LEFT JOIN subjects s ON m.subject = s.ROWID
LEFT JOIN summaries su ON m.summary = su.ROWID
WHERE m.deleted = 0 AND (mb.url LIKE '%/INBOX' OR mb.url LIKE '%/Inbox')
"""


def _query(where: str, params: tuple, limit: int) -> list[dict]:
    if not INDEX.exists():
        raise RuntimeError("Indice di Mail non trovato: l'app Mail è configurata su questo Mac?")
    con = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute(f"{SELECT} {where} ORDER BY m.date_received DESC LIMIT ?", (*params, limit)).fetchall()
    finally:
        con.close()
    out = []
    for rid, name, addr, subj, ts, read, url, summary in rows:
        who = f"{name} <{addr}>" if name and addr and name != addr else (name or addr or "?")
        out.append({"id": rid, "da": who, "oggetto": subj or "(senza oggetto)", "data": datetime.fromtimestamp(ts or 0),
                    "letta": bool(read), "url": url, "anteprima": " ".join(str(summary).split())[:120]})
    return out


def _fmt(rows: list[dict], preview: bool = False) -> str:
    lines = []
    for r in rows:
        line = f"- [{r['id']}] {'' if r['letta'] else '● '}{r['data']:%d/%m %H:%M} — {r['da']}: {r['oggetto']}"
        if preview and r["anteprima"]:
            line += f"\n    {r['anteprima']}"
        lines.append(line)
    return "\n".join(lines)


def non_lette(params: dict, ctx: dict) -> str:
    lim = max(1, min(int(params.get("limite") or 10), 30))
    rows = _query("AND m.read = 0", (), lim)
    if not rows:
        return "Nessuna email non letta nella posta in arrivo."
    return f"Email non lette (le più recenti, max {lim}):\n{_fmt(rows, preview=True)}\nPer leggerne una usa mail_leggi con l'id tra parentesi."


def cerca(params: dict, ctx: dict) -> str:
    q = str(params.get("query", "")).strip()
    lim = max(1, min(int(params.get("limite") or 10), 30))
    if not q:
        return "Errore: serve il campo 'query'."
    like = f"%{q}%"
    rows = _query("AND (s.subject LIKE ? OR a.address LIKE ? OR a.comment LIKE ?)", (like, like, like), lim)
    return f"Risultati per '{q}':\n{_fmt(rows)}\nPer leggerne una usa mail_leggi con l'id." if rows else f"Nessuna email trovata per '{q}'."


# ── Lettura dal file .emlx ────────────────────────────────────────────────────
def _mailbox_dir(url: str) -> Path | None:
    m = re.match(r"^[a-z]+://([^/]+)/(.+)$", url)
    if not m:
        return None
    account, path = m.group(1), m.group(2)
    d = MAIL_DIR / "V10" / account
    for part in path.split("/"):
        d = d / f"{part}.mbox"
    return d if d.exists() else None


def _find_emlx(rid: int, url: str) -> Path | None:
    base = _mailbox_dir(url)
    if base is None:
        return None
    digits = list(str(rid // 1000))[::-1] if rid >= 1000 else []
    for sub in base.iterdir():
        if not sub.is_dir():
            continue
        data = sub / "Data"
        cand = data.joinpath(*digits) / "Messages" if digits else data / "Messages"
        for name in (f"{rid}.emlx", f"{rid}.partial.emlx"):
            p = cand / name
            if p.exists():
                return p
    for p in base.rglob(f"{rid}*.emlx"):
        return p
    return None


def _emlx_message(path: Path):
    import email
    from email import policy
    data = path.read_bytes()
    nl = data.find(b"\n")
    try:
        n = int(data[:nl].strip())
        raw = data[nl + 1:nl + 1 + n]
    except ValueError:
        raw = data
    return email.message_from_bytes(raw, policy=policy.default)


def _body_text(msg) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        part = None
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:
        return ""
    if part.get_content_type() == "text/html":
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text, flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return " ".join(text.split())


def leggi(params: dict, ctx: dict) -> str:
    mid = str(params.get("id", "")).strip()
    if not mid.isdigit():
        return "Errore: serve l'id numerico del messaggio preso dall'elenco."
    rows = _query("AND m.ROWID = ?", (int(mid),), 1)
    if not rows:
        return "Messaggio non trovato."
    r = rows[0]
    path = _find_emlx(r["id"], r["url"])
    body = ""
    if path is not None:
        try:
            body = _body_text(_emlx_message(path))
        except Exception as err:
            body = f"(testo non leggibile: {err})"
    if not body:
        body = r["anteprima"] or "(testo non disponibile: il messaggio non è ancora scaricato in locale)"
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + " […]"
    return f"Da: {r['da']}\nOggetto: {r['oggetto']}\nData: {r['data']:%d/%m/%Y %H:%M}\n\n{body}"


# ── Invio via AppleScript ─────────────────────────────────────────────────────
SEND_SCRIPT = '''
on run argv
  set addr to item 1 of argv
  set subj to item 2 of argv
  set body to item 3 of argv
  tell application "Mail"
    set msg to make new outgoing message with properties {subject:subj, content:body, visible:false}
    tell msg to make new to recipient at end of to recipients with properties {address:addr}
    send msg
  end tell
  return "ok"
end run
'''


def _osa(script: str, *args: str, timeout: int = 60) -> str:
    res = subprocess.run(["osascript", "-e", script, "--", *args], capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        err = (res.stderr or "").strip()
        if "-1743" in err:
            raise RuntimeError("Accesso a Mail negato: concedilo in Impostazioni di Sistema > Privacy e sicurezza > Automazione.")
        raise RuntimeError(err.splitlines()[-1] if err else "errore AppleScript")
    return res.stdout.rstrip("\n")


def invia(params: dict, ctx: dict) -> str:
    a = str(params.get("a", "")).strip()
    oggetto = str(params.get("oggetto", "")).strip()
    testo = str(params.get("testo", "")).strip()
    if "@" not in a or not oggetto or not testo:
        return "Errore: servono destinatario (indirizzo email), oggetto e testo."
    desc = f"A: {a}\nOggetto: {oggetto}\n{testo[:160]}{'…' if len(testo) > 160 else ''}"

    def do() -> str:
        _osa(SEND_SCRIPT, a, oggetto, testo)
        msg = f"Email inviata a {a} con oggetto «{oggetto}»."
        if ctx.get("say"):
            ctx["say"](msg)
        return msg

    confirm = ctx.get("confirm")
    if confirm:
        return confirm("mail_invia", "Inviare questa email?", desc, do)
    if str(params.get("confermato", "")).lower() in ("true", "1", "sì", "si", "yes"):
        return do()
    return f"Prima di inviare chiedi conferma all'utente leggendo destinatario, oggetto e testo:\n{desc}\nPoi richiama con confermato=true."


TOOLS = [
    {"name": "mail_non_lette",
     "description": "Elenca le email non lette nella posta in arrivo di Mail (mittente, oggetto, data, anteprima, id). Usalo per 'ho email nuove?', 'leggimi la posta'.",
     "parameters": {"type": "object", "properties": {"limite": {"type": "integer", "description": "Quante al massimo (default 10)."}}},
     "run": non_lette},
    {"name": "mail_cerca",
     "description": "Cerca email nella posta in arrivo per parola nell'oggetto o nel mittente (nome o indirizzo).",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limite": {"type": "integer"}}, "required": ["query"]},
     "run": cerca},
    {"name": "mail_leggi",
     "description": "Legge il contenuto di un'email dato il suo id (dall'elenco). Riassumi il testo a voce, non leggerlo integralmente se è lungo.",
     "parameters": {"type": "object", "properties": {"id": {"type": "string", "description": "Id numerico del messaggio."}}, "required": ["id"]},
     "run": leggi},
    {"name": "mail_invia",
     "description": "Invia un'email dall'account predefinito di Mail. Azione irreversibile: viene chiesta conferma sullo schermo; se lo strumento risponde con [CONFIRMATION_PENDING], di' all'utente di confermare sul pannello e non dire che è inviata. Scrivi tu il testo completo in italiano, con saluto e firma con il nome dell'utente.",
     "parameters": {"type": "object", "properties": {"a": {"type": "string", "description": "Indirizzo email del destinatario."}, "oggetto": {"type": "string"}, "testo": {"type": "string"},
                                                     "confermato": {"type": "boolean", "description": "Solo senza interfaccia: true dopo la conferma esplicita dell'utente."}},
                    "required": ["a", "oggetto", "testo"]},
     "run": invia},
]
