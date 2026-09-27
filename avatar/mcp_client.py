"""Client MCP: collega server esterni (stdio o HTTP) e ne espone gli strumenti ai motori dell'app.

Configurazione in config/mcp.json, stesso formato di Claude Desktop:
{"mcpServers": {"nome": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/cartella"], "env": {}},
                "remoto": {"url": "https://esempio.it/mcp", "headers": {"Authorization": "Bearer …"}}}}
Gli strumenti compaiono come <nome>_<strumento>; con il motore Claude Code i server vengono passati direttamente a `claude`.
Opzioni per server: "tools": ["click", "browser_*"] (solo questi), "exclude": [...], "description_limit": 700 (caratteri
di descrizione per strumento: server come Cua Driver ne espongono decine con descrizioni lunghissime), "disabled": true.
"""
from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import subprocess
import threading
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any

from avatar.settings import CONFIG_DIR, DATA_DIR

CONFIG_FILE = CONFIG_DIR / "mcp.json"
PROTOCOL = "2025-06-18"
CALL_TIMEOUT = 120
EXTRA_PATH = ("/opt/homebrew/bin", "/opt/homebrew/opt/node@22/bin", "/usr/local/bin", str(Path.home() / ".local/bin"))


def load_config() -> dict[str, dict]:
    try:
        d = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in (d.get("mcpServers") or {}).items() if isinstance(v, dict)}
    except FileNotFoundError:
        return {}
    except Exception as err:
        raise RuntimeError(f"config/mcp.json non valido: {err}")


def save_config(text: str) -> dict[str, dict]:
    d = json.loads(text)
    if not isinstance(d, dict) or not isinstance(d.get("mcpServers", {}), dict):
        raise ValueError("serve un oggetto con la chiave mcpServers")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return load_config()


def _env_with_path(extra: dict | None) -> dict:
    env = dict(os.environ)
    env["PATH"] = ":".join([*EXTRA_PATH, env.get("PATH", "")])
    env.pop("CLAUDECODE", None)
    for k, v in (extra or {}).items():
        env[str(k)] = str(v)
    return env


class MCPServer:
    def __init__(self, name: str, cfg: dict) -> None:
        self.name, self.cfg = name, cfg
        self.tools: list[dict] = []
        self.error = ""
        self._id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._proc: subprocess.Popen | None = None
        self._session = ""
        self.stdio = bool(cfg.get("command"))

    # ── trasporto ──────────────────────────────────────────────────────────
    def start(self) -> None:
        if self.stdio:
            cmd = [str(self.cfg["command"]), *[str(a) for a in self.cfg.get("args", [])]]
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, bufsize=1, env=_env_with_path(self.cfg.get("env")), cwd=self.cfg.get("cwd") or None)
            threading.Thread(target=self._reader, daemon=True).start()
            threading.Thread(target=self._drain_stderr, daemon=True).start()
        res = self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "LuZa", "version": "1.0"}}, timeout=60)
        self._notify("notifications/initialized")
        self.server_info = res.get("serverInfo", {})
        self.tools = list(self._request("tools/list", {}, timeout=60).get("tools", []))

    def stop(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def _reader(self) -> None:
        proc = self._proc
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                mid = msg.get("id")
                if mid in self._pending:
                    self._pending[mid]["msg"] = msg
                    self._pending[mid]["ev"].set()
        except Exception:
            pass
        for p in list(self._pending.values()):
            p["msg"] = {"error": {"message": "server MCP terminato"}}
            p["ev"].set()

    def _drain_stderr(self) -> None:
        try:
            log = (DATA_DIR / "mcp").joinpath(f"{self.name}.log")
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                for line in self._proc.stderr:
                    f.write(line)
        except Exception:
            pass

    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    def _notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if self.stdio:
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()
        else:
            try:
                self._http(msg, timeout=15)
            except Exception:
                pass

    def _request(self, method: str, params: dict, timeout: float = CALL_TIMEOUT) -> dict:
        mid = self._next_id()
        msg = {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}
        if self.stdio:
            if not self._proc or self._proc.poll() is not None:
                raise RuntimeError(f"server MCP '{self.name}' non in esecuzione")
            slot = {"ev": threading.Event(), "msg": None}
            self._pending[mid] = slot
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()
            if not slot["ev"].wait(timeout):
                self._pending.pop(mid, None)
                raise RuntimeError(f"server MCP '{self.name}': nessuna risposta entro {int(timeout)} s")
            self._pending.pop(mid, None)
            resp = slot["msg"]
        else:
            resp = self._http(msg, timeout=timeout, want_id=mid)
        if resp is None:
            raise RuntimeError("risposta vuota")
        if "error" in resp:
            e = resp["error"]
            raise RuntimeError(e.get("message") if isinstance(e, dict) else str(e))
        return resp.get("result") or {}

    def _http(self, msg: dict, timeout: float, want_id: int | None = None) -> dict | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(self.cfg.get("headers") or {})}
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        req = urllib.request.Request(str(self.cfg["url"]), data=json.dumps(msg).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self._session = sid
            ctype = r.headers.get("Content-Type", "")
            body = r.read().decode("utf-8", errors="replace")
        if want_id is None:
            return None
        if "text/event-stream" in ctype:
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        m = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    if m.get("id") == want_id:
                        return m
            raise RuntimeError("nessuna risposta nello stream SSE")
        return json.loads(body) if body.strip() else None

    # ── strumenti ──────────────────────────────────────────────────────────
    def call(self, tool: str, args: dict) -> str:
        res = self._request("tools/call", {"name": tool, "arguments": args or {}})
        parts = []
        for c in res.get("content") or []:
            t = c.get("type")
            if t == "text":
                parts.append(str(c.get("text", "")))
            elif t == "image" and c.get("data"):
                cap = DATA_DIR / "captures"
                cap.mkdir(parents=True, exist_ok=True)
                ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(c.get("mimeType", ""), "png")
                p = cap / f"mcp-{self.name}-{int(time.time())}.{ext}"
                p.write_bytes(base64.b64decode(c["data"]))
                parts.append(f"IMMAGINE: {p}")
            elif t == "resource":
                r = c.get("resource") or {}
                parts.append(str(r.get("text") or r.get("uri") or ""))
        out = "\n".join(p for p in parts if p).strip()
        if res.get("isError"):
            return "Errore dello strumento: " + (out or "senza dettagli")
        if "structuredContent" in res and not out:
            out = json.dumps(res["structuredContent"], ensure_ascii=False)
        return out[:20000] or "Fatto."


def selected_tools(cfg: dict, tools: list[dict]) -> list[dict]:
    """Applica i filtri tools/exclude della configurazione all'elenco degli strumenti di un server."""
    inc, exc = cfg.get("tools") or [], cfg.get("exclude") or []
    out = []
    for t in tools:
        n = t["name"]
        if inc and not any(fnmatch.fnmatchcase(n, pat) for pat in inc):
            continue
        if exc and any(fnmatch.fnmatchcase(n, pat) for pat in exc):
            continue
        out.append(t)
    return out


def _safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)[:64].strip("_") or "strumento"


