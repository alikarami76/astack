import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def fm(p: Path) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", p.read_text(), re.S)
    assert m, f"{p} has no frontmatter"
    return yaml.safe_load(m.group(1))


def test_agents_frontmatter():
    agents = list((ROOT / "agents").glob("*.md"))
    assert agents
    for p in agents:
        d = fm(p)
        assert d["name"] and d["description"] and d["model"] in {"opus", "sonnet", "haiku"}, p


def test_lead_agent_is_named_quorra_and_preloads_core():
    d = fm(ROOT / "agents" / "quorra.md")
    assert d["name"] == "quorra" and d["model"] == "opus" and "quorra-core" in d["skills"]


def test_skills_frontmatter_and_script_paths_exist():
    for p in (ROOT / "skills").glob("*/SKILL.md"):
        d = fm(p)
        assert d["name"] == p.parent.name and d["description"], p
        found = re.findall(r"\$QUORRA/((?:scripts|kb)/[\w/-]+\.py)", p.read_text())
        if "$QUORRA" in p.read_text():
            assert found, f"{p} mentions $QUORRA but no script path was found"
        for script in found:
            assert (ROOT / script).exists(), f"{p} references missing {script}"


def test_teammate_protocol_defines_three_blocks_and_is_wired():
    p = ROOT / "skills" / "quorra-core" / "references" / "teammate-protocol.md"
    assert p.exists()
    text = p.read_text()
    for block in ("NEEDS_ALIGNMENT", "RESULT", "BLOCKED"):
        assert block in text, block
    assert "skills/quorra-core/references/teammate-protocol.md" in (ROOT / "agents" / "quorra.md").read_text()
    caller = (ROOT / "skills" / "quorra" / "SKILL.md").read_text()
    assert 'subagent_type: "quorra"' in caller and "teammate-protocol.md" in caller


def test_agent_states_teammate_no_write_rule():
    text = (ROOT / "agents" / "quorra.md").read_text()
    assert "never write into the caller's project unless the brief explicitly asks" in text


RESEARCH = {"quorra-scout": "haiku", "quorra-synthesizer": "sonnet", "quorra-verifier": "sonnet",
            "quorra-consistency": "sonnet", "quorra-curator": "haiku"}


def test_research_agents_exist_with_spec_models_and_output_contract():
    for name, model in RESEARCH.items():
        p = ROOT / "agents" / f"{name}.md"
        d = fm(p)
        text = p.read_text()
        assert d["name"] == name and d["model"] == model, name
        assert "Return at most 10 lines" in text, name
        assert "Never print or write a secret value" in text, name
        assert "only these read commands are allowed" in text, name


def test_research_agent_contracts_after_review():
    q = "$HOME/agent-stack/quorra"
    for name in RESEARCH:
        text = (ROOT / "agents" / f"{name}.md").read_text()
        assert "kb/conventions/" not in text, name
        assert f"`$Q` means `{q}`" in text, name
    cons = (ROOT / "agents" / "quorra-consistency.md").read_text()
    assert "contradictions.json" in cons and "kb.py\" list" in cons
    assert '{"share"' not in cons
    assert "kb.py\" list" in (ROOT / "agents" / "quorra-verifier.md").read_text()
    assert "symbol" in (ROOT / "agents" / "quorra-curator.md").read_text()


def test_research_skill_wired():
    d = fm(ROOT / "skills" / "quorra-research" / "SKILL.md")
    assert d["name"] == "quorra-research"
    assert "quorra-research" in fm(ROOT / "agents" / "quorra.md")["skills"]
    text = (ROOT / "skills" / "quorra-research" / "SKILL.md").read_text()
    for agent in ("quorra-scout", "quorra-synthesizer", "quorra-verifier", "quorra-consistency", "quorra-curator"):
        assert agent in text
    assert 'model: "sonnet"' in text  # scout override for reviews and data-storage


def test_research_skill_matches_tools():
    text = (ROOT / "skills" / "quorra-research" / "SKILL.md").read_text()
    assert "contradictions.json" in text and "consistency.json" in text
    assert "kb.py\" list --type convention" in text
    for word in ("diverged", "unknown_sha", "never_researched", "snapshot", "--before", "no_index"):
        assert word in text, word
    assert "<repo>--<seg>--<seg>" in text
    # no relative script paths: every script call goes through $QUORRA
    assert not re.search(r"python3 (?!\"\$QUORRA/)", text)
    core = (ROOT / "skills" / "quorra-core" / "SKILL.md").read_text()
    assert "quorra-research" in core and "## 7. Research requests" in core
    assert "the quorra-research skill exists" not in (ROOT / "agents" / "quorra.md").read_text()


