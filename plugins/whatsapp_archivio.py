"""Archivio delle chat WhatsApp esportate (.txt o .zip): indice locale, ricerca, lettura per periodo, riepiloghi."""
from __future__ import annotations

import re
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.settings import DATA_DIR  # noqa: E402

ARCHIVE = DATA_DIR / "whatsapp"
DB = ARCHIVE / "index.sqlite"
MAX_CHARS = 12000
# iOS: [21/11/20, 13:56:41] Nome: testo  — Android: 21/11/20, 13:56 - Nome: testo
RE_IOS = re.compile(r"^‎?\[(\d{1,2}/\d{1,2}/\d{2,4}),? (\d{1,2}:\d{2}(?::\d{2})?)\] ([^:]{1,80}?): (.*)$")
RE_AND = re.compile(r"^‎?(\d{1,2}/\d{1,2}/\d{2,4}),? (\d{1,2}:\d{2}(?::\d{2})?) - ([^:]{1,80}?): (.*)$")
SKIP = ("crittografati end-to-end", "end-to-end encrypted")
MEDIA = {"immagine omessa": "[immagine]", "video omesso": "[video]", "audio omesso": "[audio]", "sticker omesso": "[sticker]",
         "GIF omessa": "[gif]", "documento omesso": "[documento]", "image omitted": "[immagine]", "video omitted": "[video]"}


