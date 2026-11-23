"""Audit trail for the model-driven agents (OpenAI-served; Portkey gateway).

Three append-only tables in db/audit.db (triggers block UPDATE and DELETE):
  agent_turn   one row per model call: system-prompt hash, the full input messages, the full text output,
               tool calls requested, and the parsed structured output if any.
  tool_result  one row per tool call the orchestrator makes: the gateway's answer (truncated to 8 KB).
  query_log / proposal_log (already written by the gateway): permissions, rows returned, published payloads.

What is captured: everything the model received and everything it wrote back in plain text, including
its rationale fields. Hidden reasoning tokens are NOT exported by the API, so they cannot be logged and this
trail does not claim to contain them. Anything the agent is meant to think through must appear in its
output, and that is what the audit records.
"""
import hashlib, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS query_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, agent TEXT, tool TEXT, args_json TEXT, db TEXT, rows_returned INTEGER, status TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS proposal_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, agent TEXT, payload_json TEXT, payload_sha256 TEXT);
CREATE TRIGGER IF NOT EXISTS no_update_p BEFORE UPDATE ON proposal_log BEGIN SELECT RAISE(ABORT,'proposal log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_p BEFORE DELETE ON proposal_log BEGIN SELECT RAISE(ABORT,'proposal log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON query_log BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON query_log BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;
CREATE TABLE IF NOT EXISTS agent_turn(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, agent TEXT, turn INTEGER,
  model TEXT, system_sha256 TEXT, input_json TEXT, output_text TEXT, tool_calls_json TEXT, structured_json TEXT, output_sha256 TEXT);
CREATE TABLE IF NOT EXISTS tool_result(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, agent TEXT, turn INTEGER,
  tool TEXT, args_json TEXT, result_json TEXT, result_sha256 TEXT);
CREATE TRIGGER IF NOT EXISTS no_upd_turn BEFORE UPDATE ON agent_turn BEGIN SELECT RAISE(ABORT,'agent trail is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_del_turn BEFORE DELETE ON agent_turn BEGIN SELECT RAISE(ABORT,'agent trail is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_upd_tool BEFORE UPDATE ON tool_result BEGIN SELECT RAISE(ABORT,'agent trail is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_del_tool BEFORE DELETE ON tool_result BEGIN SELECT RAISE(ABORT,'agent trail is append-only'); END;
"""

def ensure_schema(audit_db: str | Path) -> None:
    """Create db/audit.db (all four append-only tables) if it is missing. The audit database is not in git: every clone
    starts with an empty trail and each machine keeps its own."""
    Path(audit_db).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(audit_db)); c.executescript(SCHEMA); c.commit(); c.close()

class TraceLog:
    def __init__(self, root: str | Path, run_id: str):
        self.db = Path(root) / "db" / "audit.db"; self.run_id = run_id
        ensure_schema(self.db)

    def _ins(self, sql, vals):
        c = sqlite3.connect(str(self.db), timeout=30); c.execute(sql, vals); c.commit(); c.close()

    def turn(self, agent: str, turn: int, model: str, system_prompt: str, messages: list, output_text: str,
             tool_calls: list | None = None, structured: dict | None = None):
        ts = datetime.now(timezone.utc).isoformat()
        self._ins("INSERT INTO agent_turn(ts,run_id,agent,turn,model,system_sha256,input_json,output_text,tool_calls_json,structured_json,output_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (ts, self.run_id, agent, turn, model, hashlib.sha256(system_prompt.encode()).hexdigest(),
                   json.dumps(messages, default=str), output_text or "", json.dumps(tool_calls or [], default=str),
                   json.dumps(structured, default=str) if structured is not None else None,
                   hashlib.sha256((output_text or "").encode()).hexdigest()))

    def validated(self, agent: str, turn: int, label: str, obj: dict):
        """Records an output that passed its contract (used for the Red Team, which may not publish to the proposal bus)."""
        self._ins("INSERT INTO agent_turn(ts,run_id,agent,turn,model,system_sha256,input_json,output_text,tool_calls_json,structured_json,output_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), self.run_id, agent, turn, "validated", "", "[]",
                   f"[VALIDATED OUTPUT: {label}]", "[]", json.dumps(obj, default=str),
                   hashlib.sha256(json.dumps(obj, default=str).encode()).hexdigest()))

    def tool(self, agent: str, turn: int, tool: str, args: dict, result: dict):
        s = json.dumps(result, default=str)
        self._ins("INSERT INTO tool_result(ts,run_id,agent,turn,tool,args_json,result_json,result_sha256) VALUES(?,?,?,?,?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), self.run_id, agent, turn, tool,
                   json.dumps(args, default=str)[:4000], s[:8000], hashlib.sha256(s.encode()).hexdigest()))
