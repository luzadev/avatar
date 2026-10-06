"""Accesso remoto: app web (PWA) servita in HTTPS sulla rete locale, con abbinamento tramite QR e token.

- HTTPS (porta 8793) con certificato firmato da una CA locale generata al primo avvio (data/remote/): l'iPhone e
  Android richiedono un contesto sicuro per microfono e installazione; la CA va installata una volta sul telefono
  (scaricabile da http://IP:8791/ca.crt).
- WebSocket /ws: stato, registro, conferme e audio (WAV base64) verso il telefono; testo, interruzioni e conferme
  dal telefono. POST /stt: audio registrato dal telefono, decodificato con ffmpeg e trascritto da Whisper.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import secrets
import socket
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np

from avatar.settings import BASE_DIR, DATA_DIR, Settings

STATIC = BASE_DIR / "remote"
CERT_DIR = DATA_DIR / "remote"
HTTP_PORT, HTTPS_PORT = 8791, 8793


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def ensure_certs(ip: str) -> tuple[Path, Path, Path]:
    """CA locale + certificato del server con SAN per IP e nome .local; rigenerato se l'IP cambia."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    ca_key, ca_crt = CERT_DIR / "ca.key", CERT_DIR / "ca.crt"
    srv_key, srv_crt, srv_san = CERT_DIR / "server.key", CERT_DIR / "server.crt", CERT_DIR / "server.san"
    if not ca_crt.exists() or not (CERT_DIR / "ca.v2").exists():
        # Requisiti Apple per una CA di fiducia: basicConstraints critico, keyUsage keyCertSign, SKID; RSA 2048, SHA-256
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key), "-out", str(ca_crt),
                        "-days", "3650", "-subj", "/CN=LuZa CA locale/O=LuZa", "-sha256",
                        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                        "-addext", "subjectKeyIdentifier=hash"], check=True, capture_output=True)
        (CERT_DIR / "ca.v2").write_text("1")
        srv_san.unlink(missing_ok=True)   # il certificato del server va rifirmato con la nuova CA
    (CERT_DIR / "ca.cer").write_bytes(subprocess.run(["openssl", "x509", "-in", str(ca_crt), "-outform", "DER"], check=True, capture_output=True).stdout)
    host = socket.gethostname()
    san = f"IP:{ip},IP:127.0.0.1,DNS:{host},DNS:localhost"
    if not srv_crt.exists() or not srv_san.exists() or srv_san.read_text() != san:
        csr = CERT_DIR / "server.csr"
        ext = CERT_DIR / "server.ext"
        ext.write_text(f"subjectAltName={san}\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n"
                       "keyUsage=critical,digitalSignature,keyEncipherment\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n")
        subprocess.run(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(srv_key), "-out", str(csr), "-subj", "/CN=LuZa"], check=True, capture_output=True)
        subprocess.run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
                        "-out", str(srv_crt), "-days", "820", "-sha256", "-extfile", str(ext)], check=True, capture_output=True)
        srv_san.write_text(san)
    return ca_crt, srv_crt, srv_key


def wav_bytes(audio: np.ndarray, rate: int = 24000) -> bytes:
    pcm = np.clip(audio, -1, 1) if audio.dtype != np.int16 else audio
    data = (pcm * 32767).astype(np.int16) if pcm.dtype != np.int16 else pcm
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(data.tobytes())
    return buf.getvalue()


