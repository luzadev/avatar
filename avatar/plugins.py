"""Plugin: file Python in plugins/ che dichiarano strumenti chiamabili dai motori.

Formato AvatarPy (più strumenti per file):
    TOOLS = [{"name": ..., "description": ..., "parameters": {json schema}, "run": fn}]
    fn(parameters: dict, ctx: dict) -> str

Formato Mark-LIV (uno strumento per file): PLUGIN = {...} + run(parameters, player=None, ...).
"""
from __future__ import annotations

import importlib.util
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


def _normalize_schema(schema: dict) -> dict:
    """Converte gli schemi in stile Gemini (tipi maiuscoli) in JSON Schema valido."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out: dict = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            out[k] = v.lower()
        elif k == "properties" and isinstance(v, dict):
            out[k] = {pk: _normalize_schema(pv) for pk, pv in v.items()}
        elif k == "items" and isinstance(v, dict):
            out[k] = _normalize_schema(v)
        else:
            out[k] = v
    if "type" not in out:
        out["type"] = "object" if "properties" in out else "string"
    if out["type"] == "object":
        out.setdefault("properties", {})
    return out


class Tool:
    def __init__(self, module: str, name: str, description: str, parameters: dict, run: Callable) -> None:
        self.module, self.name, self.description, self.parameters, self.run = module, name, description, parameters, run


class PluginRegistry:
    def __init__(self, plugins_dir: Path = PLUGINS_DIR) -> None:
        self.dir = plugins_dir
        self.tools: dict[str, Tool] = {}
        self.modules: dict[str, dict[str, Any]] = {}
        self.ctx: dict[str, Any] = {}
        self._loaded = False

    def load(self) -> "PluginRegistry":
        if self._loaded:
            return self
        self._loaded = True
        if not self.dir.exists():
            return self
        for path in sorted(self.dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            mod_name = path.stem
            info = {"name": mod_name, "description": "", "valid": True, "error": "", "tools": []}
            try:
                spec = importlib.util.spec_from_file_location(f"avatar_plugins.{mod_name}", path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                tools = getattr(module, "TOOLS", None)
                if tools is None and hasattr(module, "PLUGIN"):
                    p = module.PLUGIN
                    tools = [{"name": p["name"], "description": p.get("description", ""), "parameters": p.get("parameters", {}),
                              "run": (lambda params, ctx, _m=module: _m.run(params, ctx.get("player")))}]
                if not tools:
                    raise ValueError("nessun TOOLS o PLUGIN definito")
                for t in tools:
                    name = str(t["name"])
                    if not _NAME_RE.match(name):
                        raise ValueError(f"nome strumento non valido: {name}")
                    if name in self.tools:
                        raise ValueError(f"strumento duplicato: {name}")
                    self.tools[name] = Tool(mod_name, name, str(t.get("description", "")), _normalize_schema(t.get("parameters", {})), t["run"])
                    info["tools"].append(name)
                info["description"] = (getattr(module, "__doc__", "") or "").strip().splitlines()[0] if getattr(module, "__doc__", None) else ", ".join(info["tools"])
            except Exception as err:
                info.update(valid=False, error=f"{err}")
                traceback.print_exc()
            self.modules[mod_name] = info
        return self

    def add_external(self, module: str, description: str, tools: list[dict]) -> None:
        """Registra strumenti forniti a runtime (server MCP), sostituendo quelli dello stesso modulo."""
        self.load()
        self.remove_module(module)
        info = {"name": module, "description": description, "valid": True, "error": "", "tools": []}
        for t in tools:
            name = str(t["name"])
            if name in self.tools:   # nome già usato da un plugin: prefissa
                name = f"x_{name}"[:64]
            self.tools[name] = Tool(module, name, str(t.get("description", "")), _normalize_schema(t.get("parameters", {})), t["run"])
            info["tools"].append(name)
        self.modules[module] = info

    def remove_module(self, module: str) -> None:
        for name in [n for n, t in self.tools.items() if t.module == module]:
            self.tools.pop(name, None)
        self.modules.pop(module, None)

    def _enabled(self, module: str) -> bool:
        try:
            from memory.config_manager import get_plugin_enabled
            return bool(get_plugin_enabled(module))
        except Exception:
            return True

    def active(self) -> list[Tool]:
        self.load()
        return [t for t in self.tools.values() if self._enabled(t.module)]

    def has(self, name: str) -> bool:
        self.load()
        return name in self.tools

    def anthropic_tools(self) -> list[dict]:
        return [{"name": t.name, "description": t.description, "eager_input_streaming": True, "input_schema": t.parameters} for t in self.active()]

    def openai_tools(self, local: bool = False) -> list[dict]:
        """Per i modelli locali (local=True) esclude i server MCP esterni: le loro descrizioni (Cua Driver ~14k token)
        rallentano troppo il primo turno. Si riattivano con l'impostazione mcp_locali."""
        tools = self.active()
        if local:
            try:
                from avatar.settings import Settings
                if not Settings().get("mcp_locali"):
                    tools = [t for t in tools if not t.module.startswith("mcp:")]
            except Exception:
                pass
        return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]

    def mcp_tools(self) -> list[dict]:
        return [{"name": t.name, "description": t.description, "inputSchema": t.parameters} for t in self.active()]

    def run(self, name: str, args: dict) -> str:
        self.load()
        tool = self.tools.get(name)
        if tool is None:
            return f"Strumento sconosciuto: {name}"
        try:
            out = str(tool.run(dict(args or {}), self.ctx) or "Fatto.")
            on_image, on_file = self.ctx.get("on_image"), self.ctx.get("on_file")
            for line in out.splitlines():
                try:
                    if on_image and line.startswith("IMMAGINE: "):
                        on_image(line[10:].strip())
                    elif on_file and line.startswith("FILE: "):
                        on_file(line[6:].strip())
                except Exception as e:
                    print(f"[plugin] invio al telefono: {e}")
            return out
        except Exception as err:
            if not isinstance(err, (RuntimeError, ValueError)):
                traceback.print_exc()
            return f"Errore in {name}: {err}"

    def list_for_ui(self) -> list[dict]:
        self.load()
        return [{"name": m["name"], "description": m["description"] or ", ".join(m["tools"]), "enabled": self._enabled(m["name"]),
                 "valid": m["valid"], "error": m["error"]} for m in self.modules.values()]


registry = PluginRegistry()
