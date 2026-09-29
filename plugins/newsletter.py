"""Newsletter e posta indesiderata: trova i mittenti che offrono la disiscrizione standard (List-Unsubscribe),
si disiscrive con conferma (one-click, email o link) e blocca i mittenti senza via d'uscita con una regola di Mail.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mail  # noqa: E402  (plugin mail: indice, emlx, invio)
from avatar.macos import confirm_or_param, CONFERMATO  # noqa: E402
from avatar.settings import DATA_DIR  # noqa: E402

STATO = DATA_DIR / "disiscrizioni.json"
_last: dict = {"senders": []}
CARTELLE = {"arrivo": "(mb.url LIKE '%/INBOX' OR mb.url LIKE '%/Inbox')",
            "indesiderata": "(mb.url LIKE '%Junk%' OR mb.url LIKE '%ndesiderata%' OR mb.url LIKE '%Spam%')"}


def _load() -> dict:
    try:
        return json.loads(STATO.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    STATO.parent.mkdir(parents=True, exist_ok=True)
    STATO.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def _parse_lu(value: str) -> tuple[str, str]:
    """(url https, mailto) dall'intestazione List-Unsubscribe."""
    urls = re.findall(r"<([^>]+)>", value or "") or [value or ""]
    http = next((u.strip() for u in urls if u.strip().lower().startswith("http")), "")
    mailto = next((u.strip() for u in urls if u.strip().lower().startswith("mailto:")), "")
    return http, mailto


