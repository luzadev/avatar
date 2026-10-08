"""Documenti interrogabili: indicizza le cartelle scelte (PDF, Word, testo, pagine…) in pezzi da ~800 caratteri,
con ricerca ibrida per parole (SQLite FTS5) e per significato (embeddings multilingual-e5-small). Tutto locale.
L'indice si aggiorna da solo ogni ora, solo per i file nuovi o modificati."""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.settings import DATA_DIR, Settings  # noqa: E402

DB = DATA_DIR / "documenti.sqlite"
EMB_MODEL = "intfloat/multilingual-e5-small"
EXT = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt", ".pages", ".html", ".csv"}
SKIP_DIRS = {"node_modules", ".git", "Library", ".venv", "venv", "__pycache__", "build", "dist", ".Trash"}
MAX_MB = 30
_emb: dict = {}
_stato = {"in_corso": False, "ultimo": "", "errore": ""}


def _cartelle() -> list[Path]:
    raw = str(Settings().get("documenti_cartelle") or "~/Documents, ~/Desktop")
    return [Path(c.strip()).expanduser() for c in raw.split(",") if c.strip()]


def _con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, chunks INTEGER);
        CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, path TEXT, n INTEGER, text TEXT, emb BLOB);
        CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN INSERT INTO fts(rowid, text) VALUES (new.id, new.text); END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN INSERT INTO fts(fts, rowid, text) VALUES ('delete', old.id, old.text); END;
    """)
    return con


def _embed(texts: list[str], query: bool = False) -> np.ndarray:
    """Vettori normalizzati (384 dimensioni); prefissi 'query:'/'passage:' richiesti dal modello e5."""
    import torch
    from transformers import AutoModel, AutoTokenizer
    if not _emb:
        _emb["tok"] = AutoTokenizer.from_pretrained(EMB_MODEL)
        dev = "mps" if torch.backends.mps.is_available() else "cpu"      # GPU del Mac: ~10× più veloce della CPU
        _emb["model"] = AutoModel.from_pretrained(EMB_MODEL).eval().to(dev)
        _emb["dev"] = dev
    tok, model = _emb["tok"], _emb["model"]
    out = []
    for i in range(0, len(texts), 32):
        batch = [("query: " if query else "passage: ") + t for t in texts[i:i + 32]]
        enc = tok(batch, padding=True, truncation=True, max_length=512, return_tensors="pt").to(_emb["dev"])
        with torch.no_grad():
            h = model(**enc).last_hidden_state
        m = enc["attention_mask"].unsqueeze(-1).float()
        v = (h * m).sum(1) / m.sum(1)
        out.append(torch.nn.functional.normalize(v, dim=1).float().cpu().numpy().astype(np.float32))
    return np.concatenate(out) if out else np.zeros((0, 384), np.float32)


def _testo(path: Path) -> str:
    import file as fileplugin   # stesso estrattore del plugin file (pdf, docx, testo, textutil per gli altri)
    return fileplugin._read(path)


def _pezzi(testo: str, size: int = 800, overlap: int = 150) -> list[str]:
    testo = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", testo)).strip()
    out, i = [], 0
    while i < len(testo):
        out.append(testo[i:i + size]); i += size - overlap
    return [p for p in out if len(p.strip()) > 40]


def _escludi() -> set[str]:
    raw = str(Settings().get("documenti_escludi") or "Progetti2026")
    return {x.strip() for x in raw.split(",") if x.strip()}


def _files():
    skip = SKIP_DIRS | _escludi()
    for root in _cartelle():
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".") and not d.endswith(".app")]
            for f in filenames:
                p = Path(dirpath) / f
                if p.suffix.lower() in EXT and not f.startswith("~$"):
                    try:
                        if p.stat().st_size <= MAX_MB * 1e6:
                            yield p
                    except OSError:
                        pass


def aggiorna(log=None) -> str:
    """Indicizzazione incrementale: aggiunge i file nuovi/modificati, toglie quelli spariti."""
    import fcntl
    DB.parent.mkdir(parents=True, exist_ok=True)
    lockf = open(DATA_DIR / "documenti.lock", "w")
    try:                                   # blocco tra processi: app e indicizzazioni manuali non si sovrappongono
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lockf.close()
        return "Indicizzazione già in corso."
    _stato["in_corso"] = True
    try:
        con = _con()
        noti = dict(con.execute("SELECT path, mtime FROM files").fetchall())
        visti, nuovi, pezzi_tot = set(), 0, 0
        for p in _files():
            sp = str(p); visti.add(sp)
            mt = p.stat().st_mtime
            if noti.get(sp) == mt:
                continue
            try:
                pezzi = _pezzi(_testo(p))[:400]
            except Exception:
                pezzi = []
            vec = _embed(pezzi) if pezzi else np.zeros((0, 384), np.float32)
            con.execute("DELETE FROM chunks WHERE path=?", (sp,))
            con.executemany("INSERT INTO chunks(path, n, text, emb) VALUES (?,?,?,?)", [(sp, i, t, vec[i].tobytes()) for i, t in enumerate(pezzi)])
            con.execute("INSERT OR REPLACE INTO files(path, mtime, chunks) VALUES (?,?,?)", (sp, mt, len(pezzi)))
            con.commit()
            nuovi += 1; pezzi_tot += len(pezzi)
            if log and nuovi % 25 == 0:
                log(f"[documenti] indicizzati {nuovi} file…")
        spariti = [p for p in noti if p not in visti]
        for sp in spariti:
            con.execute("DELETE FROM chunks WHERE path=?", (sp,)); con.execute("DELETE FROM files WHERE path=?", (sp,))
        con.commit()
        n_files, n_chunks = con.execute("SELECT COUNT(*), COALESCE(SUM(chunks),0) FROM files").fetchone()
        con.close()
        _stato["ultimo"] = time.strftime("%d/%m %H:%M")
        return f"Indice aggiornato: {nuovi} file nuovi o modificati, {len(spariti)} rimossi. Totale {n_files} documenti, {n_chunks} passaggi."
    finally:
        _stato["in_corso"] = False
        fcntl.flock(lockf, fcntl.LOCK_UN); lockf.close()


def cerca_testo(domanda: str, k: int = 6) -> list[dict]:
    con = _con()
    rows = con.execute("SELECT id, path, text, emb FROM chunks").fetchall()
    if not rows:
        con.close(); return []
    ids = np.array([r[0] for r in rows]); M = np.frombuffer(b"".join(r[3] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    q = _embed([domanda], query=True)[0]
    sem = M @ q                                                     # coseno (vettori normalizzati)
    score = {int(i): float(s) for i, s in zip(ids, sem)}
    words = [w for w in re.findall(r"\w{3,}", domanda.lower())]
    if words:
        try:
            for (rid,) in con.execute("SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT 30", (" OR ".join(w + "*" for w in words),)):
                score[rid] = score.get(rid, 0) + 0.15                  # bonus per le parole esatte
        except sqlite3.OperationalError:
            pass
    best = sorted(score.items(), key=lambda kv: -kv[1])[:k * 3]
    by_id = {r[0]: r for r in rows}
    out, per_file = [], {}
    for rid, sc in best:
        _i, path, text, _e = by_id[rid]
        if per_file.get(path, 0) >= 2:
            continue
        per_file[path] = per_file.get(path, 0) + 1
        out.append({"path": path, "testo": text, "score": round(sc, 3)})
        if len(out) >= k:
            break
    con.close()
    return out


# ── strumenti ────────────────────────────────────────────────────────────────
def t_cerca(params: dict, ctx: dict) -> str:
    domanda = str(params.get("domanda") or "").strip()
    if not domanda:
        return "Errore: serve la domanda."
    if not DB.exists():
        return "Non ho ancora indicizzato i documenti: usa documenti_aggiorna (ci vuole qualche minuto la prima volta)."
    res = cerca_testo(domanda, max(3, min(int(params.get("quanti") or 6), 12)))
    if not res:
        return "Nessun passaggio pertinente nei documenti indicizzati."
    home = str(Path.home())
    righe = [f"[{i + 1}] {r['path'].replace(home, '~')} (pertinenza {r['score']}):\n{r['testo']}" for i, r in enumerate(res)]
    return ("Passaggi più pertinenti dai documenti dell'utente. Rispondi basandoti su questi, cita il nome del file, "
            "e di' chiaramente se l'informazione non c'è:\n\n" + "\n\n".join(righe))


def t_aggiorna(params: dict, ctx: dict) -> str:
    p = ctx.get("player")
    if p is not None and hasattr(p, "set_state"):
        p.set_state("PROCESSING · indicizzo i documenti")
    return aggiorna(ctx.get("log"))


def t_stato(params: dict, ctx: dict) -> str:
    if not DB.exists():
        return "Indice non ancora creato. Cartelle: " + ", ".join(str(c) for c in _cartelle())
    con = _con(); n_files, n_chunks = con.execute("SELECT COUNT(*), COALESCE(SUM(chunks),0) FROM files").fetchone(); con.close()
    return (f"Documenti indicizzati: {n_files} file, {n_chunks} passaggi. Cartelle: {', '.join(str(c) for c in _cartelle())}. "
            + ("Indicizzazione in corso." if _stato["in_corso"] else f"Ultimo aggiornamento: {_stato['ultimo'] or 'all\'avvio'}."))


_proc: dict = {"p": None}


def pausa(attiva: bool) -> None:
    """Sospende/riprende l'indicizzazione mentre LuZa risponde: niente contesa di GPU e CPU con il modello."""
    import signal
    p = _proc["p"]
    if p is not None and p.poll() is None:
        try:
            p.send_signal(signal.SIGSTOP if attiva else signal.SIGCONT)
        except Exception:
            pass