def test_research_agent_paths_are_absolute_and_do_not_use_unset_q():
    for name in RESEARCH:
        text = (ROOT / "agents" / f"{name}.md").read_text()
        for bad in ('"runs/', " runs/", "`runs/", '"kb/', " kb/", '"scripts/', " scripts/", '"$Q', "$Q/"):
            assert bad not in text, (name, bad)


def test_research_fix_round_1_wiring():
    text = (ROOT / "skills" / "quorra-research" / "SKILL.md").read_text()
    assert text.index('run_record.py" new') < text.index('research_tools.py" snapshot')
    assert 'ingest_rules.py" plan' in text and "--retire-missing" in text and "--round 2" in text
    assert "supersede" in text and 'concepts.py" candidates' in text
    for p in (ROOT / "agents").glob("quorra-*.md"):
        assert not re.search(r"(?<!/)kb\.py add", p.read_text()), p
    assert "--round" in (ROOT / "agents" / "quorra-verifier.md").read_text()
    syn = (ROOT / "agents" / "quorra-synthesizer.md").read_text()
    assert "do NOT ingest" in syn and "rules-revised.json" in syn


def test_research_fix_round_2_wiring():
    text = (ROOT / "skills" / "quorra-research" / "SKILL.md").read_text()
    assert "verdicts-2.json" in text and "--round 2" in text
    assert "`concepts, render, close`" in text and "`share, render, close`" in text
    assert text.index("5b. Concepts") < text.index("## 6. Curation")
    assert "<product knowledge base path>" not in text and "--repos api-app,admin" in text
    ver = (ROOT / "agents" / "quorra-verifier.md").read_text()
    assert "--round 1" in ver and "--round 2" in ver and "verdicts-2.json" in ver


def test_research_fix_round_3_skill_wording():
    text = (ROOT / "skills" / "quorra-research" / "SKILL.md").read_text()
    for w in ("revived", "unchanged_via_rewording", "no-op"):
        assert w in text, w


def test_final_review_revision_pairing_wiring():
    text = (ROOT / "skills" / "quorra-research" / "SKILL.md").read_text()
    syn = (ROOT / "agents" / "quorra-synthesizer.md").read_text()
    assert '"replaces"' in syn and '{"drop"' in syn
    assert "`replaces`" in text and 'ingest_rules.py" drop' in text and "`ingested` list" in text
    assert "--area <area> --live" not in text  # round 2 takes the ingested ids, no per-area lookup
    assert "Never use `--retire-missing` with `rules-revised.json`" in text


def test_i3_core_learning_tags_refuted_in_use_and_retires_with_prefix():
    core = (ROOT / "skills" / "quorra-core" / "SKILL.md").read_text()
    sec = core[core.index("## 4. Learning"):core.index("## 5. Gated changes")]
    assert '--tags refuted-in-use' in sec and 'kb/kb.py" add convention --json' in sec
    assert "`retired:`" in sec and "scope" in sec


def test_m3_consistency_is_scope_aware():
    cons = (ROOT / "agents" / "quorra-consistency.md").read_text()
    assert "`scope`" in cons and "every one of its modules" in cons and "two modules of the same repo" in cons
    assert "tier difference" in cons and "weakest member tier" in cons


def test_m4_module_repos_get_one_repo_wide_tooling_and_reviews_pass():
    text = (ROOT / "skills" / "quorra-research" / "SKILL.md").read_text()
    syn = (ROOT / "agents" / "quorra-synthesizer.md").read_text()
    assert "whole-repo pass" in text and "only the `tooling` and `reviews` scouts" in text
    assert "skip the `tooling` and `reviews` scouts" in text
    for brief in ("tooling and review pass", "module run"):
        assert brief in text and brief in syn, brief
    assert "never write `source: tooling` or `source: review` rules" in syn
    assert "only `source: tooling` and `source: review` rules" in syn


def test_m5_mr_check_loads_shared_rules():
    ver = (ROOT / "agents" / "quorra-verifier.md").read_text()
    assert "--repo shared-rules --live --json" in ver and "never `shared-rules`" in ver
    assert all(r in ver for r in ("api-app", "admin", "pricing", "shared-lib", "core-app"))