def _parse_ts(d: str, t: str) -> datetime | None:
    for fmt in ("%d/%m/%y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(f"{d} {t}", fmt)
        except ValueError:
            continue
    return None


def parse(text: str):
    msgs = []
    for raw in text.splitlines():
        line = raw.rstrip("\r").replace("‎", "")
        m = RE_IOS.match(line) or RE_AND.match(line)
        if m:
            ts = _parse_ts(m.group(1), m.group(2))
            body = m.group(4).strip()
            if ts is None or any(s in body for s in SKIP):
                continue
            for k, v in MEDIA.items():
                if body == k:
                    body = v
            msgs.append([ts, m.group(3).strip(), body])
        elif msgs and line.strip():
            msgs[-1][2] += "\n" + line.strip()
    return msgs


def _db() -> sqlite3.Connection:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS chats (name TEXT PRIMARY KEY, file TEXT, mtime REAL, count INTEGER, first TEXT, last TEXT, participants TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, chat TEXT, ts TEXT, sender TEXT, text TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_chat_ts ON messages(chat, ts)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2')")
    con.execute("CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN INSERT INTO fts(rowid, text) VALUES (new.id, new.text); END")
    con.execute("CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN INSERT INTO fts(fts, rowid, text) VALUES ('delete', old.id, old.text); END")
    for col in ("source TEXT", "jid TEXT", "from_me INTEGER DEFAULT 0"):
        try:
            con.execute(f"ALTER TABLE messages ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _index_file(con: sqlite3.Connection, name: str, path: Path) -> int:
    msgs = parse(path.read_text(encoding="utf-8", errors="replace"))
    con.execute("DELETE FROM messages WHERE chat = ? AND (source IS NULL OR source = 'export')", (name,))
    con.executemany("INSERT INTO messages (chat, ts, sender, text, source) VALUES (?, ?, ?, ?, 'export')",
                    [(name, ts.strftime("%Y-%m-%d %H:%M:%S"), s, t) for ts, s, t in msgs])
    con.execute("INSERT INTO fts(fts) VALUES ('rebuild')")
    senders = sorted({s for _, s, _ in msgs}, key=lambda s: -sum(1 for _, x, _ in msgs if x == s))
    con.execute("INSERT OR REPLACE INTO chats VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, str(path), path.stat().st_mtime, len(msgs), msgs[0][0].strftime("%Y-%m-%d") if msgs else "",
                 msgs[-1][0].strftime("%Y-%m-%d") if msgs else "", ", ".join(senders[:6])))
    con.commit()
    return len(msgs)


def _sync(con: sqlite3.Connection) -> list[str]:
    """Indicizza i .txt nuovi o modificati presenti nella cartella archivio."""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    known = {r[0]: r for r in con.execute("SELECT name, file, mtime FROM chats")}
    done = []
    for path in sorted(ARCHIVE.glob("*.txt")):
        name = path.stem
        if name in known and abs(known[name][2] - path.stat().st_mtime) < 1:
            continue
        n = _index_file(con, name, path)
        done.append(f"{name} ({n} messaggi)")
    return done


def _chat_name(con: sqlite3.Connection, chat: str) -> str | None:
    names = [r[0] for r in con.execute("SELECT name FROM chats")]
    if not chat:
        return names[0] if len(names) == 1 else None
    q = chat.lower()
    return next((n for n in names if n.lower() == q), None) or next((n for n in names if q in n.lower()), None)


def _range(params: dict) -> tuple[str | None, str | None]:
    da, a = str(params.get("da", "")).strip(), str(params.get("a", "")).strip()
    today = datetime.now().date()
    alias = {"oggi": (today, today), "ieri": (today - timedelta(days=1), today - timedelta(days=1)),
             "settimana": (today - timedelta(days=7), today), "mese": (today - timedelta(days=30), today)}
    if da.lower() in alias:
        d1, d2 = alias[da.lower()]
        return d1.isoformat(), d2.isoformat() + " 23:59:59"
    return (da or None), ((a[:10] + " 23:59:59") if a else None)


def _fmt(rows) -> str:
    return "\n".join(f"[{ts[:16]}] {s}: {t}" for ts, s, t in rows)


def importa(params: dict, ctx: dict) -> str:
    src = Path(str(params.get("percorso", ""))).expanduser()
    nome = str(params.get("nome", "")).strip()
    if not src.exists():
        return f"Percorso non trovato: {src}"
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    txt: Path | None = None
    if src.is_dir():
        cands = list(src.glob("*.txt"))
        txt = next((c for c in cands if c.name == "_chat.txt"), cands[0] if cands else None)
    elif src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            members = [m for m in z.namelist() if m.endswith(".txt") and not m.startswith("__MACOSX") and not Path(m).name.startswith(".")]
            member = next((m for m in members if Path(m).name == "_chat.txt"), members[0] if members else None)
            if not member:
                return "Nello zip non c'è un file .txt."
            tmp = ARCHIVE / "_import.txt"
            tmp.write_bytes(z.read(member))
            txt = tmp
    else:
        txt = src
    if txt is None:
        return "Nessun file .txt trovato."
    con = _db()
    if not nome:
        # Gli zip di WhatsApp si chiamano "WhatsApp Chat - Nome.zip": usa quel nome.
        m = re.match(r"^WhatsApp Chat - (.+)$", src.stem, flags=re.I)
        if m:
            nome = m.group(1).strip()
    if not nome:
        msgs = parse(txt.read_text(encoding="utf-8", errors="replace"))
        senders = {}
        for _, s, _ in msgs:
            senders[s] = senders.get(s, 0) + 1
        top = sorted(senders, key=senders.get, reverse=True)
        nome = " & ".join(top[:2]) if len(top) > 1 else (top[0] if top else src.stem)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", nome).strip() or "chat"
    dest = ARCHIVE / f"{safe}.txt"
    if txt.resolve() != dest.resolve():
        shutil.copyfile(txt, dest)
    if (ARCHIVE / "_import.txt").exists():
        (ARCHIVE / "_import.txt").unlink()
    n = _index_file(con, safe, dest)
    con.close()
    return f"Chat importata come «{safe}»: {n} messaggi indicizzati."


def importa_cartella(params: dict, ctx: dict) -> str:
    folder = Path(str(params.get("percorso", ""))).expanduser()
    if not folder.is_dir():
        return f"Cartella non trovata: {folder}"
    files = sorted([p for p in folder.iterdir() if p.suffix.lower() in (".zip", ".txt") and not p.name.startswith(".")])
    if not files:
        return "Nessun file .zip o .txt nella cartella."
    results, errors = [], []
    for f in files:
        try:
            results.append(importa({"percorso": str(f)}, ctx))
        except Exception as err:
            errors.append(f"{f.name}: {err}")
    out = f"Importate {len(results)} chat:\n" + "\n".join("- " + r.replace("Chat importata come ", "") for r in results)
    if errors:
        out += "\nErrori:\n" + "\n".join("- " + e for e in errors)
    return out


def elenca(params: dict, ctx: dict) -> str:
    con = _db()
    _sync(con)
    rows = con.execute("SELECT name, count, first, last, participants FROM chats ORDER BY name").fetchall()
    con.close()
    if not rows:
        return f"Archivio vuoto. Esporta una chat da WhatsApp e importala con whatsapp_importa, oppure copia il .txt in {ARCHIVE}."
    return "Chat in archivio:\n" + "\n".join(f"- {n}: {c} messaggi dal {f} al {l} (partecipanti: {p})" for n, c, f, l, p in rows)


def cerca(params: dict, ctx: dict) -> str:
    q = str(params.get("query", "")).strip()
    if not q:
        return "Errore: serve la query."
    lim = max(1, min(int(params.get("limite") or 15), 60))
    con = _db()
    _sync(con)
    chat = _chat_name(con, str(params.get("chat", "")))
    da, a = _range(params)
    sql = "SELECT m.ts, m.sender, m.text FROM fts JOIN messages m ON m.id = fts.rowid WHERE fts MATCH ?"
    args: list = ['"' + q.replace('"', '') + '"*' if " " not in q else " ".join('"' + w.replace('"', '') + '"' for w in q.split())]
    if chat:
        sql += " AND m.chat = ?"; args.append(chat)
    if da:
        sql += " AND m.ts >= ?"; args.append(da)
    if a:
        sql += " AND m.ts <= ?"; args.append(a)
    sql += " ORDER BY m.ts DESC LIMIT ?"; args.append(lim)
    rows = con.execute(sql, args).fetchall()
    con.close()
    if not rows:
        return f"Nessun messaggio contiene '{q}'."
    return f"Messaggi con '{q}' ({len(rows)}, i più recenti):\n" + _fmt(reversed(rows))


def leggi(params: dict, ctx: dict) -> str:
    con = _db()
    _sync(con)
    chat = _chat_name(con, str(params.get("chat", "")))
    if chat is None:
        con.close()
        return "Chat non trovata: usa whatsapp_chat_elenca per vedere i nomi."
    da, a = _range(params)
    n = max(1, min(int(params.get("ultimi") or 0) or 0, 400))
    if da or a:
        sql, args = "SELECT ts, sender, text FROM messages WHERE chat = ?", [chat]
        if da:
            sql += " AND ts >= ?"; args.append(da)
        if a:
            sql += " AND ts <= ?"; args.append(a)
        rows = con.execute(sql + " ORDER BY ts LIMIT 400", args).fetchall()
    else:
        rows = list(reversed(con.execute("SELECT ts, sender, text FROM messages WHERE chat = ? ORDER BY ts DESC LIMIT ?", (chat, n or 30)).fetchall()))
    con.close()
    if not rows:
        return "Nessun messaggio nel periodo."
    text = _fmt(rows)
    if len(text) > MAX_CHARS:
        text = text[-MAX_CHARS:]
        text = "[…]\n" + text[text.find("\n") + 1:]
    return f"Chat «{chat}», {len(rows)} messaggi:\n{text}"


def statistiche(params: dict, ctx: dict) -> str:
    con = _db()
    _sync(con)
    chat = _chat_name(con, str(params.get("chat", "")))
    if chat is None:
        con.close()
        return "Chat non trovata."
    rows = con.execute("SELECT sender, COUNT(*) FROM messages WHERE chat = ? GROUP BY sender ORDER BY 2 DESC", (chat,)).fetchall()
    months = con.execute("SELECT substr(ts,1,7), COUNT(*) FROM messages WHERE chat = ? GROUP BY 1 ORDER BY 1 DESC LIMIT 12", (chat,)).fetchall()
    con.close()
    return (f"Chat «{chat}»: " + ", ".join(f"{s} {c} messaggi" for s, c in rows) + ".\nUltimi mesi: " +
            ", ".join(f"{m} {c}" for m, c in months))


TOOLS = [
    {"name": "whatsapp_importa", "description": "Importa una chat WhatsApp esportata (.txt, .zip o cartella con _chat.txt) nell'archivio locale e la indicizza. Il nome è facoltativo: altrimenti usa i partecipanti.",
     "parameters": {"type": "object", "properties": {"percorso": {"type": "string"}, "nome": {"type": "string"}}, "required": ["percorso"]}, "run": importa},
    {"name": "whatsapp_importa_cartella", "description": "Importa tutte le chat WhatsApp esportate (.zip o .txt) presenti in una cartella.",
     "parameters": {"type": "object", "properties": {"percorso": {"type": "string"}}, "required": ["percorso"]}, "run": importa_cartella},
    {"name": "whatsapp_chat_elenca", "description": "Elenca le chat WhatsApp in archivio con numero di messaggi, periodo e partecipanti.",
     "parameters": {"type": "object", "properties": {}}, "run": elenca},
    {"name": "whatsapp_cerca", "description": "Cerca parole nei messaggi WhatsApp archiviati (tutte le chat o una sola), con periodo facoltativo (da/a AAAA-MM-GG oppure da='settimana'|'mese').",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "chat": {"type": "string"}, "da": {"type": "string"}, "a": {"type": "string"}, "limite": {"type": "integer"}}, "required": ["query"]}, "run": cerca},
    {"name": "whatsapp_leggi", "description": "Legge i messaggi di una chat: gli ultimi N oppure un periodo (da/a). Usalo per riassumere o rispondere a domande su cosa è stato detto.",
     "parameters": {"type": "object", "properties": {"chat": {"type": "string"}, "ultimi": {"type": "integer"}, "da": {"type": "string"}, "a": {"type": "string"}}}, "run": leggi},
    {"name": "whatsapp_statistiche", "description": "Quanti messaggi per persona e per mese in una chat.",
     "parameters": {"type": "object", "properties": {"chat": {"type": "string"}}}, "run": statistiche},
]
