"""Motore Claude Code: avvia `claude -p` in streaming JSON con un server MCP per la memoria."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from memory import memory_manager as mm

from avatar.memory_tools import memory_prompt
from .base import Emit, History, persona_text, today_label, user_block

MEMORY_TOOLS = ["mcp__avatar"]  # tutti gli strumenti del server MCP dell'app (memoria e plugin)
ACCESS = {
    "chat": {"tools": "WebSearch,WebFetch", "allowed": ["WebSearch", "WebFetch"], "mode": None},
    "read": {"tools": "Read,Glob,Grep,WebSearch,WebFetch", "allowed": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"], "mode": None},
    "full": {"tools": "default",
             "allowed": ["Bash", "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep", "WebSearch", "WebFetch", "Agent"],
             "mode": "acceptEdits"},
}
CANDIDATES = [Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude"),
              Path.home() / ".claude/local/claude", Path.home() / ".npm-global/bin/claude"]
MCP_SCRIPT = Path(__file__).resolve().parent.parent / "memory_mcp.py"


def resolve_claude(custom: str = "") -> str | None:
    if custom.strip():
        return custom.strip() if Path(custom.strip()).exists() else None
    found = shutil.which("claude")
    if found:
        return found
    for p in CANDIDATES:
        if p.exists():
            return str(p)
    return None


def claude_env(config_dir: str) -> dict:
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    if config_dir.strip():
        env["CLAUDE_CONFIG_DIR"] = config_dir.strip()
    return env


def check_claude_code(custom_path: str, config_dir: str) -> dict:
    """Comando, profilo e account collegato."""
    binary = resolve_claude(custom_path)
    home = Path.home()
    profiles = sorted(str(p) for p in home.iterdir() if p.is_dir() and (p.name == ".claude" or p.name.startswith(".claude-")))
    effective = config_dir.strip() or os.environ.get("CLAUDE_CONFIG_DIR") or str(home / ".claude")
    st = {"binary": binary, "configDir": effective, "loggedIn": False, "profiles": profiles}
    if not binary:
        st["error"] = "Comando claude non trovato."
        return st
    try:
        out = subprocess.run([binary, "auth", "status"], env=claude_env(config_dir), capture_output=True, text=True, timeout=15)
        d = json.loads(out.stdout)
        st.update(loggedIn=bool(d.get("loggedIn")), email=d.get("email"), authMethod=d.get("authMethod"),
                  configDir=d.get("configDirectory") or effective)
    except Exception as err:
        st["error"] = str(err)[:200]
    return st


class ClaudeCodeEngine:
    name = "claudecode"

    def __init__(self, model: str, access: str, claude_path: str, config_dir: str,
                 assistant_name: str, user_name: str, effort: str) -> None:
        self.model, self.access, self.claude_path, self.config_dir = model, access, claude_path, config_dir
        self.assistant_name, self.user_name, self.effort = assistant_name, user_name, effort
        self.history = History("claudecode")
        self._proc: subprocess.Popen | None = None

    def reset(self) -> None:
        self.history.clear()

    def _system_prompt(self) -> str:
        access = {
            "chat": "Non hai accesso ai file o ai comandi del Mac: se ti chiedono di farlo, spiega che serve alzare il livello di accesso nelle impostazioni dell'app.",
            "read": "Puoi leggere file e cercare nelle cartelle dell'utente, ma non modificare nulla né eseguire comandi.",
            "full": "Puoi leggere e modificare file ed eseguire comandi sul Mac dell'utente. Fallo con prudenza e spiega brevemente cosa hai fatto.",
        }[self.access]
        return "\n\n".join([
            persona_text(self.assistant_name),
            user_block(self.user_name, memory_prompt()),
            f"Adesso è {today_label()}.",
            "Strumenti di memoria (obbligatori): per ricordare un fatto sull'utente chiama `mcp__avatar__salva_memoria`; per cancellarne uno `mcp__avatar__dimentica_memoria`; per cercare `mcp__avatar__cerca_memoria`. Non dire mai di aver salvato qualcosa senza averlo chiamato davvero. Gli altri strumenti `mcp__avatar__*` (per esempio il calendario) agiscono sul Mac dell'utente: usali quando servono, e per le azioni irreversibili chiedi conferma a voce prima di richiamarli con confermato=true.",
            access,
            "Stai parlando dentro un'app vocale: rispondi in modo conversazionale, senza intestazioni Markdown né tabelle, e non citare nomi di file o strumenti interni a meno che non serva.",
        ])

    def _args(self, session_id: str, resume: bool, attach_path: str = "") -> list[str]:
        flags = dict(ACCESS[self.access])
        if attach_path:
            # Permesso di lettura limitato al file allegato (immagini incluse: Claude Code le vede).
            if "Read" not in flags["tools"] and flags["tools"] != "default":
                flags["tools"] = flags["tools"] + ",Read"
            flags["allowed"] = [*flags["allowed"], f"Read(//{attach_path.lstrip('/')})"]
        mcp = {"mcpServers": {"avatar": {"command": sys.executable, "args": [str(MCP_SCRIPT)]}}}
        args = ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--model", self.model, "--effort", self.effort,
                "--tools", flags["tools"], "--allowedTools", ",".join(flags["allowed"] + MEMORY_TOOLS),
                "--mcp-config", json.dumps(mcp), "--strict-mcp-config",
                "--system-prompt-snapshot", "off",
                "--resume" if resume else "--session-id", session_id]
        if flags["mode"]:
            args += ["--permission-mode", flags["mode"]]
        args += ["--system-prompt" if self.access == "chat" else "--append-system-prompt", self._system_prompt()]
        return args

    def _run_once(self, text: str, session_id: str, resume: bool, emit: Emit, abort: threading.Event, attach_path: str = "") -> dict:
        binary = resolve_claude(self.claude_path)
        if not binary:
            raise RuntimeError("Non trovo il comando `claude`. Installa Claude Code o indica il percorso nelle impostazioni.")
        proc = subprocess.Popen([binary, *self._args(session_id, resume, attach_path)], cwd=str(Path.home()), env=claude_env(self.config_dir),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self._proc = proc
        out = {"text": "", "initialized": False, "error": None, "result": ""}
        announced = False

        def watch_abort():
            abort.wait()
            if proc.poll() is None:
                proc.terminate()

        threading.Thread(target=watch_abort, daemon=True).start()
        try:
            proc.stdin.write(text)
            proc.stdin.close()
        except Exception:
            pass
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            t = msg.get("type")
            if t == "system" and msg.get("subtype") == "init":
                out["initialized"] = True
            elif t == "stream_event":
                ev = msg.get("event") or {}
                if ev.get("type") == "content_block_start" and (ev.get("content_block") or {}).get("type") == "tool_use":
                    name = ev["content_block"].get("name", "")
                    if name.startswith("mcp__memoria__"):
                        emit({"type": "status", "status": "memory"})
                    elif name in ("WebSearch", "WebFetch"):
                        emit({"type": "status", "status": "searching"})
                    else:
                        emit({"type": "status", "status": "working", "detail": name})
                elif ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                    delta = ev["delta"].get("text", "")
                    if delta:
                        if not announced:
                            announced = True
                            emit({"type": "status", "status": "responding"})
                        out["text"] += delta
                        emit({"type": "text", "delta": delta})
                elif ev.get("type") == "message_start" and announced:
                    emit({"type": "status", "status": "thinking"})
            elif t == "result":
                out["result"] = msg.get("result") or ""
                if msg.get("is_error") or (msg.get("subtype") and msg["subtype"] != "success"):
                    errs = msg.get("errors") or []
                    out["error"] = "; ".join(map(str, errs)) or out["result"] or msg.get("subtype")
        stderr = proc.stderr.read() if proc.stderr else ""
        out["code"] = proc.wait()
        out["stderr"] = stderr
        self._proc = None
        if not out["text"] and out["result"] and not out["error"]:
            out["text"] = out["result"]
            emit({"type": "text", "delta": out["result"]})
        return out

    def send(self, user_text: str, emit: Emit, abort: threading.Event, attach_path: str = "", **_) -> None:
        if attach_path:
            user_text += f"\n\n(Il file allegato è in {attach_path}: se ti serve vederlo, leggilo con lo strumento Read.)"
        before = {(r["category"], r["key"]) for r in mm.all_entries_for_ui()}
        session_id = str(self.history.meta.get("sessionId") or "")
        resume = bool(session_id)
        session_id = session_id or str(uuid.uuid4())
        try:
            emit({"type": "status", "status": "thinking"})
            out = self._run_once(user_text, session_id, resume, emit, abort, attach_path)
            if not abort.is_set() and resume and not out["initialized"] and out["code"] != 0:
                print("[Claude Code] sessione non ripresa, ne creo una nuova:", out["stderr"][:200])
                session_id, resume = str(uuid.uuid4()), False
                out = self._run_once(user_text, session_id, resume, emit, abort, attach_path)
            if out["initialized"]:
                self.history.meta["sessionId"] = session_id
            for r in mm.all_entries_for_ui():
                if (r["category"], r["key"]) not in before:
                    emit({"type": "memory_saved", "text": r["value"]})
            if abort.is_set():
                self.history.save()
                emit({"type": "error", "message": "Risposta interrotta.", "aborted": True})
                return
            if out["error"] or (out["code"] != 0 and not out["text"]):
                detail = out["error"] or " ".join(out["stderr"].strip().splitlines()[-3:])
                raise RuntimeError(detail or f"Claude Code è terminato con codice {out['code']}")
            text = out["text"].strip() or "(nessuna risposta)"
            self.history.messages.append({"role": "user", "text": user_text})
            self.history.messages.append({"role": "assistant", "text": text})
            self.history.save()
            emit({"type": "done", "text": text, "sources": []})
        except Exception as err:
            self.history.save()
            emit({"type": "error", "message": str(err)})
