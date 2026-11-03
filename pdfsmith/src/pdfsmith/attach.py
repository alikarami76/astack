"""Embedded files, each marked as an associated file (/AF) of the document, as PDF/A-3 requires."""

from __future__ import annotations

import datetime
import mimetypes
from pathlib import Path

import pikepdf

from .errors import InputError


def _pdf_date(timestamp: float) -> str:
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).strftime("D:%Y%m%d%H%M%SZ")


def add_attachments(pdf: pikepdf.Pdf, paths: list[str]) -> None:
    specs = []
    for path in map(Path, paths):
        if path.name in pdf.attachments:
            raise InputError(f"doc.yaml: features.attachments: two files are named '{path.name}'; rename one")
        date = _pdf_date(path.stat().st_mtime)
        spec = pikepdf.AttachedFileSpec(
            pdf,
            path.read_bytes(),
            description=path.name,
            filename=path.name,
            mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            creation_date=date,
            mod_date=date,
            relationship=pikepdf.Name.Data,
        )
        pdf.attachments[path.name] = spec
        specs.append(spec.obj)
    if specs:
        pdf.Root.AF = pikepdf.Array(specs)


def verify_attachments(pdf: pikepdf.Pdf, info: dict, geometry: dict | None) -> list[str] | None:
    wanted = [Path(p).name for p in info["features"].get("attachments", [])]
    if not wanted:
        return None
    listed = {str(spec.get("/UF", spec.get("/F", ""))) for spec in pdf.Root.get("/AF", [])}
    items = []
    for name in wanted:
        if name not in pdf.attachments:
            items.append(f"attachment '{name}' is missing")
        elif name not in listed:
            items.append(f"attachment '{name}' is not listed in the document's /AF array")
        elif pdf.attachments[name].obj.get("/AFRelationship") is None:
            items.append(f"attachment '{name}' has no /AFRelationship")
    return items
