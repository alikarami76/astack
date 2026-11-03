import hashlib

from pdfsmith import __version__
from pdfsmith.errors import EnvError, InputError, RenderError
from pdfsmith.paths import BUNDLED_FONTS, FONTS, PAGED_SHA256, VENDOR


def test_version():
    assert __version__ == "0.1.0"


def test_paged_vendored_and_pinned():
    data = (VENDOR / "paged.polyfill.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == PAGED_SHA256
    assert b"</script" not in data


def test_bundled_fonts_present_with_licence():
    for rel in BUNDLED_FONTS:
        path = FONTS / rel
        assert path.stat().st_size > 100_000, rel
        assert (path.parent / "OFL.txt").is_file(), rel


def test_exit_codes():
    assert (InputError.exit_code, RenderError.exit_code, EnvError.exit_code) == (1, 2, 4)
