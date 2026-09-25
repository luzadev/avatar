"""Motore interno: carica un modello MLX (mlx-lm) dentro l'app, senza server esterni.

Modelli da mlx-community (Hugging Face), scaricati nella cache locale al primo uso. Gli strumenti (memoria,
plugin, ricerca web) passano dal template di chat del modello nel formato Hermes/Qwen:
<tool_call>{"name": "...", "arguments": {...}}</tool_call>
"""
from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path

from avatar.memory_tools import memory_prompt, openai_tools, parse_args, run_tool
from avatar.plugins import registry
from avatar.websearch import brave_search
from .base import Emit, History, persona_text, today_label, user_block
from .openai_compat import SEARCH_TOOL, Aborted

MAX_ROUNDS = 8
MAX_HISTORY = 30
MAX_TOKENS = 3000
DEFAULT_MODEL = "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"
HUB = Path.home() / ".cache" / "huggingface" / "hub"
_EXCLUDE = ("whisper", "tts", "embed", "rerank", "clip", "vl-", "-vl", "vision", "diffusion", "parakeet")

_lock = threading.Lock()
_loaded: dict = {"name": None, "model": None, "tokenizer": None, "cache": None, "tokens": [], "snap": None}


def cached_models() -> list[str]:
    """Modelli MLX già scaricati nella cache di Hugging Face (esclusi voce/visione)."""
    out = []
    if HUB.exists():
        for d in sorted(HUB.glob("models--*")):
            name = d.name[len("models--"):].replace("--", "/")
            low = name.lower()
            if "mlx" in low and not any(x in low for x in _EXCLUDE) and (d / "snapshots").exists():
                out.append(name)
    return out


def load_model(name: str):
    """Carica (o riusa) il modello. Un solo modello in memoria per volta."""
    with _lock:
        if _loaded["name"] == name and _loaded["model"] is not None:
            return _loaded["model"], _loaded["tokenizer"]
        unload()
        from mlx_lm import load
        model, tokenizer = load(name)
        _loaded.update(name=name, model=model, tokenizer=tokenizer, cache=None, tokens=[], snap=None)
        return model, tokenizer


def unload() -> None:
    if _loaded["model"] is not None:
        _loaded.update(name=None, model=None, tokenizer=None, cache=None, tokens=[], snap=None)
        try:
            import gc
            import mlx.core as mx
            gc.collect()
            mx.clear_cache()
        except Exception:
            pass


class TagFilter:
    """Separa il testo visibile dai blocchi racchiusi tra `start` e `end` (pensieri o chiamate di strumenti)."""

    def __init__(self, start: str, end: str, keep: bool) -> None:
        self.start, self.end, self.keep = start, end, keep
        self.inside = False
        self.strip_next = False   # dopo un blocco chiuso, via gli spazi iniziali del testo che segue
        self.carry = ""
        self.buf = ""
        self.blocks: list[str] = []

    def _emit(self, piece: str) -> str:
        if self.strip_next:
            piece = piece.lstrip()
            if piece:
                self.strip_next = False
        return piece

    def _partial(self, text: str, tag: str) -> int:
        """Lunghezza del pezzo finale di `text` che potrebbe essere l'inizio di `tag`."""
        for n in range(min(len(tag) - 1, len(text)), 0, -1):
            if text.endswith(tag[:n]):
                return n
        return 0

    def push(self, delta: str) -> str:
        text, self.carry, out = self.carry + delta, "", ""
        while text:
            if self.inside:
                e = text.find(self.end)
                if e < 0:
                    n = self._partial(text, self.end)
                    self.buf += text[: len(text) - n] if n else text
                    self.carry = text[len(text) - n:] if n else ""
                    return out
                self.buf += text[:e]
                if self.keep:
                    self.blocks.append(self.buf.strip())
                self.buf, self.inside, self.strip_next = "", False, True
                text = text[e + len(self.end):]
            else:
                st = text.find(self.start)
                if st < 0:
                    n = self._partial(text, self.start)
                    out += self._emit(text[: len(text) - n] if n else text)
                    self.carry = text[len(text) - n:] if n else ""
                    return out
                out += self._emit(text[:st])
                text = text[st + len(self.start):]
                self.inside = True
        return out

    def flush(self) -> str:
        if self.inside:
            if self.keep and (self.buf + self.carry).strip():
                self.blocks.append((self.buf + self.carry).strip())
            self.buf = self.carry = ""
            self.inside = False
            return ""
        c, self.carry = self.carry, ""
        return self._emit(c)


