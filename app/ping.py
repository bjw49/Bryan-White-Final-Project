"""Two live calls to check the key, the gateway, tool calling and the prompt cache (costs a fraction of a cent).
  python -m app.ping"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from app.run import _load_env
    found = _load_env()
    print(".env loaded from:", found["loaded"] or f"none found (searched {len(found['searched'])} folders up to the drive root)")
    for k in ("PORTKEY_API_KEY", "OPENAI_API_KEY"):
        print(f"{k} taken from:", found["key_source"][k])
    from app.llm import LLM
    try:
        llm = LLM()
    except RuntimeError as e:
        print("FAIL:", e); return 1
    tool = [{"type": "function", "function": {"name": "get_time", "description": "Returns the time.",
                                              "parameters": {"type": "object", "properties": {}, "required": []}}}]
    # A fixed system prompt long enough to be cacheable (well over 1,024 tokens), sent twice: call 1 should write the
    # cache, call 2 should read it. This is the same pattern every agent uses (fixed prompt first, breakpoint after it).
    system = "You are a test agent. Follow the user's instruction exactly. " + " ".join(
        f"Rule {i}: answer briefly and use the tool when asked." for i in range(1, 220))
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": "Call the get_time tool once."}]
    try:
        m1 = llm.chat("ping", msgs, tools=tool)
        m2 = llm.chat("ping", msgs, tools=tool)
    except Exception as e:
        print("route:", llm.route)
        print(f"FAIL: {type(e).__name__}: {str(e)[:600]}")
        if getattr(llm, "api", "") == "responses":
            print("The gateway rejected the Responses API call. Fallback: set AII_API=chat (the v2.19 path) and rerun.")
        return 1
    ok = bool(m1.tool_calls) and bool(m2.tool_calls)
    print("route:", llm.route)
    print(("OK" if ok else "WARN") + f": model={llm.model_name} tool_call={'yes' if ok else 'no'}")
    print("call 1 usage:", json.dumps(m1.usage))
    print("call 2 usage:", json.dumps(m2.usage))
    cache_ok = (m2.usage or {}).get("cached", 0) > 0
    print("prompt cache:", "WORKING (call 2 read cached tokens)" if cache_ok else
          "NOT SEEN (call 2 read no cached tokens; the run still works, but the caching claim cannot be made)")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
