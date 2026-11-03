"""OCR and triage for screenshots, so agents read only the images that need a visual look. Stdlib + macOS Vision.

    ocr_shots.py text <image>...            print each image's text (cached by content hash)
    ocr_shots.py triage <image|dir>...      per image: new / changed / unchanged since the last triage, line count, text.
                                            State lives in <folder>/.ocr-index.json next to the images.

Use the text for every check about words, numbers, labels and overflow wording. Look at an image only when the
question is visual (layout, hierarchy, colour, spacing, right-to-left flow), and only if it is new or changed."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("FLYNN_OCR_CACHE") or Path.home() / ".flynn" / "cache" / "ocr")
SWIFT = HERE / "ocr_vision.swift"
EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}


def file_hash(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:20]


def _binary() -> Path:
    exe = CACHE.parent / f"ocr_vision-{file_hash(SWIFT)}"
    if not exe.exists():
        exe.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["swiftc", "-O", str(SWIFT), "-o", str(exe)], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("swiftc failed: " + r.stderr[-400:])
    return exe


def recognize(path) -> dict:
    """OCR one image. Cached by content hash, so an unchanged file is never read twice."""
    path = Path(path)
    key = file_hash(path)
    cached = CACHE / f"{key}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    r = subprocess.run([str(_binary()), str(path)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"OCR failed for {path}: {r.stderr.strip()}")
    data = json.loads(r.stdout)
    data["hash"] = key
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(data, ensure_ascii=False))
    return data


def text_of(data: dict) -> str:
    return "\n".join(l["text"] for l in sorted(data["lines"], key=lambda l: (round(l["y"], 2), l["x"])))


def _images(args):
    for a in args:
        p = Path(a)
        if p.is_dir():
            yield from sorted(f for f in p.iterdir() if f.suffix.lower() in EXTS)
        else:
            yield p


def triage(args) -> list[dict]:
    out, indexes = [], {}
    for p in _images(args):
        idx_path = p.parent / ".ocr-index.json"
        idx = indexes.setdefault(idx_path, json.loads(idx_path.read_text()) if idx_path.exists() else {})
        data = recognize(p)
        old = idx.get(p.name)
        status = "new" if old is None else "unchanged" if old == data["hash"] else "changed"
        idx[p.name] = data["hash"]
        out.append({"image": str(p), "status": status, "lines": len(data["lines"]), "text": text_of(data)})
    for idx_path, idx in indexes.items():
        idx_path.write_text(json.dumps(idx, indent=1))
    return out


def main(argv) -> int:
    if len(argv) < 3 or argv[1] not in ("text", "triage"):
        print(__doc__)
        return 2
    if argv[1] == "text":
        for p in _images(argv[2:]):
            print(f"=== {p}\n{text_of(recognize(p))}")
    else:
        for r in triage(argv[2:]):
            print(f"=== {r['image']} [{r['status']}] {r['lines']} lines" + ("" if r["status"] == "unchanged" else f"\n{r['text']}"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
