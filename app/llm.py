"""Model clients. LLM is the live OpenAI-compatible client (course Portkey gateway, model gpt-6-luna).

Two wire formats, chosen with AII_API:
  * "responses" (default): OpenAI Responses API with an explicit prompt-cache breakpoint after each agent's fixed system
    prompt (MGT 409 lecture 11, "Cached Token Cost"). The stable prefix is written to the cache once and then billed at
    the cached rate on every later call by the same agent.
  * "chat": the original /chat/completions path (v2.19). Kept as the fallback if the gateway rejects the Responses API.

The orchestrator always speaks chat-style messages; this module converts them at the edge, so nothing else changes.
Live use needs PORTKEY_API_KEY (or OPENAI_API_KEY). `python -m app.ping` checks the route in one call."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class Fn:
    name: str
    arguments: str


@dataclass
class ToolCall:
    id: str
    function: Fn


@dataclass
class Msg:
    content: str | None
    tool_calls: list[ToolCall] | None = None
    usage: dict | None = None


CACHE_TTL = os.environ.get("AII_CACHE_TTL", "30m")
# "explicit": one cache breakpoint after each agent's fixed system prompt (lecture 11). "auto": no breakpoint, the
# provider's automatic prefix caching (which also reuses earlier turns of the same conversation). Measured on
# 2026-11-23 (live endowment runs): explicit cached 23% of input ($0.0845 a run); automatic cached 58% ($0.0600).
# The breakpoint caches only the fixed system prompt; automatic prefix caching also reuses each agent's earlier turns.
CACHE_MODE = os.environ.get("AII_CACHE_MODE", "auto")


def to_responses_input(messages: list, explicit_cache: bool = True) -> list:
    """Chat-style messages -> Responses API input items. The system prompt becomes a developer message whose text part
    carries an explicit cache breakpoint: everything up to it is the cached prefix, everything after is normal input."""
    items = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            part = {"type": "input_text", "text": m.get("content") or ""}
            if explicit_cache:
                part["prompt_cache_breakpoint"] = {"mode": "explicit"}
            items.append({"role": "developer", "content": [part]})
        elif role == "user":
            items.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant":
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            for c in m.get("tool_calls") or []:
                f = c.get("function", {})
                items.append({"type": "function_call", "call_id": c["id"], "name": f.get("name", ""),
                              "arguments": f.get("arguments") or "{}"})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m.get("content") or ""})
    return items


def to_responses_tools(tools: list | None) -> list | None:
    """Chat-style {"type":"function","function":{...}} -> Responses-style flat {"type":"function","name":...}."""
    if not tools:
        return None
    out = []
    for t in tools:
        f = t.get("function", t)
        out.append({"type": "function", "name": f["name"], "description": f.get("description", ""),
                    "parameters": f.get("parameters", {"type": "object", "properties": {}})})
    return out


class LLM:
    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None,
                 extra_headers: dict | None = None, timeout: float = 60.0, api: str | None = None):
        pk, ok = os.environ.get("PORTKEY_API_KEY"), os.environ.get("OPENAI_API_KEY")
        key = api_key or pk or ok
        if not key:
            raise RuntimeError("No API key found (PORTKEY_API_KEY or OPENAI_API_KEY). Use --mock to run the scripted stand-in.")
        try:
            from openai import OpenAI  # imported after the key check; mock runs do not need the package
        except ImportError as e:
            raise RuntimeError("Live runs need the openai package: pip install openai") from e
        # Route: a Portkey key (PORTKEY_API_KEY, or an OPENAI_API_KEY that is not an OpenAI "sk-" key) goes through the
        # course Portkey gateway; a real OpenAI key ("sk-...") goes straight to OpenAI and is billed to that account.
        direct_openai = (not api_key and not pk and bool(ok) and ok.startswith("sk-")) or os.environ.get("AII_ROUTE") == "openai"
        if direct_openai:
            self.route = "OpenAI direct (api.openai.com, billed to your OpenAI account)"
            self.client = OpenAI(api_key=key, base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                                 timeout=timeout, max_retries=1)
        else:
            self.route = "course Portkey gateway"
            headers = {"x-portkey-api-key": key, **(extra_headers or {})}
            # timeout: one slow call cannot hold the run past its time budget (see orchestrator.TIME_BUDGET_S)
            self.client = OpenAI(api_key="unused", base_url=base_url or os.environ.get("PORTKEY_BASE_URL", "https://api.portkey.ai/v1"),
                                 default_headers=headers, timeout=timeout, max_retries=1)
        self.api = (api or os.environ.get("AII_API", "responses")).lower()
        if self.api not in ("responses", "chat"):
            raise RuntimeError(f"AII_API must be 'responses' or 'chat', got {self.api!r}")
        self.cache_mode = os.environ.get("AII_CACHE_MODE", CACHE_MODE)
        if self.cache_mode not in ("explicit", "auto"):
            raise RuntimeError(f"AII_CACHE_MODE must be 'explicit' or 'auto', got {self.cache_mode!r}")
        self.route += f", {self.api} API" + (f", {self.cache_mode} caching" if self.api == "responses" else "")
        self.model_name = model or os.environ.get("MODEL_NAME", "gpt-6-luna")
        self.usage: list[dict] = []

    # Transient gateway failures (5xx, dropped connection, timeout) get one more attempt after a short pause.
    # The SDK already retries once; this covers a second burst. Other errors (400 etc.) are not retried, except a
    # prompt-screen flag (invalid_prompt).
    _RETRY_ON = {"InternalServerError", "APIConnectionError", "APITimeoutError", "RateLimitError"}

    def _retry(self, fn, kw: dict):
        import time as _t
        try:
            return fn(**kw)
        except Exception as e:
            # the provider's prompt screen sometimes flags an ordinary request (code invalid_prompt, seen once in live
            # testing on a manager-research prompt); it is a 400 but not a fault in the request, so it gets one retry too
            flagged = type(e).__name__ == "BadRequestError" and "invalid_prompt" in str(e)
            if type(e).__name__ not in self._RETRY_ON and not flagged:
                raise
            _t.sleep(3)
            return fn(**kw)

    def chat(self, agent: str, messages: list, tools: list | None = None) -> Msg:
        import time as _t
        t0 = _t.monotonic()
        if self.api == "responses":
            msg, u = self._responses(messages, tools)
        else:
            msg, u = self._chat_completions(messages, tools)
        u["latency_s"] = round(_t.monotonic() - t0, 2)
        msg.usage = u
        self.usage.append({"agent": agent, **u})
        return msg

    def _chat_completions(self, messages: list, tools: list | None):
        kw = {"model": self.model_name, "messages": messages}
        if tools:
            kw["tools"] = tools
            kw["tool_choice"] = "auto"
            # The Azure gateway rejects function tools when reasoning_effort is set on /v1/chat/completions
            # (400 invalid_request_error). Default "none" sends the request; set AII_REASONING_EFFORT to change it.
            kw["reasoning_effort"] = os.environ.get("AII_REASONING_EFFORT", "none")
        r = self._retry(self.client.chat.completions.create, kw)
        m = r.choices[0].message
        calls = [ToolCall(c.id, Fn(c.function.name, c.function.arguments)) for c in (m.tool_calls or [])] or None
        return Msg(m.content, calls), usage_of(r)

    def _responses(self, messages: list, tools: list | None):
        explicit = self.cache_mode == "explicit"
        kw = {"model": self.model_name, "input": to_responses_input(messages, explicit), "store": False}
        if explicit:  # the breakpoint is set on the system prompt in to_responses_input
            kw["extra_body"] = {"prompt_cache_options": {"mode": "explicit", "ttl": CACHE_TTL}}
        rt = to_responses_tools(tools)
        if rt:
            kw["tools"] = rt
            kw["tool_choice"] = "auto"
        effort = os.environ.get("AII_REASONING_EFFORT", "none")
        if effort:
            kw["reasoning"] = {"effort": effort}
        r = self._retry(self.client.responses.create, kw)
        calls, texts = [], []
        for item in getattr(r, "output", None) or []:
            typ = getattr(item, "type", None)
            if typ == "function_call":
                calls.append(ToolCall(getattr(item, "call_id", None) or getattr(item, "id", ""),
                                      Fn(item.name, getattr(item, "arguments", None) or "{}")))
            elif typ == "message":
                for part in getattr(item, "content", None) or []:
                    if getattr(part, "type", None) == "output_text":
                        texts.append(part.text)
        text = getattr(r, "output_text", None) or "".join(texts) or None
        return Msg(text, calls or None), usage_of_responses(r)


def _get(obj, name, default=0):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default) or default
    return getattr(obj, name, default) or default


def usage_of(r) -> dict:
    """Token counts reported by the gateway for one /chat/completions call. cached = prompt tokens served from the
    provider's prompt cache (billed at the cached rate); reasoning = hidden reasoning tokens (billed as output)."""
    us = getattr(r, "usage", None)
    pd_ = _get(us, "prompt_tokens_details", None)
    cd_ = _get(us, "completion_tokens_details", None)
    return {"prompt": _get(us, "prompt_tokens"), "cached": _get(pd_, "cached_tokens"),
            "cache_write": _get(pd_, "cache_write_tokens"),
            "completion": _get(us, "completion_tokens"), "reasoning": _get(cd_, "reasoning_tokens")}


def usage_of_responses(r) -> dict:
    """Token counts for one Responses API call (lecture 11, "Measuring Token Usage"): input_tokens includes cached and
    newly cache-written tokens; output_tokens includes hidden reasoning tokens."""
    us = getattr(r, "usage", None)
    idt = _get(us, "input_tokens_details", None)
    odt = _get(us, "output_tokens_details", None)
    return {"prompt": _get(us, "input_tokens"), "cached": _get(idt, "cached_tokens"),
            "cache_write": _get(idt, "cache_write_tokens"),
            "completion": _get(us, "output_tokens"), "reasoning": _get(odt, "reasoning_tokens")}