# Formati di chiamata: Hermes/Qwen (JSON in <tool_call>) e Gemma 4 (call:nome{chiave:<|"|>testo<|"|>,...}).
FORMATS = {
    "hermes": {"think": ("<think>", "</think>"), "call": ("<tool_call>", "</tool_call>")},
    "gemma4": {"think": ("<|channel>", "<channel|>"), "call": ("<|tool_call>", "<tool_call|>")},
}
GEMMA_ESC = '<|"|>'


def detect_format(tokenizer) -> str:
    try:
        vocab = tokenizer.get_vocab()
    except Exception:
        vocab = {}
    return "gemma4" if "<|tool_call>" in vocab else "hermes"


def _gemma_to_json(body: str) -> str:
    """Converte gli argomenti nel formato Gemma in JSON: stringhe tra <|"|>, chiavi nude."""
    parts = body.split(GEMMA_ESC)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(json.dumps(part, ensure_ascii=False))
        else:
            out.append(re.sub(r'([{,\[]\s*)([A-Za-z_][\w.-]*)\s*:', r'\1"\2":', part))
    return "".join(out)


def _parse_call(raw: str, fmt: str) -> tuple[str, dict, str] | None:
    """(nome, argomenti, argomenti grezzi come li ha scritti il modello)."""
    raw = raw.strip()
    if fmt == "gemma4" or raw.startswith("call:"):
        m = re.match(r"call:\s*([\w.-]+)\s*(\{.*\})?\s*$", raw, re.S)
        if not m:
            return None
        body = m.group(2) or "{}"
        try:
            args = json.loads(_gemma_to_json(body))
        except Exception:
            return None
        return m.group(1), dict(args or {}), body[1:-1]
    try:
        d = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(d, dict) or not d.get("name"):
        return None
    args = d.get("arguments", d.get("parameters", {}))
    if isinstance(args, str):
        args = parse_args(args)
    return str(d["name"]), dict(args or {}), json.dumps(args or {}, ensure_ascii=False)


