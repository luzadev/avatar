"""Monitor di Mail, WhatsApp e Telegram: raccoglie i messaggi nuovi, li fa valutare e crea avvisi."""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from avatar.settings import DATA_DIR, Settings

STATE_FILE = DATA_DIR / "monitor.json"
MAIL_INDEX = Path.home() / "Library" / "Mail" / "V10" / "MailData" / "Envelope Index"
WA_DB = DATA_DIR / "whatsapp" / "index.sqlite"
DEFAULT_RULES = ("richieste di assistenza o di aiuto, domande rivolte a me che aspettano una risposta, problemi o cose che non funzionano, "
                 "urgenze, scadenze e pagamenti, appuntamenti da confermare o spostare, preventivi e lavori richiesti. "
                 "NON contano: newsletter, promozioni, notifiche automatiche, conferme d'ordine, saluti e chiacchiere senza richieste.")
KEYWORDS = re.compile(r"\?|urgent|aiut|assistenz|problem|non funziona|non riesco|errore|bloccat|guast|richie|preventiv|fattur|pagament|scadenz|"
                      r"conferm|appuntament|puoi|potresti|riesci|serve|servirebbe|quando|mi dici|fammi sapere|rispond|chiam|ti prego|per favore|subito|entro", re.I)
_lock = threading.Lock()


