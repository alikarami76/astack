"""LaTeX route: compile a .tex file with tectonic (XeTeX) instead of printing HTML with Chrome."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pikepdf

from .errors import EnvError, RenderError

INSTALL_HINT = "Install it with `brew install tectonic` (https://tectonic-typesetting.github.io)."
SECTIONING = re.compile(r"^[^%\n]*\\(?:part|chapter|section|subsection|subsubsection)\*?\s*[\[{]", re.M)
# tectonic prints one `error: file.tex:LINE: message` line per TeX error.
ERROR_LINE = re.compile(r"^error: (?P<where>[^\s:]+:\d+): (?P<msg>.+)$", re.M)
LOG_ERROR = re.compile(r"^! (?P<msg>.+)\n(?:.*\n)*?l\.(?P<line>\d+) ", re.M)
LOG_WARNING = re.compile(r"(?:LaTeX|Package \w+) Warning: (?P<msg>(?:Citation|Reference|There were undefined|Label)[^\n]*)", re.I)
MAX_ERRORS = 5
OVERFULL = re.compile(
    r"^Overfull \\(?P<kind>[hv])box \((?P<amount>[\d.]+)pt too (?:wide|high)\)(?P<rest>[^\n]*)", re.M
)
OVERFULL_LINES = re.compile(r"at lines? (?P<a>\d+)(?:--(?P<b>\d+))?")
LOG_FILE_TOKEN = re.compile(r"^[\w./+-]+$")


def tectonic_path() -> str | None:
    return shutil.which("tectonic")


def tectonic_version() -> str:
    exe = tectonic_path()
    if not exe:
        raise EnvError(f"tectonic not found on PATH. {INSTALL_HINT}")
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip() or "unknown version"
    except (OSError, subprocess.SubprocessError) as e:
        raise EnvError(f"tectonic at {exe} does not run: {e}. {INSTALL_HINT}") from e


def _sources(build_dir: Path) -> str:
    texts = []
    for path in sorted(build_dir.rglob("*.tex")):
        if not {"build", "out"} & set(path.relative_to(build_dir).parts[:1]):
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(texts)


def latex_info(doc: dict, build_dir: Path) -> dict:
    """The compose.json for the latex route, with the keys the post and check stages read."""
    return {
        "title": doc["meta"]["title"] or doc["name"],
        "title_from_doc": bool(doc["meta"]["title"]),
        "author": doc["meta"]["author"],
        "subject": doc["meta"]["subject"],
        "keywords": doc["meta"]["keywords"] or [],
        "lang": doc["lang"],
        "pdf_lang": doc["pdf_lang"],
        "rtl": False,
        "digits": doc["digits"],
        "template": doc["template"],
        "theme": None,
        "headings": [],
        "empty_sections": [],
        "missing_glyphs": {},
        "missing_fonts": {},
        "allowed_font_keys": [],
        "mono_font_keys": [],
        "fa_words": [],
        "latin_numbers": [],
        "pages_expect": doc["checks"]["pages"],
        "engine": "tectonic",
        "latex_sections": bool(SECTIONING.search(_sources(build_dir))),
        "latex_warnings": [],
        "latex_overfull": [],
        "overflow_pt": doc["checks"]["overflow_pt"],
        "features": {
            "attachments": [str((build_dir / n).resolve()) for n in doc["features"]["attachments"]],
            "forms": doc["features"]["forms"],
            "encrypt": doc["features"]["encrypt"],
            "merge": {"before": [], "after": []},
            "pdfa": doc["features"]["pdfa"],
        },
    }


def _with_ext(where: str) -> str:
    """tectonic names \\input files without their extension ("part:2"); show the file as it is on disk."""
    name, _, line = where.rpartition(":")
    return where if Path(name).suffix else f"{name}.tex:{line}"


def _error_summary(stderr: str, log: Path) -> str:
    """The first few TeX errors with file:line, not the whole log."""
    found = [f"{_with_ext(m['where'])}: {m['msg'].strip()}" for m in ERROR_LINE.finditer(stderr) if "XeTeX" not in m["msg"]]
    if not found and log.is_file():
        text = log.read_text(encoding="utf-8", errors="replace")
        found = [f"{log.stem}.tex:{m['line']}: {m['msg'].strip()}" for m in LOG_ERROR.finditer(text)]
    if not found:
        tail = [line for line in stderr.splitlines() if line.strip() and not line.startswith("note:")][-5:]
        found = tail or ["tectonic failed without a message"]
    lines = list(dict.fromkeys(found))
    more = f"\n  (+{len(lines) - MAX_ERRORS} more; see {log})" if len(lines) > MAX_ERRORS else f"\n  (full log: {log})"
    return "\n".join(f"  {line}" for line in lines[:MAX_ERRORS]) + more


def parse_overfull(text: str, main_tex: str) -> list[dict]:
    """Every `Overfull \\hbox/\\vbox` in a TeX log as {kind, pt, file, lines}.

    The file comes from the log's open-file parentheses: `(name` opens a file, `)` closes the innermost open
    thing. Anything in parentheses that is not a plain path is pushed as None so its `)` pops the right entry.
    The message and its box dump (up to the next blank line) are not scanned, since the dump holds document text.
    """
    found, stack, in_message = [], [], False
    for line in text.splitlines():
        if in_message:
            if line.strip():
                continue
            in_message = False
        m = OVERFULL.match(line)
        if m:
            in_message = True
            where = OVERFULL_LINES.search(m["rest"])
            name = next((n for n in reversed(stack) if n), None)
            file = _with_ext(f"{name.removeprefix('./')}:0").rpartition(":")[0] if name else main_tex
            lines = ""
            if where:
                lines = where["a"] if not where["b"] or where["b"] == where["a"] else f"{where['a']}-{where['b']}"
            found.append({"kind": m["kind"], "pt": float(m["amount"]), "file": file, "lines": lines})
            continue
        for tok in re.finditer(r"\(([^\s()]*)|\)", line):
            if tok.group(0) == ")":
                if stack:
                    stack.pop()
            else:
                stack.append(tok.group(1) if LOG_FILE_TOKEN.match(tok.group(1)) else None)
    return found


def compile_tex(build_dir: Path, tex: str, work: Path, timeout_s: float, info: dict) -> Path:
    """Compile build_dir/tex into work/raw.pdf. Runs in build_dir so \\input and \\includegraphics paths resolve.

    Fonts: put them in <dir>/fonts/ and load them with fontspec's Path=fonts/ (tectonic on macOS does not look up
    fonts by name in a project folder).
    """
    exe = tectonic_path()
    if not exe:
        raise EnvError(f"tectonic not found on PATH. {INSTALL_HINT}")
    build_dir = Path(build_dir)
    outdir = work.resolve()
    outdir.mkdir(exist_ok=True)
    stem = Path(tex).stem
    for stale in (outdir / f"{stem}.pdf", work / "raw.pdf"):
        stale.unlink(missing_ok=True)
    cmd = [exe, "-X", "compile", tex, "--outdir", str(outdir), "--keep-logs", "--keep-intermediates"]
    try:
        run = subprocess.run(cmd, cwd=build_dir, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise RenderError(f"tectonic did not finish within {timeout_s:g}s (the first run downloads packages; raise `timeout` in doc.yaml)") from e
    except OSError as e:
        raise EnvError(f"tectonic at {exe} does not run: {e}. {INSTALL_HINT}") from e
    log = outdir / f"{stem}.log"
    pdf = outdir / f"{stem}.pdf"
    if run.returncode != 0 or not pdf.is_file():
        raise RenderError(f"LaTeX failed to compile {tex}:\n{_error_summary(run.stderr, log)}")
    shutil.copyfile(pdf, work / "raw.pdf")
    if log.is_file():
        text = log.read_text(encoding="utf-8", errors="replace")
        info["latex_warnings"] = list(dict.fromkeys(m["msg"].strip() for m in LOG_WARNING.finditer(text)))
        info["latex_overfull"] = parse_overfull(text, tex)
    if not info["title_from_doc"]:
        with pikepdf.open(work / "raw.pdf") as raw:
            title = str(raw.docinfo.get("/Title", "")).strip()
        if title:
            info["title"] = title
    (work / "compose.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    (work / "geometry.json").write_text(json.dumps({"headings": [], "overflow": [], "blocked": [], "failed": [], "page_errors": []}), encoding="utf-8")
    return work / "raw.pdf"
