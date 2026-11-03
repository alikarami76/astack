"""pdfsmith command line."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import webbrowser
from pathlib import Path

import pikepdf
import pymupdf
import yaml

from . import __version__, fonts
from .build import STAGES, build
from .check import run_checks
from .compose import compose, list_templates
from .errors import EnvError, InputError, PdfsmithError
from .latex import tectonic_version
from .paths import BUNDLED_FONTS, FONTS, PAGED_SHA256, TEMPLATES, VENDOR
from .schema import LATEX


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _summary(report: dict) -> None:
    failing = [c for c in report["checks"] if c["status"] == "fail"]
    for check in report["checks"]:
        if check["status"] in ("fail", "warn"):
            _err(f"{check['status'].upper()} {check['name']}: {check['detail']}")
            for item in check["items"][:10]:
                _err(f"  - {item}")
    _err(f"{'PASSED' if report['passed'] else 'FAILED'}: {report['pages']} pages, {len(failing)} failing check(s)")


def cmd_new(a) -> int:
    source = TEMPLATES / a.template / "example" / a.lang
    if a.template == LATEX and a.lang != "en":
        raise InputError(f"template 'latex' with --lang {a.lang} is not implemented yet; only --lang en is available")
    if not source.is_dir():
        raise InputError(f"template '{a.template}' has no '{a.lang}' example. Templates: {', '.join(list_templates() + [LATEX])}")
    target = Path(a.dir)
    if target.exists() and not target.is_dir():
        raise InputError(f"{target} exists and is not a folder")
    if target.exists() and any(target.iterdir()):
        raise InputError(f"{target} exists and is not empty")
    shutil.copytree(source, target, dirs_exist_ok=True)
    if a.theme:
        doc_file = target / "doc.yaml"
        doc = yaml.safe_load(doc_file.read_text(encoding="utf-8"))
        doc["theme"] = a.theme
        doc_file.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(target.resolve())
    return 0


def cmd_build(a) -> int:
    pdf, report = build(Path(a.dir), a.stage)
    if report is None:
        print(pdf if pdf.is_file() else Path(a.dir) / "build")
        return 0
    _summary(report)
    _err(f"Full report: {Path(a.dir) / 'build' / 'report.json'}")
    print(pdf)
    return 0 if report["passed"] else 3


def _existing_pdf(path: str) -> Path:
    pdf = Path(path)
    if not pdf.is_file():
        raise InputError(f"{pdf} not found")
    return pdf


def cmd_check(a) -> int:
    pdf = _existing_pdf(a.pdf)
    info, geometry = {}, None
    if a.spec:
        work = Path(a.spec).resolve().parent / "build"
        if (work / "compose.json").is_file():
            info = json.loads((work / "compose.json").read_text(encoding="utf-8"))
        if (work / "geometry.json").is_file():
            geometry = json.loads((work / "geometry.json").read_text(encoding="utf-8"))
    report = run_checks(pdf, info, geometry)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    _summary(report)
    return 0 if report["passed"] else 3


def _page_range(spec: str | None, count: int) -> tuple[int, int]:
    if not spec:
        return 1, count
    lo_text, _, hi_text = spec.partition("-")
    try:
        lo = int(lo_text)
        hi = int(hi_text) if hi_text else lo
    except ValueError:
        raise InputError(f"--range {spec} is not a page or page range such as 3 or 2-5") from None
    if not 1 <= lo <= hi <= count:
        raise InputError(f"--range {spec} is outside 1-{count}")
    return lo, hi


def _open_pdf(path: str):
    pdf = _existing_pdf(path)
    try:
        doc = pymupdf.open(pdf)
    except pymupdf.FileDataError as e:
        raise InputError(f"{pdf} is not a readable PDF: {e}") from e
    if doc.needs_pass:
        doc.close()
        raise InputError(f"{pdf} is encrypted and needs a password to open")
    return doc


def cmd_pages(a) -> int:
    doc = _open_pdf(a.pdf)
    lo, hi = _page_range(a.range, doc.page_count)
    out = Path(a.out) if a.out else Path(a.pdf).with_name(Path(a.pdf).stem + "-pages")
    out.mkdir(parents=True, exist_ok=True)
    for index in range(lo - 1, hi):
        path = out / f"page-{index + 1:03d}.png"
        doc[index].get_pixmap(dpi=a.dpi).save(path)
        print(path)
    return 0


def cmd_preview(a) -> int:
    composed = compose(Path(a.dir))
    webbrowser.open(composed.html_path.resolve().as_uri())
    print(composed.html_path)
    return 0


def cmd_doctor(a) -> int:
    from .render import chrome_version

    rows = [("python", sys.version.split()[0], True)]
    try:
        rows.append(("chrome", chrome_version(), True))
    except PdfsmithError as e:
        rows.append(("chrome", str(e), False))
    paged = VENDOR / "paged.polyfill.js"
    sha = hashlib.sha256(paged.read_bytes()).hexdigest() if paged.is_file() else "missing"
    rows.append(("paged.js 0.4.3", sha[:12], sha == PAGED_SHA256))
    for rel in BUNDLED_FONTS:
        present = (FONTS / rel).is_file()
        rows.append((f"font {rel}", "ok" if present else "missing", present))
    try:
        rows.append(("tectonic (latex route)", tectonic_version(), True))
    except EnvError as e:
        # Optional: only `template: latex` needs it, so a missing tectonic does not fail doctor.
        rows.append(("tectonic (latex route, optional)", str(e), True))
    rows.append(("pdftoppm (optional)", shutil.which("pdftoppm") or "not found", True))
    for name, value, good in rows:
        _err(f"{'ok ' if good else 'BAD'} {name}: {value}")
    return 0 if all(good for _, _, good in rows) else 4


def cmd_fonts(a) -> int:
    faces = fonts.scan(Path(a.project) if a.project else None, refresh=a.action == "scan")
    if a.action == "scan":
        print(f"{len(faces)} font faces")
        return 0
    for face in sorted(faces, key=lambda f: (f.family.lower(), f.weight, f.style)):
        if a.filter and a.filter.lower() not in face.family.lower():
            continue
        weight = f"{face.weight_range[0]}-{face.weight_range[1]}" if face.variable else str(face.weight)
        print(f"{face.family}\t{face.style}\t{weight}\t{face.source}\t{face.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdfsmith", description="Build checked PDFs from Markdown or HTML, with themes and Persian support.")
    parser.add_argument("--version", action="version", version=f"pdfsmith {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="create a build folder from a template's example")
    p.add_argument("template")
    p.add_argument("dir")
    p.add_argument("--lang", default="en", choices=["en", "fa"])
    p.add_argument("--theme")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("build", help="run the pipeline on a build folder")
    p.add_argument("dir")
    p.add_argument("--stage", choices=STAGES)
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("check", help="run the mechanical checks on a PDF")
    p.add_argument("pdf")
    p.add_argument("--spec", help="doc.yaml of the build folder, to use its compose.json and geometry.json")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("pages", help="render pages to PNG for inspection")
    p.add_argument("pdf")
    p.add_argument("--dpi", type=int, default=110)
    p.add_argument("--range")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_pages)

    p = sub.add_parser("preview", help="compose and open the HTML in a browser")
    p.add_argument("dir")
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser("doctor", help="check Chrome, vendored files, fonts and tectonic")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("fonts", help="scan or list the font registry")
    p.add_argument("action", choices=["scan", "list"])
    p.add_argument("--filter")
    p.add_argument("--project")
    p.set_defaults(fn=cmd_fonts)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except PdfsmithError as e:
        _err(f"pdfsmith: {e}")
        return e.exit_code
    except (pymupdf.FileDataError, pikepdf.PdfError, OSError) as e:
        # A file the user named cannot be read or written: report it like any other input problem.
        _err(f"pdfsmith: cannot read or write a file: {e}")
        return InputError.exit_code


if __name__ == "__main__":
    sys.exit(main())
