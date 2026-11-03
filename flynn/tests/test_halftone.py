import importlib.util
from pathlib import Path

from PIL import Image

_p = Path(__file__).resolve().parent.parent / "adapters" / "html" / "halftone.py"
_spec = importlib.util.spec_from_file_location("html_halftone", _p)
halftone = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(halftone)
halftone_svg = halftone.halftone_svg


def _img(tmp_path, val, size=(32, 32)):
    p = tmp_path / "i.png"
    Image.new("L", size, val).save(p)
    return str(p)


def test_black_image_has_no_dots(tmp_path):
    assert "<circle" not in halftone_svg(_img(tmp_path, 0), cell=8)


def test_white_image_fills_grid(tmp_path):
    svg = halftone_svg(_img(tmp_path, 255), cell=8)
    assert svg.count("<circle") == 16 and 'r="4.0"' in svg


def test_color_and_viewbox(tmp_path):
    svg = halftone_svg(_img(tmp_path, 255, (40, 24)), cell=8, color="#4FD1FF")
    assert 'viewBox="0 0 40 24"' in svg and 'fill="#4FD1FF"' in svg


def test_crop(tmp_path):
    svg = halftone_svg(_img(tmp_path, 255, (64, 64)), cell=8, crop=(0, 0, 16, 16))
    assert svg.count("<circle") == 4
