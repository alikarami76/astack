"""Turn `claude -p --output-format stream-json` lines into flynn panel events, and parse flynn's
teammate blocks (NEEDS_ALIGNMENT, RESULT, BLOCKED) into cards.

Panel events (dicts with key "t"):
  session {id}            the Claude session id (used to resume the next turn)
  delta {text}            assistant text, streamed
  note {text}             a progress note (thinking display "updates")
  assistant_end           an assistant message finished
  tool {name, summary}    flynn used a tool
  denied {tools}          tool calls that were refused; each has name, input and a suggested allow rule
  activity {agent, text}  a specialist (subagent) used a tool: shown in the status line only, so the user can see it is
                          working without its whole trace
  turn_end {ok, cost, text}
Only the main agent's text and steps are shown; a subagent's output (parent_tool_use_id set) becomes activity."""
from __future__ import annotations

import json
import re
from pathlib import PurePath

BLOCK_KINDS = {"NEEDS_ALIGNMENT": "needs_alignment", "RESULT": "result", "BLOCKED": "blocked"}
LIST_KEYS = {"questions", "show_user", "assumptions_if_unanswered", "deliverables", "design_docs", "stored", "caveats"}


def _tool_summary(name: str, inp: dict) -> str:
    if name == "Agent" or name == "Task":
        who = inp.get("subagent_type", "agent")
        return f"{who}: {inp.get('description', '')}".strip()
    if inp.get("description"):
        return str(inp["description"])
    for key in ("file_path", "path", "notebook_path"):
        if inp.get(key):
            return PurePath(str(inp[key])).name
    for key in ("command", "pattern", "query", "skill", "url", "prompt"):
        if inp.get(key):
            s = str(inp[key])
            return s if len(s) <= 80 else s[:77] + "..."
    return ""


def _allow_rule(name: str, inp: dict) -> str:
    if name == "Bash" and inp.get("command"):
        return f"Bash({inp['command']})"
    for key in ("file_path", "path"):
        if inp.get(key):
            return f"{name}({inp[key]})"
    return name


# Agent tool calls by id -> which specialist it is, so its own tool calls can be named (ids are unique per session).
AGENTS: dict[str, str] = {}


def _subagent(d: dict) -> list[dict]:
    if d.get("type") != "assistant":
        return []
    who = AGENTS.get(d.get("parent_tool_use_id") or "", "specialist")
    out = []
    for block in (d.get("message") or {}).get("content") or []:
        if block.get("type") == "tool_use":
            name = block.get("name", "")
            out.append({"t": "activity", "agent": who, "tool": name, "text": _tool_summary(name, block.get("input") or {})[:120]})
    return out


