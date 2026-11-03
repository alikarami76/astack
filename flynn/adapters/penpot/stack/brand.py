"""Rebrand the Penpot frontend as flynn, without rebuilding it.

Copies the built files that carry the name or the logo out of the running frontend container, rewrites them,
and writes the results to flynn-overlay/brand/ plus docker-compose.brand.yaml, which mounts each rewritten file
over the original. Run it again after every Penpot upgrade; it always starts from the image's own files.

What changes (only what people see):
- every translation file: the product name in UI text;
- the app code (main, shared, workspace, auth bundles): the name in visible string literals only. Code names
  (PenpotContext, window.PenpotPerf), web links (penpot.app, github.com/penpot, youtube.com/c/Penpot) and the
  MCP connector plugin's name and description are left alone, because the connector may be matched by them;
- the logo symbols in both icon sprites: the flynn disc and wordmark;
- index.html: page title, description and favicon (the overlay's own index.html, which also loads inject.js).

Usage:
  python3 brand.py            rewrite and print what changed
  python3 brand.py --check    only report how many visible "Penpot" strings each file still has
Then recreate the frontend:  docker compose -p flynn-penpot -f docker-compose.yaml -f docker-compose.flynn.yaml \
                               -f docker-compose.brand.yaml up -d --force-recreate penpot-frontend
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "flynn-overlay" / "brand"
CONTAINER = "flynn-penpot-penpot-frontend-1"
APP = "/var/www/app"
DOCKER = "/Applications/Docker.app/Contents/Resources/bin/docker"
NAME = "flynn desk"          # the app (the Penpot fork); the agent inside it is flynn

CODE_FILES = ["js/main.js", "js/shared.js", "js/main-workspace.js", "js/main-auth.js",
              "js/main-dashboard.js", "js/main-settings.js", "js/main-viewer.js"]
SPRITES = ["images/sprites/symbol/icons.svg", "images/sprites/assets.svg"]

# The flynn mark: Sam Flynn's identity disc as it appears in the film, seen face-on in the dark. A dark body, a
# thick neon outer ring broken once at the lower right, a thinner ring around the hub and four points of light.
# Each light is a cyan tube with a soft glow; nothing white. Colours are fixed (the disc is its own light source); only the wordmark text follows the theme.
import math

C = 250


def _arc(r: float, a0: float, a1: float, cx: float = C, cy: float = C) -> str:
    """An arc from angle a0 to a1 (degrees, clockwise, 0 = 3 o'clock)."""
    p0 = (cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0)))
    p1 = (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)))
    large = 1 if (a1 - a0) % 360 > 180 else 0
    return f"M{p0[0]:.2f} {p0[1]:.2f}A{r} {r} 0 {large} 1 {p1[0]:.2f} {p1[1]:.2f}"


def _tube(d: str, width: float) -> str:
    """One light: a soft cyan glow under a cyan tube, along path d (no white core)."""
    return (f'<path d="{d}" stroke="#00c8ff" stroke-width="{width * 1.8:.1f}" opacity=".5" filter="url(#fdk-glow)"/>'
            f'<path d="{d}" stroke="#14dcff" stroke-width="{width}"/>')


_OUTER = _arc(196, 52, 52 + 360 - 13)                 # broken at about half past four
_HUB = _arc(104, 0, 359.9)
_DOTS = "".join(f'<circle cx="{C + 148 * math.cos(math.radians(a)):.1f}" cy="{C + 148 * math.sin(math.radians(a)):.1f}" r="10"/>'
                for a in (-130, -50, 50, 130))

DISC_BODY = (
    "<defs>"
    '<radialGradient id="fdk-body" cx=".5" cy=".42" r=".6"><stop offset="0" stop-color="#0c1820"/>'
    '<stop offset=".75" stop-color="#050a0e"/><stop offset="1" stop-color="#020406"/></radialGradient>'
    '<filter id="fdk-glow" x="-25%" y="-25%" width="150%" height="150%"><feGaussianBlur stdDeviation="10"/></filter>'
    '<filter id="fdk-soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4"/></filter>'
    "</defs>"
    f'<circle cx="{C}" cy="{C}" r="222" fill="url(#fdk-body)"/>'
    '<g fill="none" stroke-linecap="round">'
    + _tube(_OUTER, 30)
    + _tube(_HUB, 14)
    + "</g>"
    + f'<g fill="#14dcff"><g filter="url(#fdk-soft)" opacity=".8">{_DOTS}</g>{_DOTS}</g>'
)


def disc(cx=250, cy=250, s=1.0):
    return f'<g transform="translate({cx - 250 * s:.2f} {cy - 250 * s:.2f}) scale({s})">{DISC_BODY}</g>'


MARK = disc()
# The wordmark slot shows the disc only; the name next to it is real text (declutter.css, `.flynn-name`), never
# letters drawn inside the picture (2026-10-06: the user found the baked-in text ugly).
WORDMARK = MARK
LOADER = disc(130.5, 172.5, 0.5)

SYMBOLS = {  # symbol id suffix -> (viewBox, content); the prefix (icon-/asset-) is kept
    "penpot-logo-icon": ("0 0 500 500", MARK),
    "penpot-logo": ("0 0 500 500", WORDMARK),
    "penpot-logo-icon-loader": ("0 0 261 345", LOADER),
}

FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 500"><rect width="500" height="500" rx="110" '
           'fill="#0b0f14"/><g color="#8fe3ff" transform="translate(40 40) scale(.84)">' + MARK + '</g></svg>')


# Always read Penpot's own files from the image: the running container already has the branded copies
# mounted over them.
_IMAGE = None