def scansiona(giorni: int = 30, cartella: str = "arrivo", max_msg: int = 400) -> list[dict]:
    where = " OR ".join(CARTELLE[c] for c in (("arrivo", "indesiderata") if cartella == "tutte" else (cartella,)))
    con = sqlite3.connect(f"file:{mail.INDEX}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute(f"""SELECT m.ROWID, mb.url, COALESCE(a.address,''), COALESCE(a.comment,''), COALESCE(s.subject,''), m.date_received
            FROM messages m JOIN mailboxes mb ON m.mailbox=mb.ROWID LEFT JOIN addresses a ON m.sender=a.ROWID LEFT JOIN subjects s ON m.subject=s.ROWID
            WHERE m.deleted=0 AND ({where}) AND m.date_received > ? ORDER BY m.date_received DESC LIMIT ?""", (time.time() - giorni * 86400, max_msg)).fetchall()
    finally:
        con.close()
    senders: dict[str, dict] = {}
    for rid, url, addr, name, subj, ts in rows:
        addr = addr.lower()
        if not addr:
            continue
        s = senders.setdefault(addr, {"addr": addr, "nome": name or addr, "n": 0, "oggetto": subj, "tipo": "nessuno", "http": "", "mailto": "", "oneclick": False})
        s["n"] += 1
        if s["tipo"] != "nessuno" and s["oneclick"]:
            continue
        p = mail._find_emlx(rid, url)
        if not p:
            continue
        try:
            msg = mail._emlx_message(p)
        except Exception:
            continue
        lu = msg.get("List-Unsubscribe")
        if not lu:
            continue
        http, mailto = _parse_lu(str(lu))
        oneclick = bool(http) and "one-click" in str(msg.get("List-Unsubscribe-Post", "")).lower()
        s.update(http=http or s["http"], mailto=mailto or s["mailto"], oneclick=s["oneclick"] or oneclick)
        s["tipo"] = "one-click" if s["oneclick"] else ("email" if s["mailto"] else "link")
    fatte = _load()
    out = sorted(senders.values(), key=lambda s: (-(s["tipo"] != "nessuno"), -s["n"]))
    for i, s in enumerate(out, 1):
        s["k"] = i
        s["fatta"] = fatte.get(s["addr"], {}).get("esito", "")
    _last["senders"] = out
    return out


def elenco(params: dict, ctx: dict) -> str:
    giorni = max(3, min(int(params.get("giorni") or 30), 365))
    cartella = str(params.get("cartella") or "arrivo").lower()
    if cartella not in ("arrivo", "indesiderata", "tutte"):
        cartella = "arrivo"
    if ctx.get("player") is not None and hasattr(ctx["player"], "set_state"):
        ctx["player"].set_state("PROCESSING · analizzo le intestazioni delle email")
    senders = scansiona(giorni, cartella)
    if not senders:
        return f"Nessun messaggio negli ultimi {giorni} giorni nella cartella scelta."
    con = [s for s in senders if s["tipo"] != "nessuno"]
    senza = [s for s in senders if s["tipo"] == "nessuno"]
    lim = max(5, min(int(params.get("limite") or 40), 80))
    lines = [f"{s['k']}. {s['nome'][:32]} <{s['addr']}> — {s['n']} msg, disiscrizione {s['tipo']}{' (già fatta: ' + s['fatta'] + ')' if s['fatta'] else ''} — «{s['oggetto'][:50]}»" for s in con[:lim]]
    out = [f"Mittenti con disiscrizione disponibile ({len(con)}) negli ultimi {giorni} giorni, cartella {cartella}:"] + lines
    if senza:
        out.append(f"Altri {len(senza)} mittenti senza disiscrizione automatica: in genere notifiche di servizio (banca, ordini, assistenza) da lasciare; se qualcuno è spam, usa mail_blocca invece di cliccare link. I più frequenti: " + ", ".join(f"{s['nome'][:20]} ({s['n']})" for s in senza[:8]) + ("…" if len(senza) > 8 else ""))
    out.append("Per procedere: mail_disiscrivi con i numeri (es. '1,2,5') o 'tutti'.")
    return "\n".join(out)


def _oneclick(url: str) -> str:
    req = urllib.request.Request(url, data=b"List-Unsubscribe=One-Click", method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0 (Macintosh) LuZa"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return f"one-click ok (HTTP {r.status})"


def _link(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) LuZa"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read(200000).decode("utf-8", errors="replace").lower()
    ok = any(w in body for w in ("unsubscribed", "disiscritt", "cancellat", "removed", "rimoss", "success", "non riceverai", "you have been", "you will no longer"))
    return f"link aperto (HTTP {r.status}){', conferma nella pagina' if ok else ', esito da verificare: la pagina potrebbe chiedere un clic'}"


def _mailto(mailto: str) -> str:
    u = urllib.parse.urlparse(mailto)
    q = urllib.parse.parse_qs(u.query)
    to = u.path
    subject = q.get("subject", ["unsubscribe"])[0]
    body = q.get("body", ["unsubscribe"])[0]
    mail._osa(mail.SEND_SCRIPT, to, subject, body)
    return f"email di disiscrizione inviata a {to}"


def disiscrivi(params: dict, ctx: dict) -> str:
    if not _last["senders"]:
        scansiona()
    sel = str(params.get("mittenti") or "").strip().lower()
    if not sel:
        return "Indica i mittenti: numeri dell'elenco (es. '1,3') o 'tutti'."
    cand = [s for s in _last["senders"] if s["tipo"] != "nessuno"]
    if sel in ("tutti", "tutte", "all"):
        chosen = cand
    else:
        keys = {k.strip() for k in re.split(r"[,\s]+", sel) if k.strip()}
        chosen = [s for s in cand if str(s["k"]) in keys or s["addr"] in keys or any(k in s["addr"] or k in s["nome"].lower() for k in keys if len(k) > 3)]
    if not chosen:
        return "Nessun mittente corrispondente con disiscrizione disponibile."
    desc = "\n".join(f"• {s['nome'][:28]} <{s['addr']}> ({s['tipo']})" for s in chosen[:12]) + (f"\n… e altri {len(chosen) - 12}" if len(chosen) > 12 else "")

    def do() -> str:
        stato = _load(); res = []
        for s in chosen:
            if ctx.get("player") is not None and hasattr(ctx["player"], "set_state"):
                ctx["player"].set_state(f"PROCESSING · disiscrizione da {s['nome'][:20]}")
            try:
                if s["oneclick"]:
                    esito = _oneclick(s["http"])
                elif s["mailto"]:
                    esito = _mailto(s["mailto"])
                elif s["http"]:
                    esito = _link(s["http"])
                else:
                    esito = "nessun metodo"
            except Exception as err:
                esito = f"fallita: {str(err)[:80]}"
            stato[s["addr"]] = {"esito": esito, "quando": time.strftime("%Y-%m-%d"), "nome": s["nome"]}
            res.append(f"- {s['nome'][:30]}: {esito}")
        _save(stato)
        ok = sum(1 for r in res if "fallita" not in r and "nessun" not in r)
        return f"Disiscrizioni eseguite: {ok} su {len(chosen)}.\n" + "\n".join(res) + "\nLe email già ricevute restano; i mittenti seri smettono entro pochi giorni."

    return confirm_or_param(ctx, params, "mail_disiscrivi", f"Disiscriversi da {len(chosen)} mittenti?", desc, do)


BLOCK_SCRIPT = '''on run argv
set addr to item 1 of argv
tell application "Mail"
    set r to make new rule at end of rules with properties {name:"LuZa: blocca " & addr, enabled:true, delete message:true, stop evaluating rules:true}
    tell r
        make new rule condition at end of rule conditions with properties {rule type:from header, qualifier:does contain value, expression:addr}
    end tell
end tell
return "ok"
end run'''


def blocca(params: dict, ctx: dict) -> str:
    addr = str(params.get("mittente") or "").strip().lower()
    if "@" not in addr:
        m = next((s for s in _last["senders"] if str(s["k"]) == addr or addr in s["nome"].lower()), None)
        if not m:
            return "Indica l'indirizzo email del mittente da bloccare."
        addr = m["addr"]

    def do() -> str:
        mail._osa(BLOCK_SCRIPT, addr)
        return f"Regola creata in Mail: le email da {addr} vengono cestinate all'arrivo."

    return confirm_or_param(ctx, params, "mail_blocca", "Bloccare questo mittente in Mail?", f"{addr}\n(regola: cestina all'arrivo)", do)


TOOLS = [
    {"name": "mail_newsletter", "description": "Trova i mittenti di newsletter e promozioni negli ultimi giorni (cartella: arrivo, indesiderata o tutte) e per ciascuno dice se offre la disiscrizione standard (one-click, email o link) e quante email ha mandato. Usalo per 'disiscrivimi dalle newsletter', 'chi mi riempie la posta?'. Nota: la posta indesiderata su questo Mac è quasi vuota, la maggior parte delle newsletter è nella posta in arrivo.",
     "parameters": {"type": "object", "properties": {"giorni": {"type": "integer"}, "cartella": {"type": "string", "enum": ["arrivo", "indesiderata", "tutte"]}, "limite": {"type": "integer"}}}, "run": elenco},
    {"name": "mail_disiscrivi", "description": "Esegue la disiscrizione dai mittenti scelti nell'ultimo elenco di mail_newsletter: mittenti = numeri separati da virgola (es. '1,2,5'), indirizzi, oppure 'tutti'. Usa il metodo standard di ciascuno (one-click, email automatica o link). Chiede conferma sullo schermo; con [CONFIRMATION_PENDING] di' all'utente di confermare.",
     "parameters": {"type": "object", "properties": {"mittenti": {"type": "string"}, "confermato": CONFERMATO}, "required": ["mittenti"]}, "run": disiscrivi},
    {"name": "mail_blocca", "description": "Blocca un mittente creando una regola in Mail che cestina le sue email all'arrivo (per lo spam senza disiscrizione). Chiede conferma.",
     "parameters": {"type": "object", "properties": {"mittente": {"type": "string", "description": "indirizzo email, oppure numero o nome dall'ultimo elenco"}, "confermato": CONFERMATO}, "required": ["mittente"]}, "run": blocca},
]
