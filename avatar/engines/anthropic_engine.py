"""Motore Claude (API Anthropic) con ricerca web server-side e strumenti di memoria."""
from __future__ import annotations

import threading

import anthropic

from avatar.memory_tools import anthropic_tools, memory_prompt, parse_args, run_tool
from avatar.plugins import registry
from .base import Emit, History, persona_text, today_label, user_block

MODEL = "claude-opus-5"
MAX_ROUNDS = 12
WEB_SEARCH = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 5,
    "user_location": {"type": "approximate", "country": "IT", "timezone": "Europe/Rome"},
}


class Aborted(Exception):
    pass


def friendly_error(err: Exception) -> str:
    if isinstance(err, anthropic.AuthenticationError):
        return "La chiave API Anthropic non è valida. Controllala nelle impostazioni."
    if isinstance(err, anthropic.PermissionDeniedError):
        return "La chiave API non ha i permessi per questo modello."
    if isinstance(err, anthropic.RateLimitError):
        return "Troppe richieste in poco tempo. Riprova tra qualche secondo."
    if isinstance(err, anthropic.BadRequestError):
        return f"Richiesta non valida: {err.message}"
    if isinstance(err, anthropic.APIConnectionError):
        return "Non riesco a raggiungere il servizio Anthropic. Controlla la connessione."
    if isinstance(err, anthropic.APIStatusError):
        return f"Errore del servizio ({err.status_code}): {err.message}"
    return str(err)


class AnthropicEngine:
    name = "anthropic"

    def __init__(self, api_key: str, assistant_name: str, user_name: str, effort: str) -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self.assistant_name = assistant_name
        self.user_name = user_name
        self.effort = effort
        self.history = History("anthropic")
        self._compaction = True

    def reset(self) -> None:
        self.history.clear()

    def _system(self) -> list[dict]:
        return [
            {"type": "text", "text": persona_text(self.assistant_name), "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": user_block(self.user_name, memory_prompt())},
            {"type": "text", "text": f"Adesso è {today_label()}.\n\nSe uno strumento risponde con [CONFIRMATION_PENDING], chiedi all'utente di confermare sul pannello e non dire che è fatto."},
        ]

    @staticmethod
    def _sources(content: list[dict]) -> list[dict]:
        seen: dict[str, dict] = {}
        for block in content:
            for c in block.get("citations") or []:
                if c.get("type") == "web_search_result_location" and c.get("url") and c["url"] not in seen:
                    seen[c["url"]] = {"title": c.get("title") or c["url"], "url": c["url"]}
        return list(seen.values())

    def send(self, user_text: str, emit: Emit, abort: threading.Event, image: tuple[str, bytes] | None = None, **_) -> None:
        if image:
            import base64
            media, data = image
            self.history.messages.append({"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": base64.b64encode(data).decode()}},
                {"type": "text", "text": user_text},
            ]})
        else:
            self.history.messages.append({"role": "user", "content": user_text})
        full = ""
        sources: list[dict] = []
        try:
            emit({"type": "status", "status": "thinking"})
            for _ in range(MAX_ROUNDS):
                kwargs: dict = dict(
                    model=MODEL,
                    max_tokens=16000,
                    betas=["server-side-fallback-2026-07-01"] + (["compact-2026-01-12"] if self._compaction else []),
                    fallbacks="default",
                    output_config={"effort": self.effort},
                    system=self._system(),
                    tools=[WEB_SEARCH, *anthropic_tools(), *registry.anthropic_tools()],
                    messages=self.history.messages,
                )
                if self._compaction:
                    kwargs["context_management"] = {"edits": [{"type": "compact_20260112"}]}
                announced = False
                try:
                    with self.client.beta.messages.stream(**kwargs) as stream:
                        for event in stream:
                            if abort.is_set():
                                raise Aborted()
                            if event.type == "content_block_start":
                                t = event.content_block.type
                                if t == "server_tool_use":
                                    emit({"type": "status", "status": "searching"})
                                elif t == "tool_use":
                                    emit({"type": "status", "status": "memory"})
                                elif t == "thinking":
                                    emit({"type": "status", "status": "thinking"})
                            elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                                if not announced:
                                    announced = True
                                    emit({"type": "status", "status": "responding"})
                                full += event.delta.text
                                emit({"type": "text", "delta": event.delta.text})
                        message = stream.get_final_message()
                except anthropic.BadRequestError as err:
                    if self._compaction and not full:
                        print(f"[Claude] compattazione rifiutata, la disattivo: {err.message}")
                        self._compaction = False
                        continue
                    raise

                content = message.model_dump(mode="json")["content"]
                sources += self._sources(content)
                self.history.messages.append({"role": "assistant", "content": content})

                if message.stop_reason == "refusal":
                    if not full:
                        why = getattr(message.stop_details, "explanation", None) if message.stop_details else None
                        full = "Non posso aiutarti con questa richiesta." + (f" ({why})" if why else "")
                    break
                if message.stop_reason == "pause_turn":
                    continue
                tool_uses = [b for b in message.content if b.type == "tool_use"]
                if message.stop_reason != "tool_use" or not tool_uses:
                    break
                results = []
                for b in tool_uses:
                    if registry.has(b.name):
                        emit({"type": "status", "status": "working", "detail": b.name})
                        res, ev = registry.run(b.name, parse_args(b.input)), None
                    else:
                        res, ev = run_tool(b.name, parse_args(b.input))
                    if ev:
                        emit(ev)
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": res})
                self.history.messages.append({"role": "user", "content": results})
                emit({"type": "status", "status": "thinking"})

            full = full.strip() or "(nessuna risposta)"
            self.history.save()
            emit({"type": "done", "text": full, "sources": sources})
        except Aborted:
            self._rollback()
            emit({"type": "error", "message": "Risposta interrotta.", "aborted": True})
        except Exception as err:
            self._rollback()
            emit({"type": "error", "message": friendly_error(err)})

    def _rollback(self) -> None:
        msgs = self.history.messages
        if msgs and msgs[-1].get("role") == "user" and (isinstance(msgs[-1].get("content"), str) or
                                                        (isinstance(msgs[-1].get("content"), list) and any(b.get("type") == "image" for b in msgs[-1]["content"]))):
            msgs.pop()
        self.history.save()