def events_from_line(line: str) -> list[dict]:
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return []
    t = d.get("type")
    if t == "system" and d.get("subtype") == "init":
        return [{"t": "session", "id": d.get("session_id")}]
    if d.get("parent_tool_use_id"):
        return _subagent(d)
    if t == "stream_event":
        e = d.get("event") or {}
        if e.get("type") == "content_block_delta":
            delta = e.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                return [{"t": "delta", "text": delta["text"]}]
            if delta.get("type") == "thinking_delta" and delta.get("thinking"):
                return [{"t": "note", "text": delta["thinking"]}]
        if e.get("type") == "message_stop":
            return [{"t": "assistant_end"}]
        return []
    if t == "assistant":
        out = []
        for block in (d.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                inp = block.get("input") or {}
                name = block.get("name", "")
                if name.startswith("mcp__desk__"):          # flynn desk tools read as "desk · ask user"
                    out.append({"t": "tool", "name": "desk", "summary": name[11:].replace("_", " ")})
                    continue
                if name in ("Agent", "Task") and block.get("id"):
                    AGENTS[block["id"]] = str(inp.get("subagent_type") or "specialist")
                out.append({"t": "tool", "name": name, "summary": _tool_summary(name, inp)})
        return out
    if t == "result":
        out = []
        if d.get("session_id"):
            out.append({"t": "session", "id": d["session_id"]})
        denials = d.get("permission_denials") or []
        if denials:
            out.append({"t": "denied", "tools": [
                {"name": x.get("tool_name", ""), "input": x.get("tool_input") or {},
                 "rule": _allow_rule(x.get("tool_name", ""), x.get("tool_input") or {})} for x in denials]})
        out.append({"t": "turn_end", "ok": not d.get("is_error", False) and d.get("subtype") == "success",
                    "cost": d.get("total_cost_usd"), "text": d.get("result", "")})
        return out
    return []


_Q = re.compile(r"^\s*(\d+)\.\s*(.*?)\s*(?:—|--)\s*recommended:\s*(.*?)(?:\s*(?:—|--)\s*because\s*(.*))?$")


def _parse_block(kind: str, lines: list[str]) -> dict:
    card: dict = {"kind": BLOCK_KINDS[kind]}
    key = None
    for raw in lines:
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("```"):
            continue
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if m and not raw.startswith(" "):
            key, val = m.group(1), m.group(2)
            if key in LIST_KEYS:
                card[key] = []
                if val:
                    card[key].append(val)
            else:
                card[key] = val
            continue
        if key in LIST_KEYS:
            if key == "questions":
                # Optional lines under a question: "options: a | b | c" and "multi: yes".
                opt = re.match(r"^\s+options:\s*(.*)$", raw)
                if opt and card[key]:
                    card[key][-1]["options"] = [o.strip() for o in opt.group(1).split("|") if o.strip()]
                    continue
                mul = re.match(r"^\s+multi:\s*(\S+)", raw)
                if mul and card[key]:
                    card[key][-1]["multi"] = mul.group(1).lower() in ("yes", "true", "y")
                    continue
                q = _Q.match(line)
                if q:
                    card[key].append({"n": int(q.group(1)), "q": q.group(2), "recommended": q.group(3),
                                      "because": q.group(4) or "", "options": [], "multi": False})
                    continue
                n = re.match(r"^\s*(\d+)\.\s*(.*)$", line)
                if n:
                    card[key].append({"n": int(n.group(1)), "q": n.group(2), "recommended": "", "because": "", "options": [], "multi": False})
                    continue
            item = re.sub(r"^\s*-\s*", "", line)
            card[key].append(item)
        elif key:
            card[key] = (card[key] + "\n" + line.strip()).strip()
    return card


def parse_blocks(text: str) -> list[dict]:
    """Find NEEDS_ALIGNMENT, RESULT and BLOCKED blocks in flynn's text."""
    lines = (text or "").splitlines()
    cards, i = [], 0
    while i < len(lines):
        head = lines[i].strip().strip("`").strip()
        if head not in BLOCK_KINDS:
            # A header can also end a line of prose ("Here it is: RESULT") when the next line starts the block.
            m = re.search(r"(?:^|\s)(NEEDS_ALIGNMENT|RESULT|BLOCKED)$", head)
            if m and i + 1 < len(lines) and lines[i + 1].strip().startswith("run:"):
                head = m.group(1)
        if head in BLOCK_KINDS:
            j = i + 1
            while j < len(lines) and lines[j].strip().strip("`").strip() not in BLOCK_KINDS and not lines[j].strip().startswith("```"):
                j += 1
            cards.append(_parse_block(head, lines[i + 1:j]))
            i = j
        else:
            i += 1
    return cards


def aligned_message(answers: list[dict], note: str = "") -> str:
    """The reply flynn expects after NEEDS_ALIGNMENT: `ALIGNED` plus numbered answers, in the user's words."""
    body = "\n".join(f"{a['n']}. {a['answer']}" for a in sorted(answers, key=lambda a: a["n"]))
    msg = "ALIGNED\n" + body
    if note.strip():
        msg += "\n\nAli also said: " + note.strip()
    return msg
