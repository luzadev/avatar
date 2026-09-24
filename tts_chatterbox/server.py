"""Servizio Chatterbox (ambiente isolato .venv-chatterbox): sintesi vocale espressiva via HTTP locale."""
from __future__ import annotations

import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8792
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
_load = torch.load


def _patched(*a, **k):
    k.setdefault("map_location", torch.device(DEVICE))
    return _load(*a, **k)


torch.load = _patched
state = {"ready": False, "error": None}
model = None
lock = threading.Lock()


def load():
    global model
    try:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        m = ChatterboxMultilingualTTS.from_pretrained(device=DEVICE)
        m.generate("ciao", language_id="it")   # riscaldamento
        model = m
        state["ready"] = True
    except Exception as err:
        state["error"] = str(err)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        self._json(200, {"ready": state["ready"], "error": state["error"], "device": DEVICE, "sr": model.sr if model else None})

    def do_POST(self):
        if not state["ready"]:
            return self._json(503, {"error": state["error"] or "modello in caricamento"})
        n = int(self.headers.get("Content-Length", "0"))
        req = json.loads(self.rfile.read(n) or b"{}")
        text = str(req.get("text", "")).strip()
        if not text:
            return self._json(400, {"error": "testo vuoto"})
        kw = {"language_id": req.get("language", "it"), "exaggeration": float(req.get("exaggeration", 0.6)),
              "cfg_weight": float(req.get("cfg", 0.3)), "temperature": float(req.get("temperature", 0.8))}
        if req.get("ref"):
            kw["audio_prompt_path"] = req["ref"]
        try:
            with lock:
                wav = model.generate(text, **kw)
            data = wav.squeeze().cpu().numpy().astype("float32").tobytes()
        except Exception as err:
            return self._json(500, {"error": str(err)})
        self.send_response(200); self.send_header("Content-Type", "application/octet-stream")
        self.send_header("X-Sample-Rate", str(model.sr)); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)


threading.Thread(target=load, daemon=True).start()
print(f"[chatterbox] in ascolto su 127.0.0.1:{PORT}, dispositivo {DEVICE}", flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