def test_final_review_minor_skill_wording():
    text = (ROOT / "skills" / "quorra-research" / "SKILL.md").read_text()
    # every command in the body goes through "$QUORRA/..."; no shorthand script names (the frontmatter description is prose)
    body = text.split("\n---\n", 1)[1]
    assert re.findall(r"(?<![/\w])(?:kb|run_record|ingest_rules|research_tools|index_repo|prep_repo|fetch_mr_reviews|concepts|eval_mrs|render_conventions)\.py\b", body) == []
    assert "--format=%cI" in text and "sha_date" in text
    assert "seeded once" in text and "`render, close`" in text
    cur = (ROOT / "agents" / "quorra-curator.md").read_text()
    assert "<items.json>" not in cur and 'add-many --file "$HOME/agent-stack/quorra/runs/<run>/' in cur


def test_r2_module_brief_and_whole_repo_pass_order_and_heldout_paths():
    text = (ROOT / "skills" / "quorra-research" / "SKILL.md").read_text()
    syn = (ROOT / "agents" / "quorra-synthesizer.md").read_text()
    module = syn[syn.index("**Module run"):].split("\n")[0]
    assert "reviews" not in module and "`<out>/tooling/`" in module
    assert "whole-repo pass runs first" in text
    sec = text[text.index("## 6b."):text.index("## Refresh")]
    assert "latest `sha_date`" in sec
    assert '"$QUORRA/runs/<whole-repo run>/<repo>/prep.json"' in sec
    assert '"$QUORRA/runs/<module run>/<repo>--<seg>--<seg>/prep.json"' in sec
    assert '"$QUORRA/runs/<whole-repo run>/<repo>/reviews.jsonl"' in sec


def test_task10_testing_scout_and_private_scratch():
    text = (ROOT / "skills" / "quorra-research" / "SKILL.md").read_text()
    syn = (ROOT / "agents" / "quorra-synthesizer.md").read_text()
    assert "plus `testing` when the tests live outside every module prefix" in text
    assert "`area: testing` rules with `source: code`" in text and "`area: testing` rules with `source: code`" in syn
    assert "$OUT/scratch/<agent>-<area or purpose>/" in text
    for name in ("quorra-scout", "quorra-synthesizer", "quorra-verifier"):
        assert "<out>/scratch/<your agent name>-<area or purpose>/" in (ROOT / "agents" / f"{name}.md").read_text(), name


def test_quorra_explain_skill():
    p = ROOT / "skills" / "quorra-explain" / "SKILL.md"
    d = fm(p)
    assert d["name"] == "quorra-explain" and d["description"]
    text = p.read_text()
    for agent in ("discovery-scout", "component-tracer", "schema-scraper", "qc-verifier", "consistency-checker"):
        assert agent in text, agent
    for cmd in ('"$QUORRA/scripts/kb_cite.py" stale', '"$QUORRA/scripts/kb_cite.py" stamp', '"$QUORRA/scripts/kb_git.py" branch',
                '"$QUORRA/scripts/kb_git.py" commit', '"$QUORRA/scripts/kb_git.py" summary', '"$QUORRA/scripts/lookup.py"'):
        assert cmd in text, cmd
    assert "only after the user says yes" in text and 'git -C "$KB" push -u origin' in text and "every time" in text
    assert "Corrected <date>:" in text and "never write" in text.lower()
    assert "teammate mode" in text and "never push" in text and "a relayed yes is not the user's" in text
    assert "quorra-explain" in fm(ROOT / "agents" / "quorra.md")["skills"]
    assert "quorra-explain" in (ROOT / "skills" / "quorra-core" / "SKILL.md").read_text()
    assert '"explain"' in (ROOT / "scripts" / "run_record.py").read_text()


