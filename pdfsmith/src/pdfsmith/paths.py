from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
THEMES = ROOT / "themes"
COMPONENTS = ROOT / "components"
FONTS = ROOT / "fonts"
VENDOR = ROOT / "vendor"

PAGED_SHA256 = "f59f361802416c770d549a647958649af2cf6601999924bc00e4f507dad5269f"
BUNDLED_FONTS = (
    "Inter/Inter[opsz,wght].ttf",
    "Vazirmatn/Vazirmatn[wght].ttf",
    "JetBrainsMono/JetBrainsMono[wght].ttf",
)
