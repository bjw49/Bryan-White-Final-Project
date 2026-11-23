"""Agent loop on PydanticAI (MGT 409 lecture 4: output format, dependencies, agent, tools, the agent loop).

PydanticAI drives the ReAct loop: it calls the model, dispatches tool calls to registered tools, feeds results back and
stops when the model answers in text. Everything that makes the loop safe is kept and enforced in code here, not left to
the framework or the model:

  * Transport: `GatewayModel` is a PydanticAI Model that sends each request through the app's own client (app/llm.py),
    so the explicit prompt-cache breakpoint, the gateway quirks, the per-call usage log and the scripted test model all
    stay exactly as before. The chat-format history the orchestrator keeps (`msgs`) is the source of truth for what the
    model sees, which is what the validate-and-repair step and the audit trail rely on.
  * Budgets: time, token and cost caps are checked before every model call (orchestrator.check_budgets); the step cap
    forces a final, tool-free answer on the last step, as in the original loop; PydanticAI's UsageLimits is a backstop.
  * Allowlist: a tool the agent is not allowed to use is never registered. If the model asks for one anyway, the call is
    routed to a denial tool that returns ACCESS_DENIED and raises a flag for the human CIO.
  * Evidence: every tool result gets an evidence id; identical repeat calls return the same id without re-running.
  * Injection: tool results flagged by the gateway's injection scan raise a run flag.
  * Order: tools run one at a time (sequential=True), in the order the model asked for them.

Select with AII_LOOP=pydantic_ai (default) or AII_LOOP=legacy (the original hand-written loop)."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # keep the console to the run log
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai import messages as M
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.usage import RequestUsage, UsageLimits

DENIED = "__tool_not_allowed__"
FORCE_FINAL = ("Your tool budget is used up. Reply now with ONLY the final JSON object, "
               "using evidence ids already returned. Do not call any more tools.")


def _assistant(msg) -> dict:
    d = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                           for c in msg.tool_calls]
    return d


class _Run:
    """State shared by the model and the tools during one agent run."""

    def __init__(self, ctx, agent: str, msgs: list, specs: list, extra: dict, steps: int):
        self.ctx, self.agent, self.msgs, self.specs, self.extra, self.steps = ctx, agent, msgs, specs, extra, steps
        self.allowed = {s["function"]["name"] for s in specs} | set(extra)
        self.seen: dict = {}
        self.requests = 0
        self.turn = 0
        self.retries_seen = 0


class GatewayModel(Model):
    """PydanticAI Model that delegates each request to the app's client (llm.chat) using the run's chat history."""

    def __init__(self, run: _Run):
        super().__init__()
        self.run = run

    @property
    def model_name(self) -> str:
        return getattr(self.run.ctx.llm, "model_name", "scripted")

    @property
    def system(self) -> str:
        return "openai"

    async def request(self, messages: list[M.ModelMessage], model_settings, model_request_parameters: ModelRequestParameters) -> M.ModelResponse:
        from app.orchestrator import check_budgets
        r, ctx = self.run, self.run.ctx
        # Mirror anything PydanticAI itself asked the model to fix (for example a retry prompt) into the history.
        retries = [p for m in messages if isinstance(m, M.ModelRequest) for p in m.parts if isinstance(p, M.RetryPromptPart)]
        for p in retries[r.retries_seen:]:
            r.msgs.append({"role": "user", "content": p.model_response()})
        r.retries_seen = len(retries)

        check_budgets(ctx, r.agent)  # time, token and cost caps, before every model call
        last = r.requests == r.steps - 1
        r.requests += 1
        if last and r.specs:
            r.msgs.append({"role": "user", "content": FORCE_FINAL})
        msg = ctx.llm.chat(r.agent, r.msgs, tools=r.specs if (r.specs and not last) else None)
        ctx.turn_no[r.agent] = ctx.turn_no.get(r.agent, 0) + 1
        r.turn = ctx.turn_no[r.agent]
        calls = None if last else msg.tool_calls
        ctx.trace.turn(r.agent, r.turn, getattr(ctx.llm, "model_name", "scripted"), ctx.system.get(r.agent, ""), list(r.msgs),
                       msg.content or "", [{"name": c.function.name, "arguments": c.function.arguments} for c in calls or []])
        u = msg.usage or {}
        usage = RequestUsage(input_tokens=u.get("prompt", 0) or 0, cache_read_tokens=u.get("cached", 0) or 0,
                             cache_write_tokens=u.get("cache_write", 0) or 0, output_tokens=u.get("completion", 0) or 0)
        if not calls:
            return M.ModelResponse(parts=[M.TextPart(content=msg.content or "")], usage=usage, model_name=self.model_name)
        r.msgs.append(_assistant(msg))
        parts: list = []
        if msg.content:
            parts.append(M.TextPart(content=msg.content))
        for c in calls:
            if c.function.name in r.allowed:
                parts.append(M.ToolCallPart(tool_name=c.function.name, args=c.function.arguments or "{}", tool_call_id=c.id))
            else:  # never registered for this agent: route to the denial tool, which logs and flags it
                parts.append(M.ToolCallPart(tool_name=DENIED, args={"requested": c.function.name}, tool_call_id=c.id))
        return M.ModelResponse(parts=parts, usage=usage, model_name=self.model_name)


def _tool_result(r: _Run, name: str, args: dict, out: Any, eid) -> str:
    """Record a tool result in the audit trace and the chat history; return what the model sees."""
    r.ctx.trace.tool(r.agent, r.turn, name, args, {"evidence_id": eid, "result": out})
    return json.dumps({"evidence_id": eid, "tool": name, "result": out}, default=str)[:14000]


def _make_tool(r: _Run, name: str) -> Any:
    def call(**args):
        ctx = r.ctx
        key = name + json.dumps(args, sort_keys=True, default=str)
        if key in r.seen:
            eid, out = r.seen[key]
        else:
            out = r.extra[name](args) if name in r.extra else ctx.gw(r.agent).call(name, **args)
            eid = ctx.add_evidence(r.agent, name, args, out)
            r.seen[key] = (eid, out)
            if isinstance(out, dict) and str(out.get("injection_scan", "")).startswith("FLAGGED"):
                ctx.flag(f"{r.agent}: instruction-like text found in {args.get('manager_id', name)} document; "
                         "delivered as untrusted data, not instructions")
        return _tool_result(r, name, args, out, eid)
    return call


def _denied_tool(r: _Run):
    def call(requested: str = "?"):
        r.ctx.flag(f"blocked: {r.agent} requested tool '{requested}' outside its allowlist")
        out = {"error": "ACCESS_DENIED", "detail": f"tool '{requested}' is not available to this agent"}
        return _tool_result(r, requested, {}, out, None)
    return call


def run_loop(ctx, agent: str, msgs: list, specs: list, extra: dict, steps: int) -> str:
    """Same contract as the original loop: runs the agent on `msgs` (chat format, mutated in place) and returns the
    final text. Raises orchestrator.Stop on budget breaches or when the step cap is exceeded."""
    from app.orchestrator import Stop
    r = _Run(ctx, agent, msgs, specs, extra, steps)
    tools = []
    for s in specs:
        f = s["function"]
        tools.append(Tool.from_schema(_wrap_history(r, _make_tool(r, f["name"])), name=f["name"],
                                      description=f.get("description"), json_schema=f.get("parameters") or {"type": "object", "properties": {}},
                                      sequential=True, takes_ctx=True))
    tools.append(Tool.from_schema(_wrap_history(r, _denied_tool(r)), name=DENIED, description="Internal: denied tool calls.",
                                  json_schema={"type": "object", "properties": {"requested": {"type": "string"}}}, sequential=True,
                                  takes_ctx=True))
    pa = Agent(GatewayModel(r), output_type=str, tools=tools, retries=2)

    def go():
        # The history lives in `msgs`; PydanticAI gets a placeholder prompt because GatewayModel sends `msgs` itself.
        return pa.run_sync("(conversation continues in the agent's history)",
                           usage_limits=UsageLimits(request_limit=steps + 3, tool_calls_limit=None))
    try:
        # Delegation (the ACIO asking a Head a follow-up) starts an agent run from inside another agent's tool.
        # Each run gets its own thread and event loop, and the caller waits for the result (lecture 10, async delegation).
        with ThreadPoolExecutor(max_workers=1) as ex:
            res = ex.submit(go).result()
    except UsageLimitExceeded:
        raise Stop(agent, f"exceeded {steps} steps without a final answer")
    return res.output or ""


def _wrap_history(r: _Run, fn):
    """Append each tool result to the chat history under the model's own tool_call_id, so the next request sees it."""
    def call(rc: RunContext, **kwargs):
        content = fn(**kwargs)
        r.msgs.append({"role": "tool", "tool_call_id": rc.tool_call_id, "content": content})
        return content
    return call