class RemoteServer:
    def __init__(self, assistant, settings: Settings, log=print) -> None:
        self.assistant, self.settings, self.log = assistant, settings, log
        self.token = str(settings.get("remote_token") or "")
        if not self.token:
            self.rotate_token()
        self.ip = lan_ip()
        self._clients: set = set()
        self._sse: set = set()          # code degli eventi per i client senza WebSocket (app sulla Home di iOS)
        self._poll: dict = {}           # cid -> (coda, ultimo accesso) per i client a interrogazione ripetuta
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.state, self.pending_confirm = "LISTENING", None
        self.pin, self.pin_expiry = "", 0.0
        self.apply_cb = None          # chiamata (nel thread Qt) dopo un salvataggio dal telefono
        self.error = ""

    # ── abbinamento ────────────────────────────────────────────────────────
    def rotate_token(self) -> str:
        self.token = secrets.token_urlsafe(18)
        self.settings.set("remote_token", self.token); self.settings.save()
        return self.token

    def new_pin(self) -> str:
        """PIN di 6 cifre, valido 10 minuti e monouso: sul telefono si digita al posto della chiave lunga."""
        self.pin = f"{secrets.randbelow(900000) + 100000}"
        self.pin_expiry = time.time() + 600
        return self.pin

    def urls(self) -> tuple[str, str, str, str]:
        base = f"https://{self.ip}:{HTTPS_PORT}"
        return base, self.new_pin(), f"{base}/?k={self.token}", f"{base}  (CA: http://{self.ip}:{HTTP_PORT}/ca.cer)"

    def has_clients(self) -> bool:
        now = time.time()
        for cid in [c for c, (_, t) in self._poll.items() if now - t > 90]:
            self._poll.pop(cid, None)
        return bool(self._clients or self._sse or self._poll)

    # ── avvio ──────────────────────────────────────────────────────────────
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="remote")
        self._thread.start()

    def stop(self) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self) -> None:
        import ssl
        from aiohttp import web
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            ca, crt, key = ensure_certs(self.ip)
        except Exception as err:
            self.error = f"certificati: {err}"
            self.log(f"ERR: Accesso remoto — {self.error}")
            return
        self.version = str(int(time.time()))

        @web.middleware
        async def mw(request, handler):
            try:
                resp = await handler(request)
            except web.HTTPException as ex:
                resp = ex
                self._access(request, ex.status)
                raise
            self._access(request, getattr(resp, "status", 0))
            if "Cache-Control" not in resp.headers:
                resp.headers["Cache-Control"] = "no-store"
            return resp

        app = web.Application(client_max_size=50 * 1024 * 1024, middlewares=[mw])
        app.router.add_get("/", self._index)
        app.router.add_get("/ws", self._ws)
        app.router.add_get("/events", self._events)
        app.router.add_get("/poll", self._poll_handler)
        app.router.add_post("/cmd", self._cmd)
        app.router.add_post("/tool", self._tool)
        app.router.add_post("/stt", self._stt)
        app.router.add_get("/img", self._img)
        app.router.add_get("/ca.crt", self._ca)
        app.router.add_get("/ca.cer", self._ca)
        app.router.add_get("/health", lambda r: web.json_response({"ok": True, "app": "LuZa"}))
        app.router.add_get("/pair", self._pair)
        app.router.add_get("/settings", self._settings_get)
        app.router.add_post("/settings", self._settings_post)
        app.router.add_get("/auth", lambda r: web.json_response({"ok": True}) if self._authed(r) else web.json_response({"ok": False}, status=401))
        app.router.add_static("/static", str(STATIC), show_index=False)
        app.router.add_get("/manifest.json", self._manifest)
        for name in ("sw.js", "icon-192.png", "icon-512.png"):
            app.router.add_get(f"/{name}", self._static_file(name))
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(str(crt), str(key))
        runner = web.AppRunner(app)
        self._loop.run_until_complete(runner.setup())
        try:
            self._loop.run_until_complete(web.TCPSite(runner, "0.0.0.0", HTTPS_PORT, ssl_context=ctx, reuse_address=True).start())
            self._loop.run_until_complete(web.TCPSite(runner, "0.0.0.0", HTTP_PORT, reuse_address=True).start())   # solo CA e reindirizzo
        except Exception as err:
            self.error = str(err)
            self.log(f"ERR: Accesso remoto non avviato: porta {HTTPS_PORT} occupata (c'è un'altra LuZa aperta?). {err}")
            return
        self.log(f"SYS: Accesso remoto pronto su https://{self.ip}:{HTTPS_PORT} (CA: http://{self.ip}:{HTTP_PORT}/ca.crt)")
        try:
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(runner.cleanup())

    # ── handler ────────────────────────────────────────────────────────────
    def _authed(self, request) -> bool:
        k = request.query.get("k") or request.headers.get("Authorization", "").replace("Bearer ", "") or request.cookies.get("luza_k", "")
        return secrets.compare_digest(k or "", self.token)

    def _access(self, request, status: int) -> None:
        try:
            with open(CERT_DIR / "access.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {request.remote} {request.method} {request.path} {status} {'https' if request.secure else 'http'} | {request.headers.get('User-Agent', '')[:90]}\n")
        except Exception:
            pass

    async def _index(self, request):
        from aiohttp import web
        if request.secure or request.scheme == "https":
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("/static/app.js", f"/static/app.js?v={self.version}")
            resp = web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})
            if self._authed(request):
                # cookie di abbinamento: consente al manifesto di includere la chiave (app sulla schermata Home)
                resp.set_cookie("luza_k", self.token, max_age=365 * 86400, secure=True, httponly=True, samesite="Lax")
            return resp
        raise web.HTTPFound(f"https://{self.ip}:{HTTPS_PORT}/" + (f"?k={request.query['k']}" if request.query.get("k") else ""))

    async def _manifest(self, request):
        """Il manifesto della PWA: l'indirizzo di avvio porta la chiave solo a chi si è già abbinato (cookie)."""
        from aiohttp import web
        d = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
        d["start_url"] = f"/?k={self.token}" if self._authed(request) else "/"
        return web.Response(text=json.dumps(d), content_type="application/manifest+json", headers={"Cache-Control": "no-store"})

    def _static_file(self, name: str):
        from aiohttp import web
        async def h(request):
            return web.FileResponse(STATIC / name, headers={"Cache-Control": "no-store"})
        return h

    async def _img(self, request):
        """Consegna un'immagine prodotta dai plugin (solo file dentro la cartella data)."""
        from aiohttp import web
        if not self._authed(request):
            raise web.HTTPUnauthorized()
        try:
            path = Path(request.query.get("p", "")).resolve()
            path.relative_to(DATA_DIR.resolve())
        except Exception:
            raise web.HTTPForbidden()
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})

    def on_image(self, path: str) -> None:
        from urllib.parse import quote
        if not self.has_clients():
            return
        p = Path(path)
        self.broadcast({"type": "image", "url": f"/img?k={self.token}&p={quote(str(p))}", "name": p.name})

    def on_file(self, path: str) -> None:
        from urllib.parse import quote
        if not self.has_clients():
            return
        p = Path(path)
        self.broadcast({"type": "file", "url": f"/img?k={self.token}&p={quote(str(p))}", "name": p.name})

    async def _ca(self, request):
        """CA in formato DER (.cer): è quello che iOS e Android installano senza storie."""
        from aiohttp import web
        return web.FileResponse(CERT_DIR / "ca.cer", headers={"Content-Type": "application/x-x509-ca-cert",
                                                               "Content-Disposition": 'attachment; filename="LuZa-CA.cer"'})

    async def _pair(self, request):
        from aiohttp import web
        pin = (request.query.get("pin") or "").strip()
        if pin and self.pin and time.time() < self.pin_expiry and secrets.compare_digest(pin, self.pin):
            self.pin = ""   # monouso
            self.log("SYS: Telefono abbinato con il PIN.")
            return web.json_response({"token": self.token})
        await asyncio.sleep(1.0)   # rallenta i tentativi
        return web.json_response({"error": "PIN non valido o scaduto: riapri Remote Control sul Mac"}, status=401)

    # ── impostazioni dal telefono: motore, modello, ragionamento, voce ─────
    SETTING_KEYS = ("provider", "effort", "mlx_model", "mlx_thinking", "claudecode_model", "local_model",
                    "tts_engine", "kokoro_voice", "voicebox_profile_id", "immagini_famiglia", "immagini_modello")

    def _options(self) -> dict:
        from avatar.tts import KOKORO_VOICES
        from avatar.engines.mlx_engine import cached_models
        opts = {
            "provider": [("anthropic", "Claude (cloud)"), ("claudecode", "Claude Code"), ("mlx", "Modello interno (MLX)"), ("local", "Server locale (vLLM)")],
            "effort": [("low", "Veloce"), ("medium", "Bilanciata"), ("high", "Approfondita")],
            "mlx_model": [(m, m.split("/")[-1]) for m in cached_models()],
            "mlx_thinking": [("auto", "Automatico"), ("off", "Spento"), ("on", "Acceso")],
            "claudecode_model": [("sonnet", "Sonnet"), ("opus", "Opus"), ("haiku", "Haiku")],
            "tts_engine": [("kokoro", "Kokoro (locale, rapida)"), ("qwen", "Qwen3-TTS (clonata, in LuZa)"), ("voicebox", "Voicebox (clonata)"), ("elevenlabs", "ElevenLabs"), ("chatterbox", "Chatterbox"), ("system", "Voce di sistema")],
            "kokoro_voice": list(KOKORO_VOICES.items()),
            "immagini_famiglia": [("z-image-turbo", "Z-Image Turbo"), ("schnell", "FLUX schnell"), ("dev", "FLUX dev"), ("qwen", "Qwen-Image")],
        }
        try:
            import sys as _s; _s.path.insert(0, str(BASE_DIR / "plugins"))
            import importlib; opts["immagini_modello"] = [("", "predefinito")] + [(m, m.split("/")[-1]) for m in importlib.import_module("immagini").cached_image_models()]
        except Exception:
            opts["immagini_modello"] = [("", "predefinito")]
        try:
            from avatar.tts import VoiceboxVoice
            opts["voicebox_profile_id"] = VoiceboxVoice.list_profiles() if VoiceboxVoice.alive() else []
        except Exception:
            opts["voicebox_profile_id"] = []
        return opts

    async def _settings_get(self, request):
        from aiohttp import web
        if not self._authed(request):
            raise web.HTTPUnauthorized()
        s = Settings()
        values = {k: s.get(k) for k in self.SETTING_KEYS}
        opts = await asyncio.get_event_loop().run_in_executor(None, self._options)
        return web.json_response({"values": values, "options": opts, "assistant": getattr(self.assistant, "name", "LuZa")})

    async def _settings_post(self, request):
        from aiohttp import web
        if not self._authed(request):
            raise web.HTTPUnauthorized()
        d = await request.json()
        vals = {k: v for k, v in d.items() if k in self.SETTING_KEYS and isinstance(v, (str, int, float, bool))}
        if not vals:
            return web.json_response({"ok": False, "error": "nessun valore"})
        self.settings.update(vals)
        self.log("SYS: Impostazioni cambiate dal telefono: " + ", ".join(f"{k}={v}" for k, v in vals.items()))
        if self.apply_cb:
            self.apply_cb()
        return web.json_response({"ok": True})

    async def _stt(self, request):
        from aiohttp import web
        if not self._authed(request):
            raise web.HTTPUnauthorized()
        raw = await request.read()
        if len(raw) < 1000:
            return web.json_response({"text": "", "error": "audio vuoto"})
        try:
            proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
                                  input=raw, capture_output=True, timeout=60)
            pcm = np.frombuffer(proc.stdout, dtype=np.int16)
        except Exception as err:
            return web.json_response({"text": "", "error": f"decodifica audio: {err}"})
        if pcm.size < 1600:
            return web.json_response({"text": "", "error": "audio troppo breve"})
        text = await asyncio.get_event_loop().run_in_executor(None, self.assistant.stt.transcribe, pcm)
        text = (text or "").strip()
        if request.query.get("run") == "1" and text:
            self.assistant.ui.write_log(f"You (telefono): {text}")
            self.assistant.handle_text(text, origin="remote")
        return web.json_response({"text": text})

    async def _ws(self, request):
        from aiohttp import web
        if not self._authed(request):
            raise web.HTTPUnauthorized()
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        self._clients.add(ws)
        self.log("SYS: Telefono collegato.")
        try:
            await ws.send_json({"type": "state", "state": self.state})
            if self.pending_confirm:
                await ws.send_json({"type": "confirm", **self.pending_confirm})
            async for msg in ws:
                if msg.type != 1:   # WSMsgType.TEXT
                    continue
                try:
                    d = json.loads(msg.data)
                except Exception:
                    continue
                if d.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
                else:
                    self._command(d)
        finally:
            self._clients.discard(ws)
            self.log("SYS: Telefono scollegato.")
        return ws

    def _command(self, d: dict) -> None:
        t = d.get("type")
        if t == "say" and str(d.get("text", "")).strip():
            text = str(d["text"]).strip()
            self.assistant.ui.write_log(f"You (telefono): {text}")
            self.assistant.handle_text(text, origin="remote")
        elif t == "interrupt":
            self.assistant.interrupt()
        elif t == "confirm":
            from core import confirm
            confirm.resolve(bool(d.get("ok")))

    async def _tool(self, request):
        """Esecuzione di uno strumento dentro l'app per conto del server MCP di Claude Code (solo da questo Mac)."""
        from aiohttp import web
        if request.remote not in ("127.0.0.1", "::1") or not self._authed(request):
            raise web.HTTPForbidden()
        d = await request.json()
        from avatar.plugins import registry
        name, args = str(d.get("name", "")), d.get("arguments") or {}
        if not registry.has(name):
            return web.json_response({"found": False})
        res = await asyncio.get_event_loop().run_in_executor(None, registry.run, name, args)
        return web.json_response({"found": True, "result": res})

    async def _cmd(self, request):
        from aiohttp import web
        if not self._authed(request):
            raise web.HTTPUnauthorized()
        try:
            self._command(await request.json())
        except Exception as err:
            return web.json_response({"ok": False, "error": str(err)})
        return web.json_response({"ok": True})

    async def _events(self, request):
        """Server-Sent Events: stessi messaggi del WebSocket, su una normale risposta HTTPS."""
        from aiohttp import web
        if not self._authed(request):
            raise web.HTTPUnauthorized()
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        await resp.prepare(request)
        q: asyncio.Queue = asyncio.Queue()
        self._sse.add(q)
        self.log("SYS: Telefono collegato (eventi).")
        try:
            await resp.write(f"data: {json.dumps({'type': 'state', 'state': self.state})}\n\n".encode())
            if self.pending_confirm:
                await resp.write(f"data: {json.dumps({'type': 'confirm', **self.pending_confirm}, ensure_ascii=False)}\n\n".encode())
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=20)
                    await resp.write(f"data: {data}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError, Exception):
            pass
        finally:
            self._sse.discard(q)
            self.log("SYS: Telefono scollegato (eventi).")
        return resp

    async def _poll_handler(self, request):
        """Interrogazione ripetuta: risponde con gli eventi in coda, aspettando al massimo 15 s. Solo richieste brevi."""
        from aiohttp import web
        if not self._authed(request):
            raise web.HTTPUnauthorized()
        cid = request.query.get("cid") or ""
        if not cid:
            return web.json_response({"events": []})
        new = cid not in self._poll
        q = self._poll.get(cid, (asyncio.Queue(), 0))[0]
        self._poll[cid] = (q, time.time())
        events = []
        if new:
            self.log("SYS: Telefono collegato (interrogazione).")
            events.append({"type": "state", "state": self.state})
            if self.pending_confirm:
                events.append({"type": "confirm", **self.pending_confirm})
        else:
            try:
                events.append(json.loads(await asyncio.wait_for(q.get(), timeout=15)))
            except asyncio.TimeoutError:
                pass
        while not q.empty():
            events.append(json.loads(q.get_nowait()))
        return web.json_response({"events": events})

    # ── verso i telefoni (chiamabili da qualunque thread) ──────────────────
    def broadcast(self, payload: dict) -> None:
        if not (self._clients or self._sse) or not self._loop:
            return
        data = json.dumps(payload, ensure_ascii=False)

        async def go():
            for ws in list(self._clients):
                try:
                    await ws.send_str(data)
                except Exception:
                    self._clients.discard(ws)
            for q in list(self._sse):
                q.put_nowait(data)
            for q, _ in list(self._poll.values()):
                q.put_nowait(data)
        try:
            asyncio.run_coroutine_threadsafe(go(), self._loop)
        except Exception:
            pass

    def on_state(self, state: str) -> None:
        self.state = state
        self.broadcast({"type": "state", "state": state})

    def on_log(self, text: str) -> None:
        self.broadcast({"type": "log", "text": text})

    def on_confirm(self, title: str, detail: str) -> None:
        self.pending_confirm = {"title": title, "detail": detail}
        self.broadcast({"type": "confirm", "title": title, "detail": detail})

    def on_confirm_hide(self) -> None:
        self.pending_confirm = None
        self.broadcast({"type": "confirm_hide"})

    def on_audio(self, text: str, audio: np.ndarray, rate: int = 24000) -> None:
        if not self._clients:
            return
        self.broadcast({"type": "audio", "text": text, "wav": base64.b64encode(wav_bytes(audio, rate)).decode()})

    def on_turn_end(self) -> None:
        self.broadcast({"type": "audio_end"})
