import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FLYNN = "$HOME/agent-stack/flynn"
MODELS = {"flynn": "opus", "flynn-scout": "sonnet", "flynn-librarian": "sonnet", "flynn-composer": "sonnet",
          "flynn-renderer": "sonnet", "flynn-critic": "sonnet", "flynn-checker": "haiku", "flynn-curator": "haiku"}


def fm(p: Path) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", p.read_text(), re.S)
    assert m, f"{p} has no frontmatter"
    return yaml.safe_load(m.group(1))


def test_agents_exist_with_spec_models():
    names = {p.stem for p in (ROOT / "agents").glob("*.md")}
    assert names == set(MODELS)
    for name, model in MODELS.items():
        d = fm(ROOT / "agents" / f"{name}.md")
        assert d["name"] == name and d["model"] == model and d["description"], name


def test_lead_preloads_core_and_wires_teammate_protocol():
    p = ROOT / "agents" / "flynn.md"
    d, text = fm(p), p.read_text()
    assert "flynn-core" in d["skills"] and d.get("memory") == "user"
    assert "skills/flynn-core/references/teammate-protocol.md" in text
    assert "never write into the caller's project unless the brief explicitly asks" in text


def test_specialist_contracts():
    for name in MODELS:
        if name == "flynn":
            continue
        text = (ROOT / "agents" / f"{name}.md").read_text()
        assert "Return at most 10 lines" in text, name
        assert "Never print or write a secret value" in text, name
        assert f"`$FLYNN` means `{FLYNN}`" in text, name


def test_skills_frontmatter_and_script_paths_exist():
    skills = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    assert {"flynn", "flynn-core", "flynn-review"} <= skills          # skills made from flynn desk add to these
    for p in (ROOT / "skills").glob("*/SKILL.md"):
        d, text = fm(p), p.read_text()
        assert d["name"] == p.parent.name and d["description"], p
        for script in re.findall(r"\$FLYNN/((?:scripts|kb)/[\w/-]+\.py)", text):
            assert (ROOT / script).exists(), f"{p} references missing {script}"


def test_agent_script_paths_exist():
    for p in (ROOT / "agents").glob("*.md"):
        for script in re.findall(r"\$FLYNN/((?:scripts|kb)/[\w/-]+\.py)", p.read_text()):
            assert (ROOT / script).exists(), f"{p} references missing {script}"


def test_teammate_protocol_blocks_and_caller_skill():
    proto = (ROOT / "skills/flynn-core/references/teammate-protocol.md").read_text()
    for block in ("NEEDS_ALIGNMENT", "RESULT", "BLOCKED", "stage:"):
        assert block in proto, block
    caller = (ROOT / "skills/flynn/SKILL.md").read_text()
    assert 'subagent_type: "flynn"' in caller and "teammate-protocol.md" in caller
    assert "other agents" in caller.lower()


def test_rubric_lists_ai_aesthetic_traits_and_contract_has_states():
    rubric = (ROOT / "skills/flynn-core/references/rubric.md").read_text().lower()
    for trait in ("purple", "gradient", "rounded", "hero", "card grid", "lorem", "shadow", "padding"):
        assert trait in rubric, trait
    contract = (ROOT / "skills/flynn-core/references/design-contract.md").read_text().lower()
    for state in ("loading", "empty", "error", "success", "permission"):
        assert state in contract, state


TOOLS = {"flynn-critic": "Read, Write, Bash, Grep, Glob", "flynn-composer": "Read, Write, Edit, Bash, Grep, Glob",
         "flynn-checker": "Read, Write, Bash, Grep, Glob", "flynn-librarian": "Read, Write, Bash, Grep, Glob",
         "flynn-curator": "Read, Write, Bash, Grep, Glob"}


def test_helper_agents_pin_tool_lists():
    # no tools line means every spawn starts with ~25k extra tokens of context
    for name, tools in TOOLS.items():
        assert fm(ROOT / "agents" / f"{name}.md").get("tools") == tools, name