class MLXEngine:
    name = "mlx"

    def __init__(self, model_name: str, search_api_key: str, assistant_name: str, user_name: str) -> None:
        self.model_name = model_name or DEFAULT_MODEL
        self.search_api_key = search_api_key
        self.assistant_name, self.user_name = assistant_name, user_name
        self.history = History("mlx")
        self.model = self.tokenizer = None
        self.fmt = "hermes"
        self.load_error: str | None = None
        threading.Thread(target=self._preload, daemon=True).start()

    def _preload(self) -> None:
        try:
            self.model, self.tokenizer = load_model(self.model_name)
            self.fmt = detect_format(self.tokenizer)
            self._warm()
        except Exception as err:
            self.load_error = str(err)
            print(f"[mlx] caricamento fallito: {err}")

    def _warm(self) -> None:
        """Pre-calcola la parte stabile del prompt (persona, memoria, strumenti, cronologia) e la fotografa."""
        if self.model is None:
            return
        with _lock:
            if _loaded["snap"] is not None:
                return
            saved = list(self.history.messages)
            try:
                renders = []
                for dummy in ("a", "b"):
                    self.history.messages = saved + [{"role": "user", "content": dummy}]
                    renders.append(list(self.tokenizer.encode(self._prompt())))
            finally:
                self.history.messages = saved
            n = 0
            while n < min(len(renders[0]), len(renders[1])) and renders[0][n] == renders[1][n]:
                n += 1
            self._prefill(renders[0][:n], n)

    def _prefill(self, tokens: list[int], cut: int):
        """Cache che contiene esattamente `tokens`, riusando la fotografia precedente se ne è un'estensione.
        La nuova fotografia viene scattata a `cut` (fine dell'ultimo messaggio): il turno successivo riparte da lì."""
        from mlx_lm import stream_generate
        from mlx_lm.models.cache import make_prompt_cache, trim_prompt_cache, can_trim_prompt_cache
        snap, cache, fed = _loaded["snap"], None, 0
        if snap and len(tokens) >= len(snap["tokens"]) and tokens[: len(snap["tokens"])] == snap["tokens"]:
            cache, fed = copy.deepcopy(snap["cache"]), len(snap["tokens"])
        elif _loaded["cache"] is not None and _loaded["tokens"]:
            # cache viva del turno precedente: ritaglia al prefisso comune (solo se il modello lo consente)
            old = _loaded["tokens"]
            n = 0
            while n < min(len(old), len(tokens)) and old[n] == tokens[n]:
                n += 1
            if n > 0 and can_trim_prompt_cache(_loaded["cache"]):
                trim_prompt_cache(_loaded["cache"], len(old) - n)
                cache, fed = _loaded["cache"], n
        _loaded.update(cache=None, tokens=[])
        if cache is None:
            cache = make_prompt_cache(self.model)
        # max_tokens=1: il token campionato non entra nella cache, che contiene quindi esattamente i token passati
        if cut > fed:
            for _ in stream_generate(self.model, self.tokenizer, tokens[fed:cut], max_tokens=1, prompt_cache=cache):
                pass
            _loaded["snap"] = {"tokens": list(tokens[:cut]), "cache": copy.deepcopy(cache)}
            fed = cut
        if len(tokens) > fed:
            for _ in stream_generate(self.model, self.tokenizer, tokens[fed:], max_tokens=1, prompt_cache=cache):
                pass
        return cache

    def close(self) -> None:
        self.model = self.tokenizer = None
        unload()

    def reset(self) -> None:
        self.history.clear()

    def _ensure_loaded(self, emit: Emit) -> None:
        if self.model is not None:
            return
        emit({"type": "status", "status": "working", "detail": "carico il modello"})
        self.load_error = None
        self.model, self.tokenizer = load_model(self.model_name)
        self.fmt = detect_format(self.tokenizer)

    def _system(self) -> str:
        note = ("\n\nRegole sugli strumenti (obbligatorie):\n"
                "- Quando l'utente ti chiede di ricordare qualcosa, o ti dice un fatto importante su di sé, DEVI chiamare salva_memoria prima di rispondere. Non dire mai di aver salvato senza averlo chiamato davvero.\n"
                "- Per cancellare una memoria chiama dimentica_memoria; per cercarne una non presente nel prompt chiama cerca_memoria.\n"
                "- Per azioni sul Mac (per esempio il calendario) usa gli strumenti dedicati. Se uno risponde con [CONFIRMATION_PENDING], chiedi all'utente di confermare sul pannello e non dire che è fatto.\n"
                "- Non inventare mai dati reali (meteo, ora, calendario, mail, messaggi, file, stato dei server): se esiste uno strumento che li fornisce, chiamalo prima di rispondere.\n"
                "- Chiama gli strumenti solo nel formato previsto dal tuo template e mai descrivendoli a parole.")
        note += ("\n- Per informazioni aggiornate chiama cerca_web e rispondi in base ai risultati." if self.search_api_key
                 else "\n- Non hai accesso al web: se ti chiedono informazioni aggiornate, dillo chiaramente.")
        return f"{persona_text(self.assistant_name)}\n\n{user_block(self.user_name, memory_prompt())}{note}"

    def _recent(self) -> list:
        msgs = self.history.messages
        if len(msgs) <= MAX_HISTORY:
            return msgs
        start = len(msgs) - MAX_HISTORY
        while start < len(msgs) and msgs[start].get("role") != "user":
            start += 1
        return msgs[start:]

    def _tools(self) -> list:
        return openai_tools() + registry.openai_tools() + ([SEARCH_TOOL] if self.search_api_key else [])

    def _prompt(self, generation: bool = True) -> str:
        """Prompt completo; con generation=False si ferma alla fine dell'ultimo messaggio (parte stabile)."""
        messages = [{"role": "system", "content": self._system()}, *self._recent()]
        try:
            return self.tokenizer.apply_chat_template(messages, tools=self._tools(), add_generation_prompt=generation, tokenize=False, enable_thinking=False)
        except TypeError:
            # Template senza supporto strumenti: li descrive nel sistema.
            messages[0]["content"] += "\n\nStrumenti disponibili (JSON):\n" + json.dumps(self._tools(), ensure_ascii=False) + \
                "\nPer usarne uno scrivi solo: <tool_call>{\"name\": \"…\", \"arguments\": {…}}</tool_call>"
            return self.tokenizer.apply_chat_template(messages, add_generation_prompt=generation, tokenize=False, enable_thinking=False)

    def _prompt_tokens(self) -> tuple[list[int], int]:
        """Token del prompt e lunghezza della parte stabile (comune al turno successivo)."""
        tokens = list(self.tokenizer.encode(self._prompt()))
        stable = list(self.tokenizer.encode(self._prompt(generation=False)))
        cut = 0
        while cut < min(len(tokens), len(stable)) and tokens[cut] == stable[cut]:
            cut += 1
        return tokens, cut

    def _run_tool(self, name: str, args: dict, emit: Emit, sources: list) -> str:
        if name == "cerca_web":
            emit({"type": "status", "status": "searching"})
            try:
                hits = brave_search(str(args.get("query", "")), self.search_api_key)
            except Exception as err:
                return f"Errore nella ricerca: {err}"
            for h in hits[:5]:
                if all(s["url"] != h["url"] for s in sources):
                    sources.append({"title": h["title"], "url": h["url"]})
            return "\n".join(f"{i+1}. {h['title']}\n   {h['url']}\n   {h['description']}" for i, h in enumerate(hits)) or "Nessun risultato."
        if registry.has(name):
            emit({"type": "status", "status": "working", "detail": name})
            return registry.run(name, args)
        emit({"type": "status", "status": "memory"})
        res, ev = run_tool(name, args)
        if ev:
            emit(ev)
        return res

    def _generate(self, emit: Emit, abort: threading.Event, announced: list) -> tuple[str, list[str], bool]:
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        f = FORMATS[self.fmt]
        think, tools, text, truncated = TagFilter(*f["think"], keep=False), TagFilter(*f["call"], keep=True), "", False
        sampler = make_sampler(temp=0.7, top_p=0.8, top_k=20)
        # Cache del prompt: la parte già vista (fotografia del turno precedente) non viene ricalcolata.
        tokens, cut = self._prompt_tokens()
        cache = self._prefill(tokens[:-1], min(cut, len(tokens) - 1))
        generated: list[int] = []
        try:
            for r in stream_generate(self.model, self.tokenizer, tokens[-1:], max_tokens=MAX_TOKENS, sampler=sampler, prompt_cache=cache):
                if abort.is_set():
                    raise Aborted()
                generated.append(r.token)
                visible = tools.push(think.push(r.text))
                if visible:
                    if not announced:
                        announced.append(True)
                        emit({"type": "status", "status": "responding"})
                    text += visible
                    emit({"type": "text", "delta": visible})
                if r.finish_reason == "length":
                    truncated = True
        except Aborted:
            raise
        else:
            # generate_step inserisce nella cache ogni token emesso, tranne l'ultimo quando si ferma per limite.
            _loaded.update(cache=cache, tokens=tokens + (generated[:-1] if truncated else generated))
        tail = tools.flush()
        if tail:
            text += tail
            emit({"type": "text", "delta": tail})
        return text, tools.blocks, truncated

    def send(self, user_text: str, emit: Emit, abort: threading.Event, **_) -> None:
        self.history.messages.append({"role": "user", "content": f"{user_text}\n\n(Adesso è {today_label()}.)"})
        full, sources, announced = "", [], []
        try:
            self._ensure_loaded(emit)
            emit({"type": "status", "status": "thinking"})
            for rnd in range(MAX_ROUNDS):
                with _lock:
                    round_text, raw_calls, truncated = self._generate(emit, abort, announced)
                full += round_text
                calls = [c for c in (_parse_call(r, self.fmt) for r in raw_calls) if c]
                if not calls:
                    self.history.messages.append({"role": "assistant", "content": round_text.strip()})
                    break
                self.history.messages.append({
                    "role": "assistant", "content": round_text.strip(),
                    "tool_calls": [{"id": f"call_{rnd}_{i}", "type": "function", "function": {"name": n, "arguments": raw}} for i, (n, a, raw) in enumerate(calls)],
                })
                for i, (n, a, raw) in enumerate(calls):
                    result = self._run_tool(n, a, emit, sources)
                    self.history.messages.append({"role": "tool", "tool_call_id": f"call_{rnd}_{i}", "name": n, "content": str(result)})
                emit({"type": "status", "status": "thinking"})
                if truncated:
                    break
            full = full.strip() or "(nessuna risposta)"
            self.history.save()
            emit({"type": "done", "text": full, "sources": sources})
        except Aborted:
            self._rollback()
            emit({"type": "error", "message": "Risposta interrotta.", "aborted": True})
        except Exception as err:
            self._rollback()
            emit({"type": "error", "message": f"Modello interno: {err}"})

    def _rollback(self) -> None:
        msgs = self.history.messages
        if msgs and msgs[-1].get("role") == "user":
            msgs.pop()
        self.history.save()
