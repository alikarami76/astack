"""Letters in flynn desk: documents people read (release notes, reports, decks, newsletters).

flynn desk has two file types, like Figma Design and Figma Slides: Designs (product UI) and Letters. Either can live
in any team and project. A letter is a file whose master is a flynn run built from flynn's own document
template (adapters/html), with the editorial theme. The canvas shows the run as real, editable elements (one
board per slide for decks); exports read the user's canvas edits back into the page first, then render with flynn's
pipeline (PDF, section PNGs, PowerPoint, email draft, Canva).

  letters.py ids                          the files that are letters
  letters.py new --type deck --title "Q3 pricing review" [--project ID]
  letters.py list | show FILE_ID

State: fstore ns letters, one {type, title, run, theme, created} per file id (~/.flynn/letters/<file id>.json,
or Postgres with FLYNN_DB_URL).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "library"))
sys.path.insert(0, str(FLYNN / "scripts"))
import fstore  # noqa: E402

SPACES = Path.home() / ".flynn" / "spaces.json"
META = Path.home() / ".flynn" / "letters"
DESIGNS_TEAM = os.environ.get("FLYNN_HOME_TEAM", "flynn")   # the team server names its home team
LETTERS_TEAM = "Letters"
# The registry is fstore ns "letters", one document per file id; on files, ~/.flynn/letters/<file id>.json as before.
fstore.files("letters", folder=lambda: META, indent=1)

TYPES = {
    "deck": {"label": "Deck", "template": "deck", "width": 1280, "hint": "16:9 slides"},
    "newsletter": {"label": "Newsletter", "template": "newsletter", "width": 640, "hint": "640px email"},
    "release-note": {"label": "Release note", "template": "release-page", "width": 1440, "hint": "long page"},
    "report": {"label": "Report", "template": "report", "width": 1440, "hint": "findings with charts"},
}


def _owner():
    import builder as B
    return B.owner()


def letter_ids() -> list[str]:
    """Every file that is a letter (the dashboard's Letters view shows these, in any team)."""
    return [*fstore.list("letters")]


def default_team() -> str:
    o = _owner()
    return next(t["id"] for t in o("get-teams") if t["name"] == DESIGNS_TEAM)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return s or "letter"


def new(kind: str, title: str, project: str | None = None, theme: str = "editorial", team: str | None = None) -> dict:
    """A letter is a file type, not a place: it goes into the given project, or the given team's Drafts."""
    if kind not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    title = (title or "").strip() or TYPES[kind]["label"]
    o = _owner()
    team = team or default_team()
    if not project:
        projects = o("get-projects", {"teamId": team})
        project = next((p["id"] for p in projects if p.get("isDefault")), None) or projects[0]["id"]
    else:
        team = next((p["teamId"] for p in o("get-projects", {"teamId": team}) if p["id"] == project), team)
    f = o("create-file", {"projectId": project, "name": title})
    sys.path.insert(0, str(FLYNN / "scripts"))
    import run_record
    run = Path(run_record.new(slug(title), "deck" if kind == "deck" else "graphic", f"Letter in flynn desk ({TYPES[kind]['label']}): {title}",
                              system=theme))
    subprocess.run([sys.executable, str(FLYNN / "adapters" / "html" / "new_page.py"), str(run), "--template",
                    TYPES[kind]["template"], "--theme", theme], check=True, capture_output=True, text=True)
    meta = {"file": f["id"], "team": team, "type": kind, "title": title, "run": str(run), "theme": theme,
            "created": dt.datetime.now().isoformat(timespec="seconds"), "built": False}
    save(meta)
    return meta


def get(file_id: str) -> dict | None:
    return fstore.get("letters", file_id)


def save(meta: dict) -> None:
    fstore.put("letters", meta["file"], meta)


def themes() -> list[dict]:
    out = []
    for d in sorted((FLYNN / "adapters" / "html" / "themes").iterdir()):
        if d.is_dir():
            out.append({"system": d.name, "label": " ".join(w.capitalize() for w in d.name.split("-")),
                        "kind": "theme"})
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="letters.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ids")
    n = sub.add_parser("new")
    n.add_argument("--type", required=True, choices=sorted(TYPES))
    n.add_argument("--title", required=True)
    n.add_argument("--project")
    sub.add_parser("list")
    sh = sub.add_parser("show")
    sh.add_argument("file")
    a = ap.parse_args(argv)
    if a.cmd == "ids":
        print(json.dumps(letter_ids()))
    elif a.cmd == "new":
        print(json.dumps(new(a.type, a.title, a.project), ensure_ascii=False, indent=1))
    elif a.cmd == "list":
        for m in fstore.list("letters").values():
            print(f"{m['file']}  {m['type']:13} {m['title']}  {m['run']}")
    else:
        print(json.dumps(get(a.file), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
