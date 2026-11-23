"""Offline checks for the Responses API path (no key, no network): message conversion, explicit cache breakpoint,
tool-call parsing, usage parsing and cache-write pricing. The live check is `python -m app.ping`."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.llm import LLM, to_responses_input, to_responses_tools, usage_of_responses  # noqa: E402
from app.pricing import cost_usd  # noqa: E402

msgs = [{"role": "system", "content": "SYSTEM PROMPT"},
        {"role": "user", "content": "rank managers"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                              "function": {"name": "list_managers", "arguments": "{\"cls\": \"US_EQ\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{\"rows\": 10}"}]
items = to_responses_input(msgs)
assert items[0]["role"] == "developer", items[0]
part = items[0]["content"][0]
assert part["text"] == "SYSTEM PROMPT" and part["prompt_cache_breakpoint"] == {"mode": "explicit"}, part
assert items[1] == {"role": "user", "content": "rank managers"}
assert items[2] == {"type": "function_call", "call_id": "c1", "name": "list_managers", "arguments": "{\"cls\": \"US_EQ\"}"}
assert items[3] == {"type": "function_call_output", "call_id": "c1", "output": "{\"rows\": 10}"}
assert len(items) == 4, "empty assistant text must not add a message"

tools = to_responses_tools([{"type": "function", "function": {"name": "f", "description": "d",
                                                               "parameters": {"type": "object", "properties": {}}}}])
assert tools == [{"type": "function", "name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}]
assert to_responses_tools(None) is None


class FakeResponses:
    def __init__(self):
        self.kw = None

    def create(self, **kw):
        self.kw = kw
        return NS(output=[NS(type="reasoning"),
                          NS(type="function_call", call_id="call_9", name="get_time", arguments="{}")],
                  output_text="",
                  usage=NS(input_tokens=5000, output_tokens=40,
                           input_tokens_details=NS(cached_tokens=4000, cache_write_tokens=0),
                           output_tokens_details=NS(reasoning_tokens=0)))


os.environ.setdefault("PORTKEY_API_KEY", "test-key")
os.environ["AII_CACHE_MODE"] = "explicit"
llm = LLM(api="responses")
fake = FakeResponses()
llm.client = NS(responses=fake)
m = llm.chat("ping", msgs[:2], tools=[{"type": "function", "function": {"name": "get_time", "description": "t",
                                                                          "parameters": {"type": "object", "properties": {}}}}])
assert m.tool_calls and m.tool_calls[0].id == "call_9" and m.tool_calls[0].function.name == "get_time"
assert fake.kw["store"] is False and fake.kw["tool_choice"] == "auto"
assert fake.kw["extra_body"]["prompt_cache_options"]["mode"] == "explicit"
assert fake.kw["input"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
assert m.usage["prompt"] == 5000 and m.usage["cached"] == 4000 and m.usage["cache_write"] == 0
assert llm.usage[-1]["agent"] == "ping"

u = usage_of_responses(NS(usage={"input_tokens": 100, "output_tokens": 5,
                                 "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 80}}))
assert u == {"prompt": 100, "cached": 0, "cache_write": 80, "completion": 5, "reasoning": 0}, u

# 1M input = 600k cached + 300k written + 100k plain; 100k output, at luna prices
c = cost_usd(1_000_000, 600_000, 100_000, "gpt-6-luna", cache_write=300_000)
exp = (100_000 * 0.10 + 600_000 * 0.01 + 300_000 * 0.125 + 100_000 * 0.50) / 1e6
assert abs(c - exp) < 1e-12, (c, exp)
assert abs(cost_usd(1000, 0, 0) - 0.0001) < 1e-12  # old call signature unchanged

os.environ["AII_CACHE_MODE"] = "auto"
auto = LLM(api="responses"); fa = FakeResponses(); auto.client = NS(responses=fa)
auto.chat("ping", msgs[:2])
assert "extra_body" not in fa.kw and "prompt_cache_breakpoint" not in fa.kw["input"][0]["content"][0], fa.kw
assert "auto caching" in auto.route
del os.environ["AII_CACHE_MODE"]
assert "auto caching" in LLM(api="responses").route  # default since the 2026-11-23 live comparison

chat = LLM(api="chat")
assert "chat API" in chat.route
print("ALL PASS")

# Live failure 2026-11-23 (US IG Head): a valid proposal followed by stray tokens and a second partial copy, and a
# repair reply with one extra closing brace. The first complete object must be used and the tail reported.
from app.orchestrator import _json_obj  # noqa: E402
seen = []
good = '{"summary": "hire IG07", "selected": [{"manager_id": "IG07", "weight_pct": 100}]}'
assert _json_obj(good + '\n 日博{"summary": "hire IG0', seen.append)["selected"][0]["manager_id"] == "IG07"
assert _json_obj(good + "}\n", seen.append)["summary"] == "hire IG07"
assert _json_obj("```json\n" + good + "\n```", seen.append)["summary"] == "hire IG07"
assert len(seen) == 2, seen  # the two replies with a tail were reported; the clean fenced one was not
for bad in ("no json here", '{"summary": "unterminated', "[1, 2]"):
    try:
        _json_obj(bad)
        raise AssertionError(f"should have failed: {bad}")
    except ValueError:
        pass
print("JSON tail handling: PASS")
