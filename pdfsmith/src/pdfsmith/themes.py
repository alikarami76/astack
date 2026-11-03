"""Theme loading (with extends) and CSS custom properties."""

from __future__ import annotations

import yaml

from .errors import InputError
from .paths import THEMES

REQUIRED = ("colors", "fonts", "type", "space", "radius")


def list_themes() -> list[str]:
    return sorted(p.stem for p in THEMES.glob("*.yaml"))


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        out[key] = _deep_merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def _load(name: str, seen: tuple[str, ...]) -> dict:
    if name in seen:
        raise InputError(f"theme extends cycle: {' -> '.join(seen + (name,))}")
    path = THEMES / f"{name}.yaml"
    if not path.is_file():
        raise InputError(f"unknown theme '{name}'. Available: {', '.join(list_themes())}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    parent = data.pop("extends", None)
    if parent:
        data = _deep_merge(_load(parent, seen + (name,)), data)
    return data


def load_theme(name: str) -> dict:
    theme = _load(name, ())
    missing = [k for k in REQUIRED if k not in theme]
    if missing:
        raise InputError(f"theme '{name}' is missing {', '.join(missing)}")
    theme["name"] = name
    return theme


def theme_css(theme: dict, font_stacks: dict[str, str]) -> str:
    t = theme["type"]
    lines = [":root {"]
    lines += [f"  --color-{key}: {value};" for key, value in theme["colors"].items()]
    lines += [f"  --font-{role}: {stack};" for role, stack in font_stacks.items()]
    lines += [
        f"  --text-base: {t['base']};",
        f"  --scale: {t['scale']};",
        f"  --leading: {t['leading']};",
        f"  --leading-tight: {t['leading_tight']};",
        f"  --space: {theme['space']['unit']};",
        f"  --radius: {theme['radius']};",
        "}",
    ]
    return "\n".join(lines) + "\n"
