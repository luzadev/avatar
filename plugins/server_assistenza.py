"""Assistenza server: parla con il bot sysadmin (Claude Code sul server ponte) e legge il monitoraggio Sentinella.

Due canali, nessuna shell diretta da qui:
- Telegram (account personale, Telethon): la richiesta viene inviata al bot @luzaserver_bot, che indaga via SSH
  e chiede approvazione con i pulsanti per ogni comando che modifica qualcosa. Da qui si può approvare o negare.
- SSH verso il server ponte: interroga l'API locale di Sentinella (flotta, allarmi, backup, azioni proposte)
  con uno script eseguito sul server; la password dell'amministratore non lascia mai il server.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import confirm_or_param, CONFERMATO  # noqa: E402
from avatar.settings import Settings  # noqa: E402
from avatar import telegram_client as tg  # noqa: E402

_state: dict = {"since": 0}          # ultimo id di messaggio già consegnato all'assistente
QUIET_SECONDS = 5                    # silenzio del bot dopo l'ultima risposta = risposta completa


def _cfg() -> tuple[str, str, str]:
    s = Settings()
    ssh = str(s.get("server_assist_ssh") or "").strip()
    d = str(s.get("server_assist_dir") or "/opt/aiserverassistance").strip()
    bot = str(s.get("server_assist_bot") or "luzaserver_bot").strip().lstrip("@")
    return ssh, d, bot


# ── Sentinella via SSH ───────────────────────────────────────────────────────
_REMOTE = r'''
import json, sys, urllib.request, urllib.parse
env = {}
for line in open(sys.argv[1] + "/.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
base = env.get("SENTINELLA_API_URL", "http://127.0.0.1:8000")
req = json.loads(sys.stdin.read())
data = urllib.parse.urlencode({"username": env.get("SENTINELLA_ADMIN_USER", "admin"), "password": env.get("SENTINELLA_ADMIN_PASS", "")}).encode()
tok = json.load(urllib.request.urlopen(urllib.request.Request(base + "/api/auth/login", data=data), timeout=15))["access_token"]
out = {}
for item in req:
    path, method, body = item["path"], item.get("method", "GET"), item.get("json")
    r = urllib.request.Request(base + path, method=method, headers={"authorization": "Bearer " + tok, "content-type": "application/json"},
                               data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read()
            out[path] = json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        out[path] = {"errore": "HTTP %s: %s" % (e.code, e.read()[:200].decode(errors="replace"))}
print(json.dumps(out, default=str))
'''


def _sentinella(requests: list[dict]) -> dict:
    """Esegue sul server ponte uno script Python (via stdin) che interroga l'API Sentinella con le credenziali locali."""
    ssh, d, _ = _cfg()
    if not ssh:
        raise RuntimeError("Assistenza server non configurata: imposta l'accesso SSH al server ponte (utente@host) nella scheda Messaggistica.")
    # Il caricatore non contiene apici: sopravvive alla shell remota. Legge tutto lo stdin, separa script e richiesta
    # sul marcatore "\n=====\n", rimette la richiesta su stdin ed esegue lo script.
    loader = "import sys,io;s=sys.stdin.read();i=s.index(chr(10)+chr(61)*5+chr(10));sys.stdin=io.StringIO(s[i+7:]);exec(s[:i])"
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh, "python3", "-c", f"'{loader}'", d]
    script = _REMOTE.replace("req = json.loads(sys.stdin.read())", "req = json.loads(sys.stdin.readline())")
    proc = subprocess.run(cmd, input=script + "\n=====\n" + json.dumps(requests) + "\n", capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip().splitlines()
        raise RuntimeError("server ponte non raggiungibile o script fallito: " + (err[-1] if err else f"codice {proc.returncode}"))
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        raise RuntimeError("risposta inattesa dal server ponte: " + proc.stdout[-300:])


def _pct(v) -> str:
    return "?" if v is None else f"{round(float(v))}%"


def _icon(status: str) -> str:
    return {"online": "🟢", "warning": "🟡", "critical": "🔴", "offline": "⚫"}.get(str(status or "").lower(), "⚪")


def _sev(s: str) -> str:
    return {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(str(s or "").lower(), "⚪")


def flotta(params: dict, ctx: dict) -> str:
    res = _sentinella([{"path": "/api/servers"}, {"path": "/api/alerts?status_filter=firing"}])
    servers, alerts = res.get("/api/servers") or [], res.get("/api/alerts?status_filter=firing") or []
    if isinstance(servers, dict):
        return f"Errore dal monitoraggio: {servers.get('errore')}"
    if not servers:
        return "Nessun server monitorato."
    n_al = {}
    for a in alerts if isinstance(alerts, list) else []:
        n_al[a.get("server_id")] = n_al.get(a.get("server_id"), 0) + 1
    lines = []
    for s in servers:
        m = s.get("latest") or {}
        met = f" cpu {_pct(m.get('cpu_percent'))}, ram {_pct(m.get('mem_percent'))}, disco {_pct(m.get('disk_percent'))}" if m else " (nessuna metrica)"
        al = f", {n_al[s['id']]} allarm{'e' if n_al[s['id']] == 1 else 'i'}" if s.get("id") in n_al else ""
        lines.append(f"{_icon(s.get('status'))} {s.get('name')} [{s.get('status')}]{met}{al}")
    return f"Flotta ({len(servers)} server):\n" + "\n".join(lines)


def dettaglio(params: dict, ctx: dict) -> str:
    nome = str(params.get("nome", "")).strip().lower()
    if not nome:
        return "Errore: serve il nome del server (vedi server_flotta)."
    res = _sentinella([{"path": "/api/servers"}, {"path": "/api/alerts?status_filter=firing"}])
    servers = res.get("/api/servers") or []
    s = next((x for x in servers if str(x.get("name", "")).lower() == nome), None) or next((x for x in servers if nome in str(x.get("name", "")).lower()), None)
    if not s:
        return f"Server '{nome}' non trovato. Server disponibili: " + ", ".join(x.get("name", "?") for x in servers)
    m = s.get("latest") or {}
    os_info = s.get("os_info") or {}
    al = [a for a in (res.get("/api/alerts?status_filter=firing") or []) if a.get("server_id") == s.get("id")]
    out = [f"{_icon(s.get('status'))} {s.get('name')} [{s.get('status')}] {s.get('hostname') or ''} {os_info.get('distro') or ''}".strip()]
    if m:
        out.append(f"CPU {_pct(m.get('cpu_percent'))}, RAM {_pct(m.get('mem_percent'))}, disco {_pct(m.get('disk_percent'))}, load {m.get('load1', '?')}")
    out.append(f"Ultimo heartbeat: {str(s.get('last_seen') or '?').replace('T', ' ')[:19]}")
    if al:
        out.append("Allarmi attivi:")
        out += [f"  {_sev(a.get('severity'))} {a.get('title')}: {' '.join(str(a.get('message') or '').split())[:160]}" for a in al[:8]]
    else:
        out.append("Nessun allarme attivo.")
    return "\n".join(out)


def allarmi(params: dict, ctx: dict) -> str:
    res = _sentinella([{"path": "/api/servers"}, {"path": "/api/alerts?status_filter=firing"}])
    alerts = res.get("/api/alerts?status_filter=firing") or []
    if isinstance(alerts, dict):
        return f"Errore dal monitoraggio: {alerts.get('errore')}"
    if not alerts:
        return "Nessun allarme attivo sui server."
    names = {s.get("id"): s.get("name") for s in (res.get("/api/servers") or [])}
    lines = [f"{_sev(a.get('severity'))} [{names.get(a.get('server_id'), 'srv' + str(a.get('server_id')))}] {a.get('title')}: {' '.join(str(a.get('message') or '').split())[:160]} (dal {str(a.get('created_at') or '')[:16].replace('T', ' ')})" for a in alerts[:20]]
    more = f"\n… e altri {len(alerts) - 20}" if len(alerts) > 20 else ""
    return f"Allarmi attivi ({len(alerts)}):\n" + "\n".join(lines) + more


def backup(params: dict, ctx: dict) -> str:
    azione = str(params.get("azione", "elenco")).strip().lower()
    if azione == "storico":
        runs = _sentinella([{"path": "/api/backups/runs"}]).get("/api/backups/runs") or []
        if not runs:
            return "Nessun backup eseguito finora."
        lines = [f"- job {r.get('job_id')} {str(r.get('started_at') or r.get('created_at') or '')[:16].replace('T', ' ')}: {r.get('status')} {r.get('size_bytes') or ''} {r.get('message') or r.get('error') or ''}".rstrip() for r in runs[:15]]
        return "Ultimi backup:\n" + "\n".join(lines)
    res = _sentinella([{"path": "/api/backups/jobs"}, {"path": "/api/servers"}])
    jobs = res.get("/api/backups/jobs") or []
    names = {s.get("id"): s.get("name") for s in (res.get("/api/servers") or [])}
    if azione == "esegui":
        rif = str(params.get("job", "")).strip().lower()
        job = next((j for j in jobs if str(j.get("id")) == rif or str(j.get("name", "")).lower() == rif), None) or next((j for j in jobs if rif and rif in str(j.get("name", "")).lower()), None)
        if not job:
            return "Job di backup non trovato. Disponibili: " + ", ".join(f"{j.get('id')} {j.get('name')}" for j in jobs)

        def do() -> str:
            r = _sentinella([{"path": f"/api/backups/jobs/{job['id']}/run", "method": "POST"}])
            v = r.get(f"/api/backups/jobs/{job['id']}/run")
            if isinstance(v, dict) and v.get("errore"):
                return f"Errore: {v['errore']}"
            return f"Backup '{job.get('name')}' avviato su {names.get(job.get('server_id'), '?')}: l'agent lo eseguirà al prossimo heartbeat."
        return confirm_or_param(ctx, params, "server_backup", "Avviare il backup?", f"{job.get('name')} su {names.get(job.get('server_id'), '?')}\n{job.get('paths') or ''}", do)
    if not jobs:
        return "Nessun job di backup configurato."
    lines = [f"- {j.get('id')} {j.get('name')} su {names.get(j.get('server_id'), '?')}: {j.get('paths') or ''} {('cron ' + str(j.get('cron'))) if j.get('cron') else ''} {'(disattivo)' if j.get('enabled') is False else ''}".rstrip() for j in jobs]
    return "Job di backup:\n" + "\n".join(lines) + "\nPer lanciarne uno: server_backup con azione=esegui e job=<id o nome>."


def azioni(params: dict, ctx: dict) -> str:
    azione = str(params.get("azione", "elenco")).strip().lower()
    res = _sentinella([{"path": "/api/actions"}, {"path": "/api/servers"}])
    actions = res.get("/api/actions") or []
    if isinstance(actions, dict):
        return f"Errore dal monitoraggio: {actions.get('errore')}"
    names = {s.get("id"): s.get("name") for s in (res.get("/api/servers") or [])}

    def fmt(a: dict) -> str:
        return f"[{a.get('id')}] {names.get(a.get('server_id'), '?')} ({a.get('status')}, rischio {a.get('risk') or a.get('risk_level') or '?'}): {a.get('command') or ''} — {a.get('reason') or a.get('explanation') or ''}".strip()

    if azione in ("approva", "rifiuta"):
        aid = str(params.get("id", "")).strip()
        a = next((x for x in actions if str(x.get("id")) == aid), None)
        if not a:
            pend = [x for x in actions if str(x.get("status", "")).lower() == "pending"]
            return "Azione non trovata. In attesa: " + ("\n".join(fmt(x) for x in pend) if pend else "nessuna")
        verb = "approve" if azione == "approva" else "reject"

        def do() -> str:
            r = _sentinella([{"path": f"/api/actions/{a['id']}/{verb}", "method": "POST"}])
            v = r.get(f"/api/actions/{a['id']}/{verb}")
            if isinstance(v, dict) and v.get("errore"):
                return f"Errore: {v['errore']}"
            return ("Azione approvata: verrà eseguita al prossimo heartbeat dell'agent." if verb == "approve" else "Azione rifiutata.")
        return confirm_or_param(ctx, params, "server_azione", "Approvare l'intervento sul server?" if verb == "approve" else "Rifiutare l'intervento?", fmt(a), do)
    pend = [x for x in actions if str(x.get("status", "")).lower() == "pending"]
    if not pend:
        recent = actions[-5:] if isinstance(actions, list) else []
        return "Nessun intervento in attesa di approvazione." + ("\nUltimi: \n" + "\n".join(fmt(x) for x in recent) if recent else "")
    return f"Interventi proposti in attesa ({len(pend)}):\n" + "\n".join(fmt(x) for x in pend) + "\nPer decidere: server_azioni con azione=approva|rifiuta e id."


# ── Assistente sysadmin via Telegram ─────────────────────────────────────────
def _buttons(msg) -> list:
    out = []
    for row in (msg.buttons or []):
        for b in row:
            data = getattr(b.button, "data", None)
            if isinstance(data, (bytes, bytearray)):
                out.append((data.decode(errors="ignore"), b))
    return out


def _pending(msg) -> bool:
    return any(d.startswith(("ok:", "approve:")) for d, _ in _buttons(msg))


def _text(m) -> str:
    t = (m.message or "").strip()
    if not t and getattr(m, "media", None):
        t = "[allegato]"
    return t


async def _collect(client, bot, since: int, wait: float) -> tuple[list, bool, bool]:
    """Raccoglie le risposte del bot dopo `since`. Ritorna (messaggi, completo, approvazione_richiesta)."""
    start = time.monotonic()
    seen: dict[int, object] = {}
    last_change = None
    while True:
        await asyncio.sleep(2)
        msgs = await client.get_messages(bot, min_id=since, limit=40)
        incoming = sorted((m for m in msgs if not m.out), key=lambda m: m.id)
        if [m.id for m in incoming] != list(seen):
            seen = {m.id: m for m in incoming}
            last_change = time.monotonic()
        if any(_pending(m) for m in incoming):
            return incoming, False, True
        now = time.monotonic()
        if incoming and last_change is not None and now - last_change >= QUIET_SECONDS:
            return incoming, True, False
        if now - start >= wait:
            return incoming, False, False


def _report(msgs: list, done: bool, approval: bool) -> str:
    if msgs:
        _state["since"] = max(m.id for m in msgs)
    parts = [_text(m) for m in msgs if _text(m)]
    if approval:
        req = next(m for m in reversed(msgs) if _pending(m))
        return ("APPROVAZIONE RICHIESTA: il bot vuole eseguire un comando che modifica il server e attende una decisione.\n"
                + _text(req) + "\nChiedi all'utente se approvare, poi usa server_approva con decisione=approva oppure nega.")
    if not parts:
        return "Il bot sta ancora lavorando (nessuna risposta finora). Richiama server_attendi tra poco per avere il risultato."
    body = "\n\n".join(parts)
    if not done:
        return body + "\n\n(il bot potrebbe non aver finito: se manca una conclusione, richiama server_attendi)"
    return body


def chiedi(params: dict, ctx: dict) -> str:
    testo = str(params.get("richiesta", "")).strip()
    if not testo:
        return "Errore: serve la richiesta da inoltrare al bot dei server."
    wait = max(20, min(int(params.get("attesa") or 150), 300))
    _, _, botname = _cfg()

    async def go(client):
        bot = await client.get_entity(botname)
        sent = await client.send_message(bot, testo)
        r = await _collect(client, bot, sent.id, wait)
        try:
            await client.send_read_acknowledge(bot)
        except Exception:
            pass
        return r

    if ctx.get("log"):
        ctx["log"](f"[server] → @{botname}: {testo[:80]}")
    msgs, done, approval = tg.run(go)
    return _report(msgs, done, approval)


def attendi(params: dict, ctx: dict) -> str:
    wait = max(10, min(int(params.get("attesa") or 120), 300))
    _, _, botname = _cfg()

    async def go(client):
        bot = await client.get_entity(botname)
        since = _state["since"]
        if not since:
            last = await client.get_messages(bot, limit=1)
            since = last[0].id if last else 0
            # niente di nuovo da consegnare: aspetta i messaggi successivi all'ultimo visto
        r = await _collect(client, bot, since, wait)
        try:
            await client.send_read_acknowledge(bot)
        except Exception:
            pass
        return r

    msgs, done, approval = tg.run(go)
    return _report(msgs, done, approval)


def approva(params: dict, ctx: dict) -> str:
    dec = str(params.get("decisione", "")).strip().lower()
    if dec not in ("approva", "nega"):
        return "Errore: decisione deve essere 'approva' o 'nega'."
    _, _, botname = _cfg()

    async def find(client):
        bot = await client.get_entity(botname)
        msgs = await client.get_messages(bot, limit=30)
        for m in msgs:
            if not m.out and _pending(m):
                return m.id, _text(m)
        return None, None

    mid, txt = tg.run(find)
    if mid is None:
        return "Nessuna richiesta di approvazione in sospeso dal bot dei server."

    def do() -> str:
        async def click(client):
            bot = await client.get_entity(botname)
            m = (await client.get_messages(bot, ids=[mid]))[0]
            if m is None or not _pending(m):
                return "La richiesta non è più in sospeso (già decisa o scaduta).", False
            for data, b in _buttons(m):
                if (dec == "approva" and data.startswith(("ok:", "approve:"))) or (dec == "nega" and data.startswith(("no:", "reject:"))):
                    await b.click()
                    return "", True
            return "Pulsante non trovato nel messaggio.", False

        msg, ok = tg.run(click)
        if not ok:
            return msg
        if dec == "nega":
            return "Comando negato al bot.\n" + _report(*tg.run(lambda c: _collect_after(c, botname, mid, 60)))
        wait = max(20, min(int(params.get("attesa") or 150), 300))
        return "Comando approvato, il bot lo esegue.\n" + _report(*tg.run(lambda c: _collect_after(c, botname, mid, wait)))

    if dec == "nega":
        return do()
    return confirm_or_param(ctx, params, "server_approva", "Approvare il comando sul server?", txt[:400], do)


async def _collect_after(client, botname: str, since: int, wait: float):
    bot = await client.get_entity(botname)
    r = await _collect(client, bot, since, wait)
    try:
        await client.send_read_acknowledge(bot)
    except Exception:
        pass
    return r


TOOLS = [
    {"name": "server_flotta", "description": "Stato di tutti i server monitorati (Sentinella): stato, CPU, RAM, disco e numero di allarmi. Usalo per 'come stanno i server?'.",
     "parameters": {"type": "object", "properties": {}}, "run": flotta},
    {"name": "server_dettaglio", "description": "Dettaglio di un server monitorato per nome: metriche, sistema, ultimo contatto e allarmi attivi.",
     "parameters": {"type": "object", "properties": {"nome": {"type": "string"}}, "required": ["nome"]}, "run": dettaglio},
    {"name": "server_allarmi", "description": "Allarmi attivi sui server (soglie superate, servizi giù, errori, certificati).",
     "parameters": {"type": "object", "properties": {}}, "run": allarmi},
    {"name": "server_backup", "description": "Backup dei server: azione=elenco (job configurati), storico (ultime esecuzioni) o esegui (lancia un job per id o nome, con conferma).",
     "parameters": {"type": "object", "properties": {"azione": {"type": "string", "enum": ["elenco", "storico", "esegui"]}, "job": {"type": "string"}, "confermato": CONFERMATO}}, "run": backup},
    {"name": "server_azioni", "description": "Interventi di riparazione proposti dall'AI di monitoraggio in attesa di decisione: azione=elenco, approva o rifiuta (con id). Approvare richiede conferma sullo schermo.",
     "parameters": {"type": "object", "properties": {"azione": {"type": "string", "enum": ["elenco", "approva", "rifiuta"]}, "id": {"type": "string"}, "confermato": CONFERMATO}}, "run": azioni},
    {"name": "server_chiedi", "description": "Inoltra una richiesta in linguaggio naturale all'assistente sysadmin dei server (bot Telegram con Claude Code sul server ponte): diagnosi, log, spazio disco, riavvii, modifiche. Il bot indaga via SSH e risponde; se vuole eseguire un comando che modifica qualcosa, il risultato è APPROVAZIONE RICHIESTA: chiedi all'utente e usa server_approva. Accetta anche i comandi /progetti, /progetto <nome> (lavorare sul codice di un progetto sul server), /sys (torna ai server), /reset. Può richiedere fino a qualche minuto.",
     "parameters": {"type": "object", "properties": {"richiesta": {"type": "string"}, "attesa": {"type": "integer", "description": "secondi massimi di attesa (default 150)"}}, "required": ["richiesta"]}, "run": chiedi},
    {"name": "server_attendi", "description": "Continua ad aspettare la risposta del bot dei server dopo server_chiedi o server_approva, quando il lavoro non era ancora concluso.",
     "parameters": {"type": "object", "properties": {"attesa": {"type": "integer"}}}, "run": attendi},
    {"name": "server_approva", "description": "Risponde alla richiesta di approvazione in sospeso del bot dei server: decisione=approva (con conferma sullo schermo; con [CONFIRMATION_PENDING] di' all'utente di confermare) oppure nega. Poi attende l'esito.",
     "parameters": {"type": "object", "properties": {"decisione": {"type": "string", "enum": ["approva", "nega"]}, "attesa": {"type": "integer"}, "confermato": CONFERMATO}, "required": ["decisione"]}, "run": approva},
]
