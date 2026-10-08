"""Consumi: token e costi per giorno e per motore, dal registro data/consumi.jsonl scritto a ogni risposta."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.settings import DATA_DIR  # noqa: E402

LOG = DATA_DIR / "consumi.jsonl"


def _n(x: int) -> str:
    return f"{x:,}".replace(",", ".")


def riepilogo(giorni: int = 7) -> str:
    since = (datetime.now() - timedelta(days=giorni - 1)).strftime("%Y-%m-%d")
    try:
        rows = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    except FileNotFoundError:
        rows = []
    rows = [r for r in rows if r["ts"][:10] >= since]
    if not rows:
        return f"Nessun consumo registrato negli ultimi {giorni} giorni (il conteggio parte da oggi)."
    per_m = defaultdict(lambda: [0, 0, 0, 0.0, False]); per_g = defaultdict(lambda: [0, 0.0])
    for r in rows:
        m = per_m[r["motore"]]; m[0] += 1; m[1] += r["input"]; m[2] += r["output"]; m[3] += r["costo"]; m[4] = m[4] or r.get("abbonamento", False)
        g = per_g[r["ts"][:10]]; g[0] += r["input"] + r["output"]; g[1] += 0 if r.get("abbonamento") else r["costo"]
    out = [f"Consumi ultimi {giorni} giorni:"]
    for mot, (n, i, o, c, abb) in sorted(per_m.items(), key=lambda kv: -(kv[1][1] + kv[1][2])):
        costo = "gratis (locale)" if mot.startswith("locale") else (f"≈ ${c:.2f} equivalente API, incluso nell'abbonamento" if abb else f"≈ ${c:.2f}")
        out.append(f"- {mot}: {n} risposte, {_n(i)} token in ingresso, {_n(o)} in uscita, {costo}")
    out.append("Per giorno: " + "; ".join(f"{g[8:10]}/{g[5:7]} {_n(t)} token" + (f" (${c:.2f})" if c else "") for g, (t, c) in sorted(per_g.items())))
    return "\n".join(out)


def t_riepilogo(params: dict, ctx: dict) -> str:
    return riepilogo(max(1, min(int(params.get("giorni") or 7), 90)))


TOOLS = [
    {"name": "consumi_riepilogo", "description": "Token e costi delle risposte di LuZa per motore (locale, Claude Code, Claude API) e per giorno, negli ultimi N giorni (default 7). Per 'quanto ho speso?', 'quanti token ho usato?'.",
     "parameters": {"type": "object", "properties": {"giorni": {"type": "integer"}}}, "run": t_riepilogo},
]


if __name__ == "__main__":
    import tempfile
    LOG = Path(tempfile.mkdtemp()) / "c.jsonl"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    LOG.write_text("\n".join(json.dumps(r) for r in [
        {"ts": now, "motore": "locale (gemma)", "input": 100, "output": 50, "costo": 0, "abbonamento": False},
        {"ts": now, "motore": "claude api (x)", "input": 1000, "output": 200, "costo": 0.01, "abbonamento": False}]) + "\n")
    out = riepilogo(7); assert "gratis (locale)" in out and "$0.01" in out, out; print(out)
