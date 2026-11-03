import re
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENTS = {"clu": "opus", "clu-challenger": "sonnet", "clu-data-runner": "haiku", "clu-verifier": "sonnet",
          "clu-editor": "haiku", "clu-curator": "sonnet", "clu-methodologist": "opus"}
SKILLS = ["clu-core", "clu", "clu-review", "clu-causal"]


def front(p: Path) -> tuple[dict, str]:
    text = p.read_text()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert m, f"{p} has no frontmatter"
    return yaml.safe_load(m.group(1)), m.group(2)


def test_agents_frontmatter():
    for name, model in AGENTS.items():
        fm, body = front(ROOT / "agents" / f"{name}.md")
        assert fm["name"] == name and fm["model"] == model
        assert len(fm["description"]) > 80
        assert len(body.split()) > 150, f"{name} body too thin"


def test_lead_preloads_core_and_has_memory():
    fm, body = front(ROOT / "agents" / "clu.md")
    assert fm["skills"][0] == "clu-core" and fm["memory"] == "user"
    assert "AskUserQuestion" in body and "NEEDS_ALIGNMENT" in body
    assert 'Skill tool with skill "clu-core"' in body


def test_helpers_cannot_spawn_or_ask():
    for name in AGENTS:
        if name == "clu":
            continue
        fm, _ = front(ROOT / "agents" / f"{name}.md")
        tools = [t.strip() for t in str(fm["tools"]).split(",")]
        assert "Agent" not in tools and "AskUserQuestion" not in tools


def test_skills_exist_and_reference_real_files():
    for s in SKILLS:
        fm, body = front(ROOT / "skills" / s / "SKILL.md")
        assert fm["name"] == s and fm["description"]
        for ref in re.findall(r"(?<![/\w])references/[\w-]+\.md", body):
            assert (ROOT / "skills" / s / ref).exists(), f"{s} references missing {ref}"


def test_paths_in_markdown_exist():
    for p in list((ROOT / "agents").glob("*.md")) + list((ROOT / "skills").rglob("*.md")):
        for rel in re.findall(r'\$CLU/([\w./-]+\.py)', p.read_text()):
            assert (ROOT / rel).exists(), f"{p.name} points to missing {rel}"

