import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENTS = {"rinzler": "opus", "rinzler-planner": "sonnet", "rinzler-explorer": "sonnet", "rinzler-eval-author": "sonnet",
          "rinzler-judge": "sonnet", "rinzler-verifier": "sonnet", "rinzler-curator": "haiku"}
SKILLS = ["rinzler", "rinzler-core", "rinzler-plan", "rinzler-evals", "rinzler-bugs", "rinzler-data", "rinzler-perf",
          "rinzler-local"]


def front(p: Path):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", p.read_text(), re.S)
    assert m, f"{p} has no frontmatter"
    return yaml.safe_load(m.group(1)), m.group(2)


def test_every_agent_has_an_explicit_model():
    found = {p.stem for p in (ROOT / "agents").glob("*.md")}
    assert found == set(AGENTS)
    for name, model in AGENTS.items():
        fm, body = front(ROOT / "agents" / f"{name}.md")
        assert fm["name"] == name and fm["model"] == model, name
        assert len(fm["description"]) > 80 and len(body.split()) > 150, name


def test_lead_preloads_core_and_states_the_hard_rules():
    fm, body = front(ROOT / "agents" / "rinzler.md")
    assert fm["skills"] == ["rinzler-core"] and fm["memory"] == "user"
    for phrase in ("Never write to your company code repos", "Never print, copy or write secrets",
                   "Never use production data or production endpoints for destructive tests", "SELECT-only",
                   "only after his explicit yes", "logs in by hand", "Do not commit or push",
                   "never write into the caller's project unless the brief explicitly asks",
                   'Skill tool with skill "rinzler-core"', "your-db-skill", "your-db-analyst"):
        assert phrase in body, phrase


def test_helpers_cannot_spawn_or_ask():
    for name in AGENTS:
        if name == "rinzler":
            continue
        fm, body = front(ROOT / "agents" / f"{name}.md")
        tools = [t.strip() for t in str(fm["tools"]).split(",")]
        assert "Agent" not in tools and "AskUserQuestion" not in tools, name
        assert re.search(r"Return at most \d+ lines", body), name


def test_skills_exist_and_reference_real_files():
    for s in SKILLS:
        fm, body = front(ROOT / "skills" / s / "SKILL.md")
        assert fm["name"] == s and fm["description"], s


def test_rinzler_paths_in_markdown_exist():
    texts = list((ROOT / "agents").glob("*.md")) + list((ROOT / "skills").rglob("*.md"))
    for p in texts:
        t = p.read_text()
        for rel in re.findall(r"\$RINZLER/([\w./-]+\.(?:json|yaml|py|md|ts|js|sh))\b", t):
            assert (ROOT / rel).exists(), f"{p.name} points to missing {rel}"
        for rel in re.findall(r"agent-stack/rinzler/([\w./-]+\.(?:json|yaml|py|md|ts|js|sh))\b", t):
            assert (ROOT / rel).exists(), f"{p.name} points to missing {rel}"


def test_teammate_protocol_blocks_and_caller():
    t = (ROOT / "skills/rinzler-core/references/teammate-protocol.md").read_text()
    for block in ("NEEDS_ALIGNMENT", "NEEDS_APPROVAL", "RESULT", "BLOCKED"):
        assert block in t
    caller = (ROOT / "skills/rinzler/SKILL.md").read_text()
    assert 'subagent_type: "rinzler"' in caller and "teammate-protocol.md" in caller
    assert "never yours" in caller


def test_no_atlassian_mcp_references():
    for p in list((ROOT / 'skills').rglob('*.md')) + list((ROOT / 'agents').glob('*.md')):
        assert 'mcp__claude_ai_Atlassian' not in p.read_text(), p


def test_bug_skill_requires_verifier_dedupe_and_approval():
    t = (ROOT / "skills/rinzler-bugs/SKILL.md").read_text()
    for phrase in ("rinzler-verifier", "dedupe", "fingerprint", "explicit yes", "jira_tool.py", "--dry-run", "paste"):
        assert phrase in t, phrase