class MCPManager:
    """Avvia i server configurati e registra i loro strumenti nel registro dei plugin."""

    def __init__(self) -> None:
        self.servers: dict[str, MCPServer] = {}
        self.errors: dict[str, str] = {}
        self.registry = None
        self.log = print
        self._lock = threading.Lock()

    def start(self, registry, log=None) -> None:
        self.registry = registry
        if log:
            self.log = log
        threading.Thread(target=self.reload, daemon=True).start()

    def reload(self) -> None:
        with self._lock:
            self._stop_all()
            try:
                cfg = load_config()
            except Exception as err:
                self.errors["config"] = str(err)
                self.log(f"ERR: MCP — {err}")
                return
            self.errors.pop("config", None)
            for name, c in cfg.items():
                if c.get("disabled"):
                    continue
                srv = MCPServer(name, c)
                try:
                    srv.start()
                except Exception as err:
                    srv.stop()
                    self.errors[name] = str(err)
                    self.log(f"ERR: MCP «{name}» — {err}")
                    traceback.print_exc()
                    continue
                self.errors.pop(name, None)
                self.servers[name] = srv
                self._register(srv)
                self.log(f"SYS: MCP «{name}»: {len(srv.tools)} strumenti.")

    def _register(self, srv: MCPServer) -> None:
        if self.registry is None:
            return
        tools = []
        limit = int(srv.cfg.get("description_limit") or 700)
        for t in selected_tools(srv.cfg, srv.tools):
            tname = _safe(f"{srv.name}_{t['name']}")
            desc = " ".join((t.get("description") or t["name"]).split())
            if len(desc) > limit:
                desc = desc[:limit].rsplit(" ", 1)[0] + " …"
            tools.append({"name": tname, "description": f"[{srv.name}] {desc}",
                          "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                          "run": (lambda params, ctx, _s=srv, _n=t["name"]: _s.call(_n, params))})
        info = getattr(srv, "server_info", {}) or {}
        self.registry.add_external(f"mcp:{srv.name}", f"Server MCP {info.get('name') or srv.name}: {len(tools)} strumenti", tools)

    def _stop_all(self) -> None:
        for name, srv in list(self.servers.items()):
            if self.registry is not None:
                self.registry.remove_module(f"mcp:{name}")
            srv.stop()
        self.servers.clear()

    def stop(self) -> None:
        with self._lock:
            self._stop_all()

    def status(self) -> str:
        lines = []
        for name, srv in self.servers.items():
            sel = selected_tools(srv.cfg, srv.tools)
            lines.append(f"• {name}: {len(sel)} strumenti attivi su {len(srv.tools)} — " + ", ".join(t["name"] for t in sel[:8]) + (" …" if len(sel) > 8 else ""))
        for name, err in self.errors.items():
            lines.append(f"✗ {name}: {err}")
        return "\n".join(lines) or "Nessun server MCP configurato."


manager = MCPManager()
