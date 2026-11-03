"""Run the pipeline stages in order, or one stage on its own."""

from __future__ import annotations

import json
from pathlib import Path

from .check import run_checks
from .compose import compose
from .errors import InputError
from .latex import compile_tex, latex_info
from .post import postprocess
from .render import render
from .schema import LATEX, load_doc

STAGES = ("compose", "render", "post", "check")


def _load(path: Path, stage: str) -> dict:
    if not path.is_file():
        raise InputError(f"{path.name} not found; run the {stage} stage first")
    return json.loads(path.read_text(encoding="utf-8"))


def build(build_dir: Path, stage: str | None = None) -> tuple[Path, dict | None]:
    build_dir = Path(build_dir)
    doc = load_doc(build_dir)
    work = build_dir / "build"
    work.mkdir(exist_ok=True)
    pdf = build_dir / "out" / f"{doc['name']}.pdf"
    stages = STAGES if stage is None else (stage,)

    is_latex = doc["template"] == LATEX
    if "compose" in stages:
        if is_latex:
            # Nothing to compose: the .tex file is the source. Record what post and check need.
            (work / "compose.json").write_text(json.dumps(latex_info(doc, build_dir), ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            compose(build_dir)
    info = _load(work / "compose.json", "compose")
    if "render" in stages and is_latex:
        compile_tex(build_dir, doc["tex"], work, doc["timeout"], info)
    elif "render" in stages:
        render(
            work / "composed.html",
            work / "raw.pdf",
            work / "geometry.json",
            timeout_s=doc["timeout"],
            network=doc["network"],
            allow_overflow=doc["checks"]["allow_overflow"],
        )
    geometry = _load(work / "geometry.json", "render") if {"post", "check"} & set(stages) else None
    if "post" in stages:
        postprocess(work / "raw.pdf", pdf, info, geometry)
    report = None
    if "check" in stages:
        if not pdf.is_file():
            raise InputError(f"{pdf} not found; run the post stage first")
        report = run_checks(pdf, info, geometry)
        (work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return pdf, report
