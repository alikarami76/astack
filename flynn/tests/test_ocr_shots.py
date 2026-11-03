import json
import platform
import subprocess

import pytest

pytestmark = pytest.mark.skipif(platform.system() != "Darwin", reason="Apple Vision needs macOS")


@pytest.fixture
def shot(tmp_path):
    out = tmp_path / "shots"
    out.mkdir()
    p = out / "sec-01.png"
    # draw text with AppKit through a tiny swift script
    src = tmp_path / "draw.swift"
    src.write_text('''import AppKit
let img = NSImage(size: NSSize(width: 600, height: 160))
img.lockFocus()
NSColor.white.set(); NSRect(x: 0, y: 0, width: 600, height: 160).fill()
("Total orders 1234" as NSString).draw(at: NSPoint(x: 20, y: 60), withAttributes: [.font: NSFont.systemFont(ofSize: 40), .foregroundColor: NSColor.black])
img.unlockFocus()
let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
''')
    subprocess.run(["swift", str(src), str(p)], check=True, capture_output=True)
    return p


def test_ocr_reads_text_and_caches(shot, tmp_path, monkeypatch):
    import ocr_shots
    monkeypatch.setattr(ocr_shots, "CACHE", tmp_path / "cache" / "ocr")
    data = ocr_shots.recognize(shot)
    assert "1234" in ocr_shots.text_of(data)
    assert list((tmp_path / "cache" / "ocr").glob("*.json"))
    assert ocr_shots.recognize(shot)["hash"] == data["hash"]


def test_triage_new_unchanged_changed(shot, tmp_path, monkeypatch):
    import ocr_shots
    monkeypatch.setattr(ocr_shots, "CACHE", tmp_path / "cache" / "ocr")
    assert ocr_shots.triage([shot.parent])[0]["status"] == "new"
    assert ocr_shots.triage([shot.parent])[0]["status"] == "unchanged"
    shot.write_bytes(shot.read_bytes() + b"\0")
    assert ocr_shots.triage([shot.parent])[0]["status"] == "changed"
    assert json.loads((shot.parent / ".ocr-index.json").read_text())["sec-01.png"]
