"""Home Assistant: dispositivi, stati, accensione/spegnimento, regolazioni, scene, servizi e Assist.

Usa l'API REST con un token di accesso a lunga durata (Profilo > Sicurezza in Home Assistant),
salvato nel portachiavi. Indirizzo e token si impostano nella scheda "Casa" delle impostazioni.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import confirm_or_param, CONFERMATO  # noqa: E402
from avatar.settings import Settings  # noqa: E402

DOMINI_IT = {"light": "luce", "switch": "interruttore", "fan": "ventilatore", "climate": "clima", "cover": "tapparella",
             "media_player": "media", "lock": "serratura", "sensor": "sensore", "binary_sensor": "sensore", "scene": "scena",
             "script": "script", "automation": "automazione", "vacuum": "aspirapolvere", "camera": "telecamera",
             "alarm_control_panel": "allarme", "humidifier": "umidificatore", "water_heater": "scaldabagno", "input_boolean": "opzione"}
AZIONABILI = ("light", "switch", "fan", "climate", "cover", "media_player", "lock", "scene", "script", "automation",
              "vacuum", "humidifier", "water_heater", "input_boolean", "siren", "remote")
DELICATI = ("lock", "alarm_control_panel", "siren")   # richiedono conferma
_cache: dict = {"t": 0.0, "items": []}


def _cfg() -> tuple[str, str]:
    s = Settings()
    url = str(s.get("homeassistant_url") or "").strip().rstrip("/")
    tok = s.get_secret("homeassistant_token")
    if not url or not tok:
        raise RuntimeError("Home Assistant non configurato: indirizzo e token di accesso nella scheda Casa delle impostazioni.")
    return url, tok


def api(path: str, data: dict | None = None, timeout: int = 15):
    url, tok = _cfg()
    req = urllib.request.Request(url + path, method="POST" if data is not None else "GET",
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                                 data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError("Home Assistant rifiuta il token (401): rigeneralo dal tuo profilo e incollalo nelle impostazioni.")
        raise RuntimeError(f"Home Assistant: HTTP {e.code} {e.read()[:200].decode(errors='replace')}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Home Assistant non raggiungibile ({e.reason}).")


def verifica() -> str:
    c = api("/api/config")
    return f"Collegato a «{c.get('location_name')}», Home Assistant {c.get('version')}, {len(entita(force=True))} entità."


def entita(force: bool = False) -> list[dict]:
    """Elenco entità con nome, stato e area (via template; ripiego su /api/states)."""
    if not force and time.time() - _cache["t"] < 60 and _cache["items"]:
        return _cache["items"]
    items = []
    try:
        tpl = "{% for s in states %}{{ s.entity_id }}\t{{ s.name }}\t{{ s.state }}\t{{ area_name(s.entity_id) or '' }}\n{% endfor %}"
        url, tok = _cfg()
        req = urllib.request.Request(url + "/api/template", method="POST", data=json.dumps({"template": tpl}).encode(),
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            for line in r.read().decode().splitlines():
                p = line.split("\t")
                if len(p) >= 3:
                    items.append({"id": p[0], "nome": p[1], "stato": p[2], "area": p[3] if len(p) > 3 else ""})
    except Exception:
        for s in api("/api/states") or []:
            items.append({"id": s["entity_id"], "nome": s.get("attributes", {}).get("friendly_name", s["entity_id"]), "stato": s.get("state", ""), "area": ""})
    _cache.update(t=time.time(), items=items)
    return items


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def trova(nome: str, dominio: str = "") -> dict | None:
    """Entità per id, nome esatto, nome parziale o parole in comune (nome + area)."""
    nome = (nome or "").strip()
    if not nome:
        return None
    items = entita()
    if dominio:
        items = [e for e in items if e["id"].startswith(dominio + ".")]
    for e in items:
        if e["id"] == nome:
            return e
    n = _norm(nome)
    for e in items:
        if _norm(e["nome"]) == n:
            return e
    words = set(n.split())
    best, score = None, 0
    for e in items:
        hay = set(_norm(e["nome"] + " " + e["area"] + " " + e["id"].split(".", 1)[-1].replace("_", " ")).split())
        dom_it = DOMINI_IT.get(e["id"].split(".")[0], "")
        if dom_it:
            hay.add(dom_it)
        common = len(words & hay)
        if n in _norm(e["nome"]):
            common += 2
        if common > score:
            best, score = e, common
    return best if score > 0 else None


def _lista(items: list[dict], limite: int) -> str:
    out = []
    for e in items[:limite]:
        area = f" ({e['area']})" if e["area"] else ""
        out.append(f"- {e['nome']}{area}: {e['stato']} [{e['id']}]")
    if len(items) > limite:
        out.append(f"… e altre {len(items) - limite}")
    return "\n".join(out)


def dispositivi(params: dict, ctx: dict) -> str:
    filtro = _norm(str(params.get("filtro", "")))
    dominio = str(params.get("dominio", "")).strip().lower()
    items = entita(force=True)
    if dominio:
        items = [e for e in items if e["id"].split(".")[0] == dominio or DOMINI_IT.get(e["id"].split(".")[0]) == dominio]
    else:
        items = [e for e in items if e["id"].split(".")[0] in AZIONABILI or e["id"].split(".")[0] in ("sensor", "binary_sensor", "person", "weather")]
    if filtro:
        items = [e for e in items if filtro in _norm(e["nome"] + " " + e["area"] + " " + e["id"])]
    if not items:
        return "Nessun dispositivo corrisponde." + ("" if filtro else " Home Assistant non espone entità azionabili.")
    if not filtro and not dominio:
        # panoramica compatta per area e dominio
        by_area: dict[str, list] = {}
        for e in items:
            if e["id"].split(".")[0] in AZIONABILI:
                by_area.setdefault(e["area"] or "senza area", []).append(e)
        out = [f"Dispositivi azionabili: {sum(len(v) for v in by_area.values())} in {len(by_area)} aree."]
        for area, lst in sorted(by_area.items()):
            out.append(f"{area}: " + ", ".join(f"{e['nome']} ({e['stato']})" for e in lst[:12]) + (f", +{len(lst)-12}" if len(lst) > 12 else ""))
        return "\n".join(out) + "\nPer i dettagli usa casa_dispositivi con filtro o dominio."
    return _lista(items, max(5, min(int(params.get("limite") or 40), 100)))


def stato(params: dict, ctx: dict) -> str:
    e = trova(str(params.get("entita", "")))
    if not e:
        return f"Non trovo '{params.get('entita')}' in Home Assistant. Prova casa_dispositivi con un filtro."
    s = api(f"/api/states/{e['id']}") or {}
    a = s.get("attributes", {})
    utili = {k: v for k, v in a.items() if k in ("brightness", "color_temp", "rgb_color", "temperature", "current_temperature", "hvac_mode",
             "humidity", "current_position", "volume_level", "media_title", "battery_level", "unit_of_measurement", "device_class", "percentage")}
    stato_txt = s.get("state", "?")
    if a.get("unit_of_measurement"):
        stato_txt += " " + str(a["unit_of_measurement"])
    if "brightness" in a and a["brightness"] is not None:
        utili["luminosita_pct"] = round(int(a["brightness"]) * 100 / 255)
    det = ", ".join(f"{k}={v}" for k, v in utili.items() if k not in ("unit_of_measurement",))
    return f"{e['nome']}{' (' + e['area'] + ')' if e['area'] else ''}: {stato_txt}" + (f" — {det}" if det else "") + f"\nAggiornato: {str(s.get('last_updated', ''))[:19].replace('T', ' ')}"


def _call(dominio: str, servizio: str, dati: dict) -> str:
    api(f"/api/services/{dominio}/{servizio}", dati)
    return "ok"


def _accendi_spegni(params: dict, ctx: dict, on: bool) -> str:
    nome = str(params.get("entita", ""))
    e = trova(nome)
    if not e:
        return f"Non trovo '{nome}'. Prova casa_dispositivi con un filtro."
    dom = e["id"].split(".")[0]
    if dom not in AZIONABILI:
        return f"{e['nome']} è un {DOMINI_IT.get(dom, dom)}: non si può accendere o spegnere."
    verbo = "Accendere" if on else "Spegnere"

    def do() -> str:
        _cache["t"] = 0
        if dom == "lock":
            _call("lock", "unlock" if on else "lock", {"entity_id": e["id"]})
            return f"{e['nome']}: {'sbloccata' if on else 'bloccata'}."
        if dom == "cover":
            _call("cover", "open_cover" if on else "close_cover", {"entity_id": e["id"]})
            return f"{e['nome']}: {'apro' if on else 'chiudo'}."
        _call("homeassistant", "turn_on" if on else "turn_off", {"entity_id": e["id"]})
        return f"{e['nome']}: {'accesa' if on else 'spenta'}."

    if dom in DELICATI:
        return confirm_or_param(ctx, params, "casa_delicato", f"{verbo} {e['nome']}?", f"{DOMINI_IT.get(dom, dom)} [{e['id']}]", do)
    return do()


def accendi(params: dict, ctx: dict) -> str:
    return _accendi_spegni(params, ctx, True)


def spegni(params: dict, ctx: dict) -> str:
    return _accendi_spegni(params, ctx, False)


def imposta(params: dict, ctx: dict) -> str:
    e = trova(str(params.get("entita", "")))
    if not e:
        return f"Non trovo '{params.get('entita')}'."
    dom = e["id"].split(".")[0]
    fatto = []
    _cache["t"] = 0
    if dom == "light":
        dati = {"entity_id": e["id"]}
        if params.get("luminosita") is not None:
            dati["brightness_pct"] = max(0, min(100, int(params["luminosita"])))
            fatto.append(f"luminosità {dati['brightness_pct']}%")
        if params.get("colore"):
            dati["color_name"] = str(params["colore"]).strip()
            fatto.append(f"colore {dati['color_name']}")
        if params.get("temperatura") is not None:
            dati["kelvin"] = int(params["temperatura"])
            fatto.append(f"{dati['kelvin']} K")
        _call("light", "turn_on", dati)
    elif dom == "climate":
        if params.get("temperatura") is not None:
            _call("climate", "set_temperature", {"entity_id": e["id"], "temperature": float(params["temperatura"])})
            fatto.append(f"temperatura {params['temperatura']}°")
        if params.get("modo"):
            _call("climate", "set_hvac_mode", {"entity_id": e["id"], "hvac_mode": str(params["modo"])})
            fatto.append(f"modalità {params['modo']}")
    elif dom == "cover":
        if params.get("posizione") is not None:
            _call("cover", "set_cover_position", {"entity_id": e["id"], "position": max(0, min(100, int(params["posizione"])))})
            fatto.append(f"posizione {params['posizione']}%")
    elif dom == "media_player":
        if params.get("volume") is not None:
            _call("media_player", "volume_set", {"entity_id": e["id"], "volume_level": max(0, min(100, int(params["volume"]))) / 100})
            fatto.append(f"volume {params['volume']}%")
        if params.get("modo") in ("play", "pause", "stop", "next", "previous"):
            svc = {"play": "media_play", "pause": "media_pause", "stop": "media_stop", "next": "media_next_track", "previous": "media_previous_track"}[params["modo"]]
            _call("media_player", svc, {"entity_id": e["id"]})
            fatto.append(params["modo"])
    elif dom == "fan":
        if params.get("luminosita") is not None or params.get("posizione") is not None:
            pct = int(params.get("luminosita") if params.get("luminosita") is not None else params.get("posizione"))
            _call("fan", "set_percentage", {"entity_id": e["id"], "percentage": max(0, min(100, pct))})
            fatto.append(f"velocità {pct}%")
    if not fatto:
        return f"Per {e['nome']} ({DOMINI_IT.get(dom, dom)}) non ho una regolazione applicabile con questi parametri."
    return f"{e['nome']}: " + ", ".join(fatto) + "."


def scena(params: dict, ctx: dict) -> str:
    nome = str(params.get("nome", ""))
    e = trova(nome, "scene") or trova(nome, "script") or trova(nome, "automation")
    if not e:
        return f"Non trovo la scena o lo script '{nome}'."
    dom = e["id"].split(".")[0]
    _call(dom, "turn_on" if dom == "scene" else ("trigger" if dom == "automation" else "turn_on"), {"entity_id": e["id"]})
    return f"{DOMINI_IT.get(dom, dom).capitalize()} «{e['nome']}» avviata."


def servizio(params: dict, ctx: dict) -> str:
    dom = str(params.get("dominio", "")).strip()
    svc = str(params.get("servizio", "")).strip()
    if not dom or not svc:
        return "Errore: servono dominio e servizio (es. light / turn_on)."
    dati = params.get("dati") or {}
    if isinstance(dati, str):
        try:
            dati = json.loads(dati)
        except Exception:
            return "Errore: 'dati' deve essere un oggetto JSON."
    if params.get("entita"):
        e = trova(str(params["entita"]))
        if not e:
            return f"Non trovo '{params['entita']}'."
        dati["entity_id"] = e["id"]

    def do() -> str:
        _cache["t"] = 0
        _call(dom, svc, dati)
        return f"Servizio {dom}.{svc} eseguito."

    if dom in DELICATI or dom in ("homeassistant", "hassio", "shell_command", "persistent_notification") and svc not in ("turn_on", "turn_off", "toggle"):
        return confirm_or_param(ctx, params, "casa_servizio", f"Eseguire {dom}.{svc}?", json.dumps(dati, ensure_ascii=False)[:300], do)
    return do()


def chiedi(params: dict, ctx: dict) -> str:
    testo = str(params.get("testo", "")).strip()
    if not testo:
        return "Errore: serve il testo del comando."
    r = api("/api/conversation/process", {"text": testo, "language": "it"}) or {}
    _cache["t"] = 0
    try:
        speech = r["response"]["speech"]["plain"]["speech"]
    except Exception:
        speech = ""
    kind = (r.get("response") or {}).get("response_type", "")
    if kind == "error":
        return f"Assist non ha capito: {speech or 'nessun dettaglio'}. Prova con casa_dispositivi e gli strumenti diretti."
    return speech or "Assist ha eseguito il comando senza commento."


# ── Musica e annunci (Alexa Media Player e altri lettori) ───────────────────
_services: dict = {"t": 0.0, "names": {}}
SORGENTI = {"spotify": "Spotify", "amazon": "Amazon Music", "amazon music": "Amazon Music", "tunein": "TuneIn", "radio": "TuneIn",
            "apple": "Apple Music", "apple music": "Apple Music", "deezer": "Deezer"}


def servizi() -> dict:
    if time.time() - _services["t"] > 300 or not _services["names"]:
        _services["names"] = {d["domain"]: list(d["services"].keys()) for d in (api("/api/services") or [])}
        _services["t"] = time.time()
    return _services["names"]


def _alexa(entity_id: str) -> str:
    """Nome del servizio notify dell'integrazione Alexa Media Player per questo lettore, o ''."""
    slug = entity_id.split(".", 1)[-1]
    return f"alexa_media_{slug}" if f"alexa_media_{slug}" in servizi().get("notify", []) else ""