class Monitor:
    def __init__(self, ui, say, settings: Settings) -> None:
        self.ui, self.say, self.settings = ui, say, settings
        self.state = self._load()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── stato ────────────────────────────────────────────────────────────
    def _load(self) -> dict:
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {"mail_rowid": None, "wa_ts": None, "tg": {}, "alerts": [], "seen": []}

    def _save(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.state["alerts"] = self.state.get("alerts", [])[-100:]
        self.state["seen"] = self.state.get("seen", [])[-2000:]
        STATE_FILE.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        time.sleep(20)
        while not self._stop.is_set():
            if self.settings.get("monitor_enabled"):
                try:
                    self.scan()
                except Exception as err:
                    print(f"[monitor] {err}")
            self._stop.wait(int(self.settings.get("monitor_intervallo") or 60))

    # ── raccolta ─────────────────────────────────────────────────────────
    def _collect_mail(self, baseline: bool) -> list[dict]:
        if not MAIL_INDEX.exists():
            return []
        con = sqlite3.connect(f"file:{MAIL_INDEX}?mode=ro", uri=True, timeout=5)
        try:
            last = self.state.get("mail_rowid")
            if last is None or baseline:
                self.state["mail_rowid"] = con.execute("SELECT MAX(ROWID) FROM messages").fetchone()[0] or 0
                return []
            rows = con.execute("""SELECT m.ROWID, COALESCE(a.comment,''), COALESCE(a.address,''), COALESCE(s.subject,''), COALESCE(su.summary,'')
                FROM messages m JOIN mailboxes mb ON m.mailbox = mb.ROWID LEFT JOIN addresses a ON m.sender = a.ROWID
                LEFT JOIN subjects s ON m.subject = s.ROWID LEFT JOIN summaries su ON m.summary = su.ROWID
                WHERE m.ROWID > ? AND m.deleted = 0 AND (mb.url LIKE '%/INBOX' OR mb.url LIKE '%/Inbox') ORDER BY m.ROWID LIMIT 40""", (last,)).fetchall()
            if rows:
                self.state["mail_rowid"] = max(r[0] for r in rows)
            return [{"fonte": "Mail", "chi": f"{n} <{ad}>" if n else ad, "testo": f"{sub}\n{' '.join(str(summ).split())[:300]}", "ref": f"mail:{rid}"} for rid, n, ad, sub, summ in rows]
        finally:
            con.close()

    def _collect_whatsapp(self, baseline: bool) -> list[dict]:
        if not WA_DB.exists():
            return []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        last = self.state.get("wa_ts")
        if last is None or baseline:
            self.state["wa_ts"] = now
            return []
        con = sqlite3.connect(f"file:{WA_DB}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute("SELECT id, ts, chat, sender, text FROM messages WHERE source = 'live' AND from_me = 0 AND ts > ? ORDER BY ts LIMIT 40", (last,)).fetchall()
        finally:
            con.close()
        if rows:
            self.state["wa_ts"] = max(r[1] for r in rows)
        return [{"fonte": "WhatsApp", "chi": chat if sender == chat else f"{sender} (gruppo {chat})", "testo": text[:300], "ref": f"wa:{rid}"} for rid, ts, chat, sender, text in rows]

    def _collect_sms(self, baseline: bool) -> list[dict]:
        try:
            import importlib
            from avatar.plugins import registry
            registry.load()
            sms = importlib.import_module("avatar_plugins.sms")
        except Exception:
            return []
        if not sms.DB.exists():
            return []
        last = self.state.get("sms_rowid")
        if last is None or baseline:
            top = sms._query("", (), 1)   # può fallire senza «Accesso completo al disco»: l'errore arriva a scan()
            self.state["sms_rowid"] = top[0]["id"] if top else 0
            return []
        rows = sms._query("AND m.is_from_me = 0 AND m.ROWID > ?", (last,), 40)
        if rows:
            self.state["sms_rowid"] = max(r["id"] for r in rows)
        return [{"fonte": "Messaggi", "chi": r["chi"], "testo": r["testo"][:300], "ref": f"sms:{r['id']}"} for r in rows]

    def _collect_telegram(self, baseline: bool) -> list[dict]:
        try:
            from avatar import telegram_client as tg
            tg.credentials()
        except Exception:
            return []
        if not (Path(str(tg.SESSION) + ".session")).exists():
            return []
        state = self.state.setdefault("tg", {})
        out: list[dict] = []

        async def go(client):
            async for d in client.iter_dialogs(limit=60):
                if d.is_channel and not d.is_group:
                    continue
                key = str(d.id)
                top = d.message.id if d.message else 0
                last = state.get(key)
                if last is None or baseline:
                    state[key] = top
                    continue
                if top <= last:
                    continue
                async for m in client.iter_messages(d.entity, min_id=last, limit=20):
                    if m.out or not (m.message or "").strip():
                        continue
                    who = d.name
                    if d.is_group:
                        try:
                            s = await m.get_sender()
                            who = f"{getattr(s, 'first_name', '') or getattr(s, 'title', '')} (gruppo {d.name})"
                        except Exception:
                            pass
                    out.append({"fonte": "Telegram", "chi": who, "testo": m.message[:300], "ref": f"tg:{d.id}:{m.id}"})
                state[key] = top

        try:
            tg.run(go)
        except Exception as err:
            print(f"[monitor] telegram: {err}")
        return out

    # ── valutazione ──────────────────────────────────────────────────────
    def _classify(self, items: list[dict]) -> list[dict]:
        candidates = [it for it in items if KEYWORDS.search(it["testo"]) or it["fonte"] == "Mail" or "gruppo" not in it["chi"]]
        if not candidates:
            return []
        rules = self.settings.get("monitor_regole") or DEFAULT_RULES
        listing = "\n".join(f"{i}. [{it['fonte']}] {it['chi']}: {it['testo'].replace(chr(10), ' ')[:300]}" for i, it in enumerate(candidates[:30]))
        prompt = (f"Sei il filtro di attenzione di un assistente personale. L'utente vuole essere avvisato solo per: {rules}\n\n"
                  f"Messaggi nuovi:\n{listing}\n\n"
                  'Rispondi SOLO con un JSON: {"avvisi":[{"n":<numero>,"motivo":"<max 12 parole>","priorita":"alta|media"}]}. '
                  "Se nessuno merita attenzione: {\"avvisi\":[]}.")
        from avatar.quick_llm import ask
        raw = ask(prompt, system="Rispondi solo con JSON valido, senza testo attorno.")
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []
        alerts = []
        for a in data.get("avvisi", []):
            try:
                it = candidates[int(a["n"])]
            except Exception:
                continue
            alerts.append({**it, "motivo": str(a.get("motivo", ""))[:120], "priorita": "alta" if str(a.get("priorita", "")).lower() == "alta" else "media"})
        return alerts

    # ── ciclo ────────────────────────────────────────────────────────────
    def scan(self, baseline: bool = False) -> list[dict]:
        with _lock:
            first = self.state.get("mail_rowid") is None and self.state.get("wa_ts") is None and not self.state.get("tg")
            items: list[dict] = []
            for name, fn in (("Mail", self._collect_mail), ("WhatsApp", self._collect_whatsapp), ("Messaggi", self._collect_sms), ("Telegram", self._collect_telegram)):
                try:
                    items += fn(baseline or first)
                except Exception as err:
                    key = f"warned_{name}"
                    if not self.state.get(key):
                        self.state[key] = True
                        self.ui.write_log(f"SYS: Monitor, {name} non leggibile — {str(err)[:160]}")
                    print(f"[monitor] {name}: {err}")
            seen = set(self.state.get("seen", []))
            items = [it for it in items if it["ref"] not in seen]
            self.state.setdefault("seen", []).extend(it["ref"] for it in items)
            alerts = self._classify(items) if items else []
            for a in alerts:
                a["id"] = uuid.uuid4().hex[:6]
                a["quando"] = datetime.now().strftime("%d/%m %H:%M")
                a["aperto"] = True
                self.state.setdefault("alerts", []).append(a)
                self._notify(a)
            self._save()
            return alerts

    def _notify(self, a: dict) -> None:
        riga = f"{a['fonte']} · {a['chi']}: {a['motivo']}"
        self.ui.write_log(f"ERR: ATTENZIONE — {riga}")
        try:
            title = "Ava: richiede attenzione" if a["priorita"] != "alta" else "Ava: URGENTE"
            safe = lambda s: s.replace('"', "'").replace("\\", "")
            subprocess.run(["osascript", "-e", f'display notification "{safe(a["chi"] + ": " + a["testo"][:120])}" with title "{safe(title)}" subtitle "{safe(a["fonte"] + " — " + a["motivo"])}"'], timeout=10)
        except Exception:
            pass
        if self.settings.get("monitor_annuncia"):
            self.say(f"Attenzione: {a['fonte']}, {a['chi']}. {a['motivo']}.")

    def pending(self) -> list[dict]:
        return [a for a in self.state.get("alerts", []) if a.get("aperto")]

    def close(self, key: str) -> int:
        n = 0
        for a in self.state.get("alerts", []):
            if a.get("aperto") and (not key or key in (a.get("id"), a.get("chi")) or key.lower() in a.get("chi", "").lower()):
                a["aperto"] = False
                n += 1
        self._save()
        return n


monitor: Monitor | None = None
