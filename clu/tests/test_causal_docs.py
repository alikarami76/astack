from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SK = ROOT / "skills/clu-causal"
REFS = {
    "method-selection.md": ["assignment interview", "Callaway", "Sun-Abraham", "never plain TWFE", "synthetic control",
                            "rdrobust", "rddensity", "instrument", "complier", "interrupted time series", "estimand",
                            "cstat.py did", "cstat.py rdd", "cstat.py synth", "cstat.py its"],
    "experiment-design.md": ["interference", "what do treated and control units compete for", "scarce side",
                             "switchback", "budget-split", "design effect", "cstat.py power", "guardrail",
                             "novelty", "lead-day", "pull-forward", "holdout", "hashAttribute", "namespace"],
    "natural-experiments.md": ["threshold", "staggered", "alternating", "outage", "capacity", "rounding", "as good as random"],
    "stat-checks.md": ["MDE", "inconclusive", "cluster", "wild", "randomisation inference", "HAC", "Benjamini-Hochberg",
                       "forking paths", "spec_log.jsonl", "regression to the mean", "Simpson", "winsor", "PPML",
                       "delta method", "SRM", "Twyman", "Ramadan", "Jalali", "stable currency", "substitution"],
    "growthbook.md": ["growthbook.py experiments", "growthbook.py results", "cstat.py gbcheck", "read-only",
                      "CUPED", "sequential", "adjusted p-values", "interference", "cluster"],
}


def test_skill_frontmatter_and_protocol():
    text = (SK / "SKILL.md").read_text()
    assert text.startswith("---\nname: clu-causal\n")
    for step in ["Assignment interview", "clu-methodologist", "clu-challenger", "analysis_plan.md", "plan_locked_at",
                 "spec_log.jsonl", "Diagnostics and sensitivity", "clu-verifier", "estimand", "inconclusive",
                 "experiment", ".venv-causal/bin/python", "setup.sh"]:
        assert step in text, f"SKILL.md missing {step!r}"
    assert len(text.split()) < 1200, "keep SKILL.md lean; detail goes in references"


def test_references_cover_required_content():
    for name, tokens in REFS.items():
        text = (SK / "references" / name).read_text()
        low = text.lower()
        missing = [t for t in tokens if t.lower() not in low]
        assert not missing, f"{name} missing {missing}"
        assert "http" in text, f"{name} must cite sources"


def test_methodologist_agent():
    import re, yaml
    text = (ROOT / "agents/clu-methodologist.md").read_text()
    fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1))
    assert fm["name"] == "clu-methodologist" and fm["model"] == "opus"
    tools = [t.strip() for t in fm["tools"].split(",")]
    assert "Agent" not in tools and "AskUserQuestion" not in tools and "Write" in tools
    for t in ["causal diagram", "estimand", "ranked", "analysis_plan.md", "MDE", "interference", "never plain TWFE",
              "method-selection.md", "experiment-design.md", "natural-experiments.md", "cstat.py"]:
        assert t in text, f"methodologist missing {t!r}"


def test_core_and_challenger_wiring():
    core = (ROOT / "skills/clu-core/SKILL.md").read_text()
    assert "| causal |" in core and "clu-causal" in core
    ch = (ROOT / "agents/clu-challenger.md").read_text()
    for t in ["Causal plans", "interference", "staggered", "TWFE", "cluster", "forking paths", "estimand"]:
        assert t.lower() in ch.lower(), f"challenger missing {t!r}"
    import run_record
    assert "causal" in run_record.TYPES