def riproduci(params: dict, ctx: dict) -> str:
    e = trova(str(params.get("dispositivo", "")), "media_player")
    if not e:
        return f"Non trovo il lettore '{params.get('dispositivo')}'. Lettori disponibili: " + ", ".join(x["nome"] for x in entita() if x["id"].startswith("media_player.") and x["stato"] != "unavailable")
    brano = str(params.get("brano", "")).strip()
    if not brano:
        return "Errore: serve cosa riprodurre (brano, artista, playlist, stazione radio o URL)."
    if e["stato"] == "unavailable":
        return f"{e['nome']} non è raggiungibile al momento."
    sorgente = SORGENTI.get(str(params.get("sorgente", "")).strip().lower(), "")
    _cache["t"] = 0
    if _alexa(e["id"]):
        # comando vocale inoltrato ad Alexa, es. "riproduci Let It Go di Idina Menzel su Spotify"
        testo = brano if re.match(r"^(riproduci|metti|suona|fai partire)\b", brano.lower()) else f"riproduci {brano}"
        if sorgente and sorgente.lower() not in testo.lower():
            testo += f" su {sorgente}"
        _call("media_player", "play_media", {"entity_id": e["id"], "media_content_type": "custom", "media_content_id": testo})
        return f"Ho chiesto a {e['nome']}: «{testo}»."
    if brano.startswith(("http://", "https://")):
        _call("media_player", "play_media", {"entity_id": e["id"], "media_content_type": "music", "media_content_id": brano})
        return f"Su {e['nome']} parte l'URL indicato."
    return f"{e['nome']} non è un dispositivo Alexa: posso avviarci solo un URL (stream, file) con casa_riproduci, oppure prova casa_chiedi."


