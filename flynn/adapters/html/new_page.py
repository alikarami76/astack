#!/usr/bin/env python3
"""flynn HTML adapter: start a page in a run folder from a template and a theme.

  new_page.py <run folder> [--template release-page] [--theme editorial] [--force]

Creates <run>/page/ with index.html (from themes/<theme>/templates/<template>.html if the theme has one, else templates/<template>.html), every *.css file, fonts/ and img/ (from
themes/<theme>/), and an img/ for screenshots. Refuses to overwrite an existing page unless --force."""
import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def templates(theme: str | None = None) -> list[str]:
    """Shared templates, plus the ones a theme ships in themes/<theme>/templates/."""
    names = {p.stem for p in (HERE / "templates").glob("*.html")}
    if theme:
        names |= {p.stem for p in (HERE / "themes" / theme / "templates").glob("*.html")}
    return sorted(names)


def _template_path(template: str, theme: str) -> Path:
    """A template of the theme's own wins over a shared one of the same name (both themes may have a `newsletter`)."""
    own = HERE / "themes" / theme / "templates" / f"{template}.html"
    return own if own.exists() else HERE / "templates" / f"{template}.html"


def new_page(run: Path, template: str = "release-page", theme: str = "editorial", force: bool = False) -> Path:
    th = HERE / "themes" / theme
    tpl = _template_path(template, theme)
    if not tpl.exists():
        raise SystemExit(f"no template {template!r}; available: {', '.join(templates(theme))}")
    if not th.is_dir():
        raise SystemExit(f"no theme {theme!r}")
    page = run / "page"
    if (page / "index.html").exists() and not force:
        raise SystemExit(f"{page / 'index.html'} exists; pass --force to overwrite")
    (page / "img").mkdir(parents=True, exist_ok=True)
    shutil.copy(tpl, page / "index.html")
    for css in sorted(th.glob("*.css")):  # style.css plus any template-specific sheets (deck.css, newsletter.css)
        shutil.copy(css, page / css.name)
    if (th / "fonts").is_dir():
        shutil.copytree(th / "fonts", page / "fonts", dirs_exist_ok=True)
    if (th / "img").is_dir():  # theme artwork such as the logo (dk-mckinsey)
        shutil.copytree(th / "img", page / "img", dirs_exist_ok=True)
    return page


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--template", default="release-page")
    ap.add_argument("--theme", default="editorial")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    print(new_page(Path(a.run), a.template, a.theme, a.force))
    return 0


if __name__ == "__main__":
    sys.exit(main())