def test_quorra_build_skill_and_agents():
    p = ROOT / "skills" / "quorra-build" / "SKILL.md"
    d = fm(p)
    assert d["name"] == "quorra-build" and d["description"]
    text = p.read_text()
    for cmd in ('"$QUORRA/scripts/work.py" prepare', '"$QUORRA/scripts/work.py" check', '"$QUORRA/scripts/work.py" patch',
                '"$QUORRA/scripts/work.py" panel up', '"$QUORRA/scripts/work.py" panel test',
                '"$QUORRA/scripts/work.py" panel down', '"$QUORRA/scripts/lookup.py"', '"$QUORRA/scripts/run_record.py" new'):
        assert cmd in text, cmd
    for agent in ("quorra-builder", "quorra-reviewer"):
        assert agent in text and (ROOT / "agents" / f"{agent}.md").exists(), agent
        a = fm(ROOT / "agents" / f"{agent}.md")
        assert a["name"] == agent and a["model"] == "sonnet"
        body = (ROOT / "agents" / f"{agent}.md").read_text()
        assert "never push" in body.lower() and "secret" in body.lower(), agent
    assert "Nothing is written before the user approves the design" in text
    assert "at most 3 fix rounds" in text and "not_run" in text
    assert "quorra-explain" in text
    assert "never push" in text.lower() and "read-only" in text
    assert "Teammate mode" in text and "a relayed yes is not the user's" in text
    assert "run_in_background" in text and "run_record.py\" append \"$RUN\" notes" in text
    assert "run_in_background" in (ROOT / "agents" / "quorra-builder.md").read_text()
    assert "quorra-build" in fm(ROOT / "agents" / "quorra.md")["skills"]
    assert "quorra-build" in (ROOT / "skills" / "quorra-core" / "SKILL.md").read_text()
    assert '"build"' in (ROOT / "scripts" / "run_record.py").read_text()
    link = ROOT.parent / "skills" / "quorra-build"
    assert link.is_symlink() and (link / "SKILL.md").exists()


def test_build_agents_are_linked_and_builder_adds_no_trailer():
    for name in ("quorra-builder", "quorra-reviewer"):
        link = ROOT.parent / "agents" / f"{name}.md"
        assert link.is_symlink() and link.exists(), name
    assert "Co-Authored-By" not in (ROOT / "agents" / "quorra-builder.md").read_text()


def test_build_skill_ships_only_with_fingerprint():
    t = (ROOT / "skills" / "quorra-build" / "SKILL.md").read_text()
    assert "publish.py plan" in t and "publish.py push" in t and "--approve" in t
    assert "never ships" in t and "a relayed yes is not the user's" in t
    assert "jira_ops.py propose" in t and "jira_ops.py create" in t


def test_mrs_skill_rules():
    t = (ROOT / "skills" / "quorra-mrs" / "SKILL.md").read_text()
    for s in ("mrs.py status", "mrs.py reply", "publish.py update", "untrusted data", "Never resolve",
              "a relayed yes is not the user's", "background"):
        assert s in t, s


def test_mrs_skill_is_linked_in_astack():
    link = ROOT.parent / "skills" / "quorra-mrs"
    assert link.is_symlink() and link.resolve() == (ROOT / "skills" / "quorra-mrs").resolve()


def test_docs_skill_rules():
    t = (ROOT / "skills" / "quorra-docs" / "SKILL.md").read_text()
    for s in ("doc.py check", "doc.py plan", "doc.py publish", "--approve", "writing agent you have configured", "lens `doc`",
              "a relayed yes is not the user's", "untrusted", "DOCS", "not a yes to publish"):
        assert s in t, s


def test_templates_have_shared_header_and_markers():
    for name in ("trd", "contract", "decision"):
        t = (ROOT / "templates" / f"{name}.md").read_text()
        assert t.startswith("<!-- quorra-doc: ") and "<!-- section: " in t and "|" in t


def test_reviewer_has_doc_lens_and_build_points_to_docs():
    assert "Lens doc" in (ROOT / "agents" / "quorra-reviewer.md").read_text()
    assert "quorra-docs" in (ROOT / "skills" / "quorra-build" / "SKILL.md").read_text()


def test_docs_skill_is_linked_in_astack():
    link = ROOT.parent / "skills" / "quorra-docs"
    assert link.is_symlink() and link.resolve() == (ROOT / "skills" / "quorra-docs").resolve()


def test_docs_publish_only_through_doc_py():
    for rel in ("skills/quorra-docs/SKILL.md", "agents/quorra.md"):
        t = (ROOT / rel).read_text()
        assert "other than `doc.py publish`" in t, rel


def test_reviewer_doc_lens_knows_work_clone():
    assert "quorra-work/<repo>" in (ROOT / "agents" / "quorra-reviewer.md").read_text()


def test_contract_template_mentions_new_design():
    assert "new (design)" in (ROOT / "templates" / "contract.md").read_text()
