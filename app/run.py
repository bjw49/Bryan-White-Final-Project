"""Command line.
  python3 -m app.run --mock --owner "A university endowment of $2bn ..."     # scripted stand-in, no key needed
  python3 -m app.run --owner "..."                                          # live: needs PORTKEY_API_KEY
Writes output/<run_id>/ (memo.md, decision.json or escalation.json, proposals.json, flags.json) and audit/<run_id>_*.md."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> dict:
    """Finds .env by walking up from the app's folder: this folder first, then the folder above (the course rule),
    then each folder above that until one is found or the drive root is reached. Only the first .env found is read.
    Keys already set in the environment win. Returns where it looked and which file it loaded (never the values)."""
    searched, loaded, from_file = [], None, []
    pre = {k for k in ("PORTKEY_API_KEY", "OPENAI_API_KEY") if os.environ.get(k)}
    d = ROOT
    while True:
        p = d / ".env"
        searched.append(p)
        if p.is_file():
            for line in p.read_text(encoding="utf-8-sig").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    k = k.strip().removeprefix("export ").strip()
                    if k not in os.environ:
                        os.environ[k] = v.strip().strip('"').strip("'")
                        from_file.append(k)
            loaded = str(p)
            break
        if d.parent == d:
            break
        d = d.parent
    src = {k: ("Windows environment (set before the app started; overrides any .env)" if k in pre
               else f".env file {loaded}" if k in from_file else "not set")
           for k in ("PORTKEY_API_KEY", "OPENAI_API_KEY")}
    return {"searched": [str(x) for x in searched], "loaded": loaded, "key_source": src}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the investment decision pipeline.")
    ap.add_argument("--owner", help="Owner description in plain English (or --owner-file)")
    ap.add_argument("--owner-file", help="Path to a text file with the owner description")
    ap.add_argument("--mock", action="store_true", help="Use the scripted stand-in model (tests the wiring, not the judgment)")
    ap.add_argument("--run-id")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    text = a.owner or (Path(a.owner_file).read_text(encoding="utf-8") if a.owner_file else None)
    if not text:
        ap.error("give --owner or --owner-file")
    _load_env()
    sys.path.insert(0, str(Path(a.root).resolve()))
    if a.mock:
        from app.mock_llm import ScriptedLLM
        llm = ScriptedLLM()
    else:
        from app.llm import LLM
        llm = LLM()
    from app.orchestrator import run_pipeline
    res = run_pipeline(text, llm, a.root, a.run_id)
    print(json.dumps({k: res[k] for k in ("run_id", "status", "stop", "output_dir")}, indent=2))
    if res["flags"]:
        print(f"{len(res['flags'])} flag(s): see flags.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