def _servizio() -> None:
    """Ogni ora indicizza in un processo separato a priorità bassa (niente blocchi del GIL nell'app)."""
    import subprocess
    time.sleep(120)
    while True:
        if Settings().get("documenti_enabled", True):
            code = ("import sys; sys.argv=['memory_mcp.py']; sys.path.insert(0, %r); import documenti_rag as d; print('[documenti]', d.aggiorna(), flush=True)"
                    % str(Path(__file__).resolve().parent))
            _proc["p"] = subprocess.Popen(["nice", "-n", "15", sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parent.parent))
            _proc["p"].wait()
            _proc["p"] = None
        time.sleep(3600)


if Path(sys.argv[0]).name != "memory_mcp.py":            # solo nell'app
    threading.Thread(target=_servizio, daemon=True, name="documenti").start()


TOOLS = [
    {"name": "documenti_cerca", "description": "Cerca nel CONTENUTO dei documenti dell'utente (PDF, Word, testi nelle cartelle indicizzate: Documenti, Scrivania) per rispondere a domande come 'cosa dice il contratto sulle penali?', 'quando scade l'assicurazione?', 'trova la ricevuta del dentista'. Restituisce i passaggi più pertinenti con il file di provenienza.",
     "parameters": {"type": "object", "properties": {"domanda": {"type": "string"}, "quanti": {"type": "integer"}}, "required": ["domanda"]}, "run": t_cerca},
    {"name": "documenti_aggiorna", "description": "Aggiorna subito l'indice dei documenti (file nuovi o modificati). Normalmente avviene da solo ogni ora.",
     "parameters": {"type": "object", "properties": {}}, "run": t_aggiorna},
    {"name": "documenti_stato", "description": "Quanti documenti sono indicizzati, quali cartelle e quando è stato l'ultimo aggiornamento.",
     "parameters": {"type": "object", "properties": {}}, "run": t_stato},
]


if __name__ == "__main__":
    assert _pezzi("a" * 2000)[1].startswith("a") and len(_pezzi("a" * 2000)) == 4
    v = _embed(["Il contratto prevede una penale del 5% per ritardo."]); q = _embed(["quanto è la penale?"], query=True)[0]
    w = _embed(["La ricetta della carbonara usa guanciale."])
    assert float(v[0] @ q) > float(w[0] @ q), "il significato deve battere il testo non pertinente"
    print("ok: pertinente", round(float(v[0] @ q), 3), "> non pertinente", round(float(w[0] @ q), 3))