def annuncia(params: dict, ctx: dict) -> str:
    testo = str(params.get("testo", "")).strip()
    if not testo:
        return "Errore: serve il testo da far dire."
    nome = str(params.get("dispositivo", "")).strip()
    e = trova(nome, "media_player") if nome else None
    if nome and not e:
        return f"Non trovo il dispositivo '{nome}'."
    if e and not _alexa(e["id"]):
        return f"{e['nome']} non è un dispositivo Alexa: gli annunci vocali funzionano solo sugli Echo."
    svc = _alexa(e["id"]) if e else ("alexa_media_ovunque" if "alexa_media_ovunque" in servizi().get("notify", []) else "")
    if not svc:
        return "Nessun dispositivo Alexa disponibile per l'annuncio."
    tipo = "tts" if str(params.get("tipo", "")).lower() == "tts" else "announce"
    _call("notify", svc, {"message": testo, "data": {"type": tipo}})
    return f"Annuncio inviato a {e['nome'] if e else 'tutti gli Echo'}."


TOOLS = [
    {"name": "casa_dispositivi", "description": "Elenca i dispositivi di casa (Home Assistant) con stato e stanza. Senza parametri: panoramica per stanza. Con filtro (parola nel nome/stanza) o dominio (luce, interruttore, clima, tapparella, media, sensore, scena, …) l'elenco dettagliato con gli id.",
     "parameters": {"type": "object", "properties": {"filtro": {"type": "string"}, "dominio": {"type": "string"}, "limite": {"type": "integer"}}}, "run": dispositivi},
    {"name": "casa_stato", "description": "Stato e dettagli di un dispositivo o sensore di casa, per nome (es. 'luce cucina', 'temperatura salotto') o id.",
     "parameters": {"type": "object", "properties": {"entita": {"type": "string"}}, "required": ["entita"]}, "run": stato},
    {"name": "casa_accendi", "description": "Accende un dispositivo di casa per nome (luce, presa, ventilatore, clima, media, apre una tapparella, sblocca una serratura con conferma).",
     "parameters": {"type": "object", "properties": {"entita": {"type": "string"}, "confermato": CONFERMATO}, "required": ["entita"]}, "run": accendi},
    {"name": "casa_spegni", "description": "Spegne un dispositivo di casa per nome (chiude una tapparella, blocca una serratura con conferma).",
     "parameters": {"type": "object", "properties": {"entita": {"type": "string"}, "confermato": CONFERMATO}, "required": ["entita"]}, "run": spegni},
    {"name": "casa_imposta", "description": "Regola un dispositivo: luminosita (0-100) e colore (nome inglese es. red, warm white) o temperatura in kelvin per le luci; temperatura in gradi e modo (heat/cool/off/auto) per il clima; posizione (0-100) per tapparelle; volume (0-100) e modo (play/pause/stop/next/previous) per i media; luminosita come velocità per i ventilatori.",
     "parameters": {"type": "object", "properties": {"entita": {"type": "string"}, "luminosita": {"type": "integer"}, "colore": {"type": "string"}, "temperatura": {"type": "number"}, "modo": {"type": "string"}, "posizione": {"type": "integer"}, "volume": {"type": "integer"}}, "required": ["entita"]}, "run": imposta},
    {"name": "casa_scena", "description": "Attiva una scena, uno script o un'automazione di Home Assistant per nome (es. 'serata film', 'buonanotte').",
     "parameters": {"type": "object", "properties": {"nome": {"type": "string"}}, "required": ["nome"]}, "run": scena},
    {"name": "casa_servizio", "description": "Chiama un servizio generico di Home Assistant (dominio + servizio, es. vacuum/start, notify/mobile_app) con dati JSON e opzionale entità per nome. Per casi non coperti dagli altri strumenti; serrature, allarmi e sirene chiedono conferma.",
     "parameters": {"type": "object", "properties": {"dominio": {"type": "string"}, "servizio": {"type": "string"}, "entita": {"type": "string"}, "dati": {"type": "object"}, "confermato": CONFERMATO}, "required": ["dominio", "servizio"]}, "run": servizio},
    {"name": "casa_riproduci", "description": "Riproduce musica su un lettore di casa: brano, artista, album, playlist, stazione radio o URL. Sugli Echo (Alexa) inoltra un comando vocale ('riproduci … su Spotify'); sorgente = spotify, amazon, tunein, apple, deezer (facoltativa). Per fermare o mettere in pausa usa casa_imposta con modo=pause/stop.",
     "parameters": {"type": "object", "properties": {"dispositivo": {"type": "string"}, "brano": {"type": "string"}, "sorgente": {"type": "string"}}, "required": ["dispositivo", "brano"]}, "run": riproduci},
    {"name": "casa_annuncia", "description": "Fa pronunciare un testo a un Echo (Alexa) di casa, o a tutti se non indichi il dispositivo: annunci tipo 'la cena è pronta'.",
     "parameters": {"type": "object", "properties": {"testo": {"type": "string"}, "dispositivo": {"type": "string"}, "tipo": {"type": "string", "enum": ["announce", "tts"]}}, "required": ["testo"]}, "run": annuncia},
    {"name": "casa_chiedi", "description": "Manda una frase in italiano ad Assist di Home Assistant (es. 'spegni tutte le luci del piano di sopra') e riporta la sua risposta. Utile per comandi su più dispositivi o quando non trovi l'entità.",
     "parameters": {"type": "object", "properties": {"testo": {"type": "string"}}, "required": ["testo"]}, "run": chiedi},
]
