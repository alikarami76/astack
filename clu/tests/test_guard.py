import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import guard  # noqa: E402

RUN = "/home/user/agent-stack/clu/runs/2026-09-30-aov"
CLU = "/home/user/agent-stack/clu"


def ev(agent, tool, **tin):
    return {"agent_type": agent, "tool_name": tool, "tool_input": tin, "cwd": "/home/user/agent-stack/analysis"}


DENIED = [
    ev("clu-verifier", "Read", file_path=f"{RUN}/queries/aov.sql"),
    ev("clu-verifier", "Read", file_path=f"{RUN}/results/aov.json"),
    ev("clu-verifier", "Read", file_path=f"{RUN}/helpers/runner-aov.json"),
    ev("clu-verifier", "Bash", command=f'ls "{RUN}/queries/"'),
    ev("clu-verifier", "Bash", command=f'cat "{RUN}/results/aov.json" | head'),
    ev("clu-verifier", "Grep", pattern="SELECT", path=f"{RUN}/"),
    ev("clu-verifier", "Write", file_path=f"{RUN}/answer.md", content="x"),
    ev("clu-challenger", "Write", file_path=f"{RUN}/contract.md", content="x"),
    ev("clu-challenger", "Bash", command=f'python3 "{CLU}/kb/kb.py" add fact --json "{{}}"'),
    ev("clu-challenger", "Bash", command=f'echo hi > "{RUN}/contract.md"'),
    ev("clu-challenger", "Bash", command=f'rm -rf "{RUN}"'),
    ev("clu-editor", "Bash", command=f'sed -i "" s/a/b/ "{RUN}/answer.md"'),
    ev("clu-editor", "Bash", command=f'python3 "{CLU}/scripts/run_record.py" log x "{{}}"'),
    ev("clu-curator", "Edit", file_path=f"{CLU}/agents/clu.md", old_string="a", new_string="b"),
    ev("clu-curator", "Write", file_path=f"{CLU}/scripts/patterns.py", content="x"),
    ev("clu-curator", "Bash", command=f'python3 "{CLU}/scripts/proposals.py" apply 2026-x'),
    ev("clu-curator", "Bash", command=f'echo x >> "{CLU}/skills/clu-core/SKILL.md"'),
    ev("clu-data-runner", "Write", file_path="/home/user/Desktop/out.csv", content="x"),
    ev("clu", "Edit", file_path=f"{CLU}/skills/clu-core/SKILL.md", old_string="a", new_string="b"),
    ev("clu", "Write", file_path="/home/user/.claude/agents/clu-verifier.md", content="x"),
    ev("clu", "Write", file_path="/home/user/agent-stack/agents/clu-verifier.md", content="x"),
    ev("clu", "Edit", file_path="/home/user/agent-stack/skills/clu-core/SKILL.md", old_string="a", new_string="b"),
    ev("clu-verifier", "Bash", command=f'R="{RUN}"; cat "$R/queries/aov.sql"'),
    ev("clu-verifier", "Grep", pattern="SELECT", path=CLU),
    ev("clu-verifier", "Grep", pattern="SELECT", path=f"{CLU}/runs"),
]
ALLOWED = [
    ev("clu-verifier", "Read", file_path=f"{RUN}/contract.md"),
    ev("clu-verifier", "Read", file_path=f"{RUN}/verify/claims.json"),
    ev("clu-verifier", "Write", file_path=f"{RUN}/verify/result.json", content="{}"),
    ev("clu-verifier", "Bash", command=f'python3 tool.py --sql "SELECT 1" > "{RUN}/verify/aov.json"'),
    ev("clu-challenger", "Write", file_path=f"{RUN}/helpers/challenger-contract.json", content="{}"),
    ev("clu-challenger", "Bash", command=f'python3 "{CLU}/kb/kb.py" search "aov" --brief 2>/dev/null; echo done'),
    ev("clu-editor", "Bash", command="mkdir -p /tmp/clu-edit && pdftoppm -r 80 -png r.pdf /tmp/clu-edit/p"),
    ev("clu-editor", "Bash", command="python3 -c \"import pandas as pd; d=pd.read_excel('r.xlsx'); print(d[d.x > 0].head())\""),
    ev("clu-curator", "Write", file_path=f"{RUN}/kb_batch.json", content="[]"),
    ev("clu-curator", "Bash", command=f'python3 "{CLU}/kb/kb.py" add-many --file "{RUN}/kb_batch.json"'),
    ev("clu-curator", "Bash", command=f'python3 "{CLU}/scripts/proposals.py" new --slug s --diff-file /tmp/x.diff'),
    ev("clu-curator", "Write", file_path=f"{CLU}/kb/eval/recall_set.jsonl", content="{}"),
    ev("clu-data-runner", "Write", file_path=f"{RUN}/results/aov.json", content="{}"),
    ev("clu", "Write", file_path=f"{RUN}/contract.md", content="x"),
    ev("clu", "Bash", command=f'python3 "{CLU}/scripts/proposals.py" apply 2026-x'),
    ev("general-purpose", "Write", file_path=f"{CLU}/agents/clu.md", content="x"),
    ev(None, "Edit", file_path=f"{CLU}/hooks/guard.py", old_string="a", new_string="b"),
    ev("clu-challenger", "Bash", command=f'python3 "{CLU}/kb/kb.py" search "last touch attribution" --brief'),
    ev("clu-editor", "Bash", command='pdftoppm -png r.pdf p > "$TMPDIR/render.log"'),
    ev("clu-verifier", "Bash", command=f'python3 tool.py --output "{RUN}/verify/q.json"  # AS queries'),
]
ASTACK = "/opt/work/agent-stack/clu"


def _astack(payload):
    """The same payload with paths with the clu folder under a different parent."""
    return json.loads(json.dumps(payload).replace("/home/user/agent-stack/clu", ASTACK))


@pytest.mark.parametrize("payload", DENIED + [_astack(p) for p in DENIED])
def test_denied(payload):
    assert guard.decide(payload)


@pytest.mark.parametrize("payload", ALLOWED + [_astack(p) for p in ALLOWED])
def test_allowed(payload):
    assert guard.decide(payload) is None


def test_hook_output_denies_and_broken_input_allows():
    p = Path(guard.__file__)
    r = subprocess.run([sys.executable, str(p)], input=json.dumps(ev("clu-verifier", "Read", file_path=f"{RUN}/queries/a.sql")),
                       capture_output=True, text=True)
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert r.returncode == 0 and out["permissionDecision"] == "deny" and "blind" in out["permissionDecisionReason"]
    r2 = subprocess.run([sys.executable, str(p)], input="not json", capture_output=True, text=True)
    assert r2.returncode == 0 and r2.stdout == ""
