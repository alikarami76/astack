import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(home):
    return subprocess.run(["bash", str(ROOT / "install.sh")], env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)


def test_install_links_everything_and_is_idempotent(tmp_path):
    for _ in range(2):
        r = _run(tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
    agents = {p.name for p in (tmp_path / ".claude/agents").iterdir()}
    assert agents == {p.name for p in (ROOT / "agents").glob("*.md")}
    for s in ("flynn", "flynn-core", "flynn-review"):
        assert (tmp_path / ".claude/skills" / s).resolve() == (ROOT / "skills" / s).resolve()
    assert (tmp_path / ".zshrc").read_text().count("alias flynn='claude --agent flynn'") == 1


def test_install_refuses_to_overwrite_real_file(tmp_path):
    a = tmp_path / ".claude/agents"
    a.mkdir(parents=True)
    (a / "flynn.md").write_text("mine")
    r = _run(tmp_path)
    assert r.returncode != 0 and "REFUSING" in r.stdout
    assert (a / "flynn.md").read_text() == "mine"
