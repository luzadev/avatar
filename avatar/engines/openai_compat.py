"""Motore per server compatibili OpenAI (vLLM, Ollama, LM Studio…)."""
from __future__ import annotations

import re
import threading

import openai

from avatar.memory_tools import memory_prompt, openai_tools, parse_args, run_tool
from avatar.plugins import registry
from avatar.websearch import brave_search
from .base import Emit, History, persona_text, today_label, user_block

MAX_ROUNDS = 8
MAX_HISTORY = 30
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "cerca_web",
        "description": "Cerca sul web informazioni aggiornate (notizie, prezzi, orari, meteo, eventi). Restituisce titoli, link e descrizioni.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
}


class Aborted(Exception):
    pass


def friendly_error(err: Exception, base_url: str) -> str:
    if isinstance(err, openai.APIConnectionError):
        return f"Non riesco a raggiungere il server locale su {base_url}. È avviato?"
    if isinstance(err, openai.AuthenticationError):
        return "Il server locale ha rifiutato la chiave API."
    if isinstance(err, openai.NotFoundError):
        return "Modello non trovato sul server locale. Controlla il nome nelle impostazioni."
    if isinstance(err, openai.BadRequestError):
        return f"Il server locale ha rifiutato la richiesta: {err.message}"
    if isinstance(err, openai.APIStatusError):
        return f"Errore del server locale ({err.status_code}): {err.message}"
    return str(err)


class ThinkFilter:
    """Nasconde i blocchi <think>…</think> dei modelli ragionanti."""

    def __init__(self) -> None:
        self.inside = False
        self.carry = ""

    def push(self, delta: str) -> str:
        text, self.carry, out = self.carry + delta, "", ""
        while text:
            if self.inside:
                end = text.find("</think>")
                if end < 0:
                    self.carry = text[-8:]
                    return out
                text = text[end + 8:].lstrip()
                self.inside = False
            else:
                start = text.find("<think>")
                if start < 0:
                    m = re.search(r"<(t(h(i(n(k)?)?)?)?)?$", text)
                    if m:
                        self.carry = m.group(0)
                        out += text[: m.start()]
                    else:
                        out += text
                    return out
                out += text[:start]
                text = text[start + 7:]
                self.inside = True
        return out


