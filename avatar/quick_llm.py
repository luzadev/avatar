"""Chiamata rapida a un modello per compiti di servizio (classificazioni), secondo il motore configurato."""
from __future__ import annotations

import os
import subprocess

from avatar.settings import Settings


def ask(prompt: str, system: str = "", max_tokens: int = 800) -> str:
    s = Settings()
    provider = s.get("provider")
    if provider == "local" and s.get("local_base_url") and s.get("local_model"):
        import openai
        client = openai.OpenAI(base_url=s.get("local_base_url"), api_key=s.get_secret("local_api_key") or "non-necessaria", timeout=60)
        r = client.chat.completions.create(model=s.get("local_model"), messages=[{"role": "system", "content": system or "Rispondi solo con quanto richiesto."}, {"role": "user", "content": prompt}], max_tokens=max_tokens)
        return (r.choices[0].message.content or "").strip()
    if provider == "anthropic" and s.get_secret("anthropic_api_key"):
        import anthropic
        client = anthropic.Anthropic(api_key=s.get_secret("anthropic_api_key"))
        r = client.messages.create(model="claude-haiku-4-5", max_tokens=max_tokens, system=system or "Rispondi solo con quanto richiesto.",
                                   messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in r.content if b.type == "text").strip()
    # Claude Code (haiku, nessuno strumento, nessuna sessione)
    from avatar.engines.claude_code import resolve_claude, claude_env
    binary = resolve_claude(s.get("claudecode_path") or "")
    if not binary:
        raise RuntimeError("Nessun motore disponibile per la classificazione.")
    args = [binary, "-p", "--output-format", "text", "--model", "haiku", "--effort", "low", "--tools", "", "--no-session-persistence",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    if system:
        args += ["--system-prompt", system]
    res = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=120, env=claude_env(s.get("claudecode_config_dir") or ""), cwd=os.path.expanduser("~"))
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip()[-200:])
    return res.stdout.strip()
