"""Server MCP (stdio) che espone la memoria dell'assistente a Claude Code."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from avatar.memory_tools import mcp_tools, memory_prompt, run_tool  # noqa: E402
from avatar.plugins import registry  # noqa: E402

TOOLS = mcp_tools() + [{"name": "elenca_memorie", "description": "Elenca le memorie salvate sull'utente.",
                        "inputSchema": {"type": "object", "properties": {}}}] + registry.mcp_tools()


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        reply = (lambda result: send({"jsonrpc": "2.0", "id": rid, "result": result})) if rid is not None else (lambda r: None)
        if method == "initialize":
            reply({"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "avatar", "version": "1.1.0"}})
        elif method in ("notifications/initialized", "notifications/cancelled"):
            pass
        elif method == "ping":
            reply({})
        elif method == "tools/list":
            reply({"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            try:
                if name == "elenca_memorie":
                    reply(text_result(memory_prompt()))
                elif registry.has(name):
                    res = registry.run(name, args)
                    reply(text_result(res, res.startswith("Errore")))
                else:
                    res, _ = run_tool(name, args)
                    reply(text_result(res, res.startswith("Errore")))
            except Exception as err:
                reply(text_result(f"Errore: {err}", True))
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Metodo non supportato: {method}"}})


if __name__ == "__main__":
    main()