class OpenAICompatEngine:
    name = "local"

    def __init__(self, base_url: str, model: str, api_key: str, search_api_key: str,
                 assistant_name: str, user_name: str) -> None:
        self.base_url, self.model, self.search_api_key = base_url, model, search_api_key
        self.assistant_name, self.user_name = assistant_name, user_name
        self.client = openai.OpenAI(base_url=base_url, api_key=api_key or "non-necessaria")
        self.history = History("local")
        self._tools_supported = True

    def reset(self) -> None:
        self.history.clear()

    def _system(self) -> dict:
        if self._tools_supported:
            note = ("\n\nRegole sugli strumenti (obbligatorie):\n"
                    "- Quando l'utente ti chiede di ricordare qualcosa, o ti dice un fatto importante su di sé, DEVI chiamare salva_memoria prima di rispondere. Non dire mai di aver salvato senza averlo chiamato davvero.\n"
                    "- Per cancellare una memoria chiama dimentica_memoria; per cercarne una non presente nel prompt chiama cerca_memoria.\n"
                    "- Per azioni sul Mac (per esempio il calendario) usa gli strumenti dedicati. Se uno risponde con [CONFIRMATION_PENDING], chiedi all'utente di confermare sul pannello e non dire che è fatto.")
            note += ("\n- Per informazioni aggiornate chiama cerca_web e rispondi in base ai risultati." if self.search_api_key
                     else "\n- Non hai accesso al web: se ti chiedono informazioni aggiornate, dillo chiaramente.")
        else:
            note = "\n\nNota: in questa modalità non hai strumenti (niente memoria automatica né ricerca web)."
        return {"role": "system", "content": f"{persona_text(self.assistant_name)}\n\n{user_block(self.user_name, memory_prompt())}{note}"}

    def _recent(self) -> list:
        msgs = self.history.messages
        if len(msgs) <= MAX_HISTORY:
            return msgs
        start = len(msgs) - MAX_HISTORY
        while start < len(msgs) and msgs[start].get("role") != "user":
            start += 1
        return msgs[start:]

    def _messages(self) -> list:
        """Data e ora in coda all'ultimo messaggio utente: il prefisso resta uguale tra i turni (cache di vLLM)."""
        msgs = [dict(m) for m in self._recent()]
        for m in reversed(msgs):
            if m.get("role") == "user":
                m["content"] = f"{m['content']}\n\n(Adesso è {today_label()}.)"
                break
        return [self._system(), *msgs]

    def _tools(self):
        if not self._tools_supported:
            return None
        return openai_tools() + registry.openai_tools() + ([SEARCH_TOOL] if self.search_api_key else [])

    def _run_tool(self, name: str, raw_args: str, emit: Emit, sources: list) -> str:
        args = parse_args(raw_args)
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

    def send(self, user_text: str, emit: Emit, abort: threading.Event, **_) -> None:
        self.history.messages.append({"role": "user", "content": user_text})
        full = ""
        sources: list[dict] = []
        try:
            emit({"type": "status", "status": "thinking"})
            for rnd in range(MAX_ROUNDS):
                tools = self._tools()
                kwargs: dict = dict(model=self.model, messages=self._messages(), stream=True)
                if tools:
                    kwargs.update(tools=tools, tool_choice="auto")
                try:
                    stream = self.client.chat.completions.create(**kwargs)
                except openai.BadRequestError as err:
                    if tools and not full:
                        print(f"[locale] il server rifiuta gli strumenti, li disattivo: {err.message}")
                        self._tools_supported = False
                        continue
                    raise
                flt, calls, round_text, announced, finish = ThinkFilter(), {}, "", False, None
                for chunk in stream:
                    if abort.is_set():
                        stream.close()
                        raise Aborted()
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    if delta and delta.content:
                        visible = flt.push(delta.content)
                        if visible:
                            if not announced:
                                announced = True
                                emit({"type": "status", "status": "responding"})
                            round_text += visible
                            full += visible
                            emit({"type": "text", "delta": visible})
                    for tc in (delta.tool_calls if delta else None) or []:
                        p = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            p["id"] = tc.id
                        if tc.function and tc.function.name:
                            p["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            p["args"] += tc.function.arguments
                    if choice.finish_reason:
                        finish = choice.finish_reason
                tool_calls = [c for c in calls.values() if c["name"]]
                if not tool_calls:
                    self.history.messages.append({"role": "assistant", "content": round_text})
                    break
                for i, c in enumerate(tool_calls):
                    c["id"] = c["id"] or f"call_{rnd}_{i}"
                self.history.messages.append({
                    "role": "assistant", "content": round_text or None,
                    "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}} for c in tool_calls],
                })
                for c in tool_calls:
                    result = self._run_tool(c["name"], c["args"], emit, sources)
                    self.history.messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                emit({"type": "status", "status": "thinking"})
                if finish == "length":
                    break
            full = full.strip() or "(nessuna risposta)"
            self.history.save()
            emit({"type": "done", "text": full, "sources": sources})
        except Aborted:
            self._rollback()
            emit({"type": "error", "message": "Risposta interrotta.", "aborted": True})
        except Exception as err:
            self._rollback()
            emit({"type": "error", "message": friendly_error(err, self.base_url)})

    def _rollback(self) -> None:
        msgs = self.history.messages
        if msgs and msgs[-1].get("role") == "user":
            msgs.pop()
        self.history.save()


def list_models(base_url: str, api_key: str) -> list[str]:
    client = openai.OpenAI(base_url=base_url, api_key=api_key or "non-necessaria", timeout=8, max_retries=0)
    return [m.id for m in client.models.list()]