def image() -> str:
    global _IMAGE
    if _IMAGE is None:
        _IMAGE = subprocess.run([DOCKER, "inspect", "-f", "{{.Config.Image}}", CONTAINER], check=True,
                                capture_output=True, text=True).stdout.strip()
    return _IMAGE


def _in_image(cmd: str) -> bytes:
    return subprocess.run([DOCKER, "run", "--rm", "--entrypoint", "sh", image(), "-c", cmd], check=True,
                          capture_output=True).stdout


SRC: Path | None = None        # --src: read Penpot's built files from a folder (the image build) instead of docker


def docker_read(path: str) -> str:
    if SRC:
        return (SRC / path).read_text(encoding="utf-8")
    return _in_image(f"cat {APP}/{path}").decode("utf-8")


def docker_ls(pattern: str) -> list[str]:
    if SRC:
        return sorted(str(p.relative_to(SRC)) for p in SRC.glob(pattern))
    out = _in_image(f"cd {APP} && ls {pattern}").decode()
    return [line.strip() for line in out.splitlines() if line.strip()]


# A visible "Penpot": a whole word, not part of a code name (PenpotContext), not inside a link or path (/Penpot,
# penpot.app), and not the MCP connector's own name.
# The uppercase form is never touched: the code uses "PENPOT..." as internal keys (text styles such as
# PENPOT_SELECTION), and only error prefixes show it.
VISIBLE = re.compile(r"(?<![\w/.$-])Penpot(?![\w$]|\.app|\.dev|\.io| MCP Plugin|\s+MCP server\")")


# Persian translations write the name as پنپات (sometimes with a zero-width non-joiner).
VISIBLE_FA = re.compile("\u067e\u0646\u200c?\u067e\u0627\u062a")


def rename(text: str) -> tuple[str, int]:
    text, a = VISIBLE.subn(NAME, text)
    text, b = VISIBLE_FA.subn(NAME, text)
    return text, a + b


def rebrand_sprite(svg: str) -> tuple[str, int]:
    n = 0
    for suffix, (vb, body) in SYMBOLS.items():
        pat = re.compile(r'<symbol([^>]*)id="((?:icon|asset)-' + re.escape(suffix) + r')"([^>]*)>.*?</symbol>', re.S)

        def sub(m):
            attrs = re.sub(r'\s*viewBox="[^"]*"', "", m.group(1) + m.group(3))
            return f'<symbol{attrs} id="{m.group(2)}" viewBox="{vb}">{body}</symbol>'
        svg, k = pat.subn(sub, svg)
        n += k
    return svg, n


def tidy(text: str) -> str:
    """Name-only titles: "flynn | Full-stack design" (Penpot's tagline) becomes just "flynn"."""
    return text.replace(f"{NAME} | Full-stack design", NAME)


def rebrand_index(html: str) -> str:
    # index.html embeds the default (English) translations, so the same rename applies to the whole file.
    html = tidy(rename(html)[0])
    html = rebrand_sprite(html)[0]          # the icon sprite is inlined in index.html as well
    html = re.sub(r"<title>[^<]*</title>", f"<title>{NAME}</title>", html)
    html = re.sub(r'(<meta[^>]*(?:name|property)="(?:og:)?(?:title|description|twitter:title|twitter:description)"[^>]*content=")[^"]*"',
                  lambda m: m.group(1) + (f'{NAME}, the AI-native design studio"'), html)
    html = re.sub(r'<link rel="icon"[^>]*>', '<link rel="icon" type="image/svg+xml" href="/flynn/favicon.svg" />', html)
    return html


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--src", help="Penpot's /var/www/app copied to a folder (image build: no docker needed)")
    ap.add_argument("--out", help="write the branded files here instead of flynn-overlay/brand/")
    ap.add_argument("--index", help="write the branded index.html here instead of the overlay's own copy")
    a = ap.parse_args()
    global SRC, OUT
    if a.src:
        SRC = Path(a.src)
    if a.out:
        OUT = Path(a.out)

    files = CODE_FILES + docker_ls("js/translation.*.js")
    if a.check:
        for f in files:
            print(f"{f}: {len(VISIBLE.findall(docker_read(f)))} visible")
        return 0

    mounts = []
    for f in files:
        text, n = rename(docker_read(f))
        text = tidy(text)
        if not n:
            continue
        dest = OUT / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        mounts.append(f)
        print(f"{f}: {n} renamed")
    for f in SPRITES:
        svg, n = rebrand_sprite(docker_read(f))
        dest = OUT / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(svg, encoding="utf-8")
        mounts.append(f)
        print(f"{f}: {n} logo symbols replaced")

    # index.html is already the overlay's own copy (it loads inject.js): rebrand it in place, keeping the file
    # (the container mounts this exact file, so it must be rewritten, not replaced).
    idx = Path(a.index) if a.index else HERE / "flynn-overlay" / "index.html"
    new = rebrand_index(docker_read("index.html"))
    if "/flynn/inject.js" not in new:
        new = new.replace("</body>", '  <script src="/flynn/inject.js" defer></script></body>', 1)
    if a.index:
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(new, encoding="utf-8")
    else:
        with open(idx, "r+", encoding="utf-8") as fh:      # the running frontend mounts this exact file
            fh.seek(0); fh.write(new); fh.truncate()
    (HERE / "flynn-overlay" / "www" / "favicon.svg").write_text(FAVICON, encoding="utf-8")
    print("index.html: title, description and favicon")

    lines = ["# Generated by brand.py: mounts the flynn-branded copies over Penpot's built files.",
             "services:", "  penpot-frontend:", "    volumes:"]
    lines += [f"      - ./flynn-overlay/brand/{f}:{APP}/{f}:ro" for f in mounts]
    (HERE / "docker-compose.brand.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"docker-compose.brand.yaml: {len(mounts)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
