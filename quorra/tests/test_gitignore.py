import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ignored(rel: str) -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", "--no-index", rel]).returncode == 0


def test_copied_run_inputs_are_ignored_but_run_records_are_not():
    for rel in ("runs/2026-10-01-x/api-app/tooling/phpstan.neon", "runs/2026-10-01-x/api-app/guides/CONTRIBUTING.md",
                "runs/2026-10-01-x/api-app/reviews.jsonl", "runs/2026-10-01-x/api-app/areas/data-storage.txt"):
        assert _ignored(rel), rel
    for rel in ("runs/2026-10-01-x/run.json", "runs/2026-09-29-example-run/run.json"):
        assert not _ignored(rel), rel


def test_i4_everything_under_runs_is_ignored_except_run_json():
    for rel in ("runs/2026-10-01-x/consistency.json", "runs/2026-10-01-x/contradictions.json", "runs/2026-10-01-x/concepts.json",
                "runs/2026-10-01-x/mr-labels.jsonl", "runs/2026-10-01-x/api-app/rules.json", "runs/2026-10-01-x/api-app/prep.json",
                "runs/2026-10-01-x/api-app/scouts/naming.md", "runs/2026-10-01-x/shared-lib--src--A/verdicts.json",
                "runs/2026-10-01-x/api-app/run.json", "runs/notes.md", "runs/smoke/x.txt"):
        assert _ignored(rel), rel
    assert not _ignored("runs/2026-10-01-x/run.json")
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "runs"], capture_output=True, text=True).stdout.split()
    for rel in tracked:
        assert rel.endswith("/run.json") and not _ignored(rel), rel
