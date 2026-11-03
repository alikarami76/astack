"""Post stage: outline fallback, metadata, /Lang, reading direction and Persian page labels."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pymupdf

from . import attach, fa


def _toc(headings: list[dict]) -> list[list]:
    toc, previous = [], 0
    for h in headings:
        level = max(1, min(h["level"], previous + 1))
        toc.append([level, h["text"], h["page"]])
        previous = level
    return toc


def postprocess(raw_pdf: Path, out_pdf: Path, info: dict, geometry: dict | None) -> None:
    raw_pdf, out_pdf = Path(raw_pdf), Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_pdf.with_name(out_pdf.stem + ".tmp.pdf")
    doc = pymupdf.open(raw_pdf)
    headings = [h for h in (geometry or {}).get("headings", []) if h["text"].strip()]
    if not doc.get_toc() and headings:
        doc.set_toc(_toc(headings))
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    try:
        with pikepdf.open(tmp) as pdf:
            # Values the engine wrote (LaTeX hyperref's pdfauthor and so on) stay unless doc.yaml sets its own.
            kept = {key: str(value) for key, value in pdf.docinfo.items() if key in ("/Author", "/Subject", "/Keywords")}
            with pdf.open_metadata(set_pikepdf_as_editor=False) as xmp:
                xmp["dc:title"] = info["title"]
                if info.get("author"):
                    xmp["dc:creator"] = [info["author"]]
            # docinfo is written after the XMP block: closing open_metadata() resyncs docinfo and drops keys.
            pdf.docinfo["/Title"] = info["title"]
            for key, value in (("/Author", info.get("author")), ("/Subject", info.get("subject")), ("/Keywords", ", ".join(info.get("keywords") or []))):
                value = value or kept.get(key)
                if value:
                    pdf.docinfo[key] = value
            pdf.docinfo["/Producer"] = "pdfsmith"
            pdf.Root.Lang = pikepdf.String(info["pdf_lang"])
            prefs = pdf.Root.get("/ViewerPreferences", pikepdf.Dictionary())
            prefs.DisplayDocTitle = True
            if info.get("rtl"):
                prefs.Direction = pikepdf.Name.R2L
            pdf.Root.ViewerPreferences = prefs
            if info.get("digits") in fa.FA_DIGIT_STYLES:
                nums = []
                for i in range(len(pdf.pages)):
                    nums += [i, pikepdf.Dictionary(P=pikepdf.String(fa.to_fa_digits(str(i + 1))))]
                pdf.Root.PageLabels = pikepdf.Dictionary(Nums=pikepdf.Array(nums))
            attach.add_attachments(pdf, (info.get("features") or {}).get("attachments", []))
            pdf.save(out_pdf)
    finally:
        tmp.unlink(missing_ok=True)
