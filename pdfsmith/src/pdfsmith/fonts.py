"""Font registry (cached scan), role resolution, @font-face CSS and glyph coverage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont

from .errors import InputError
from .paths import FONTS

FONT_EXT = {".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2"}
COLLECTION_EXT = {".ttc", ".otc"}
SYSTEM_DIRS = [
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library" / "Fonts",
    Path("/usr/share/fonts"),
    Path.home() / ".local" / "share" / "fonts",
]
PRIORITY = {"project": 0, "bundled": 1, "system": 2}
INSTANCE_WEIGHTS = range(100, 1000, 100)
INSTANCE_RECIPE = 1  # bump when _instantiate changes, so cached instances are rebuilt
FORMATS = {".ttf": "truetype", ".otf": "opentype", ".woff": "woff", ".woff2": "woff2"}


@dataclass
class Face:
    path: str
    index: int
    family: str
    style: str
    weight: int
    italic: bool
    variable: bool
    weight_range: tuple
    psname: str
    source: str


@dataclass
class Role:
    role: str
    families: list
    faces: dict
    missing: list


def _cache_file() -> Path:
    base = os.environ.get("PDFSMITH_CACHE") or str(Path.home() / ".cache" / "pdfsmith")
    return Path(base) / "fonts.json"


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s or "").lower()


def _name(font, ids) -> str:
    table = font["name"]
    for i in ids:
        value = table.getDebugName(i)
        if value:
            return value
    return ""


def _faces_in(path: Path, source: str) -> list[Face]:
    try:
        if path.suffix.lower() in COLLECTION_EXT:
            members = TTCollection(str(path), lazy=True).fonts
        else:
            members = [TTFont(str(path), lazy=True)]
    except Exception:
        return []
    faces = []
    for index, font in enumerate(members):
        try:
            os2 = font["OS/2"] if "OS/2" in font else None
            weight = int(os2.usWeightClass) if os2 else 400
            style = _name(font, (17, 2))
            italic = bool(os2.fsSelection & 1) if os2 else "italic" in style.lower()
            variable = "fvar" in font
            weight_range = (weight, weight)
            if variable:
                for axis in font["fvar"].axes:
                    if axis.axisTag == "wght":
                        weight_range = (int(axis.minValue), int(axis.maxValue))
            faces.append(Face(str(path), index, _name(font, (16, 1)), style, weight, italic, variable, weight_range, _name(font, (6,)), source))
        except Exception:
            continue
    return faces


def _dirs(project_dir):
    dirs = [(FONTS, "bundled")] + [(d, "system") for d in SYSTEM_DIRS]
    if project_dir:
        dirs.insert(0, (Path(project_dir) / "fonts", "project"))
    return dirs


def scan(project_dir: Path | None = None, refresh: bool = False) -> list[Face]:
    cache_file = _cache_file()
    cache: dict = {}
    if not refresh and cache_file.is_file():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    faces: list[Face] = []
    changed = False
    for directory, source in _dirs(project_dir):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() not in FONT_EXT or not path.is_file():
                continue
            key = f"{source}:{path}"
            mtime = path.stat().st_mtime
            entry = cache.get(key)
            if not entry or entry["mtime"] != mtime:
                entry = {"mtime": mtime, "faces": [asdict(f) for f in _faces_in(path, source)]}
                cache[key] = entry
                changed = True
            faces += [Face(**{**f, "weight_range": tuple(f["weight_range"])}) for f in entry["faces"]]
    if changed:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    return faces


def find_family(faces: list[Face], family: str) -> list[Face]:
    wanted = _norm(family)
    hits = [f for f in faces if _norm(f.family) == wanted]
    if not hits:
        return []
    best = min(PRIORITY[f.source] for f in hits)
    return [f for f in hits if PRIORITY[f.source] == best]


def resolve_roles(theme_fonts: dict, overrides: dict, faces: list[Face]) -> dict[str, Role]:
    unknown = [r for r in overrides if r not in theme_fonts]
    if unknown:
        raise InputError(f"doc.yaml: fonts: unknown role(s) {', '.join(unknown)}. Roles: {', '.join(theme_fonts)}")
    roles = {}
    for role, chain in theme_fonts.items():
        chain = list(chain)
        override = overrides.get(role)
        if isinstance(override, str):
            chain = [override] + [c for c in chain if _norm(c) != _norm(override)]
        elif isinstance(override, list):
            chain = list(override)
        families, found, missing = [], {}, []
        for family in chain:
            hits = find_family(faces, family)
            if hits:
                families.append(family)
                found[family] = hits
            else:
                missing.append(family)
        if not families:
            raise InputError(f"font role '{role}': none of {chain} is installed or bundled. Run `pdfsmith fonts list` to see what is available.")
        roles[role] = Role(role, families, found, missing)
    return roles


def _instantiate(src: Path, index: int, weights: list[tuple[int, Path]], out_dir: Path) -> None:
    from fontTools.varLib import instancer

    number = index if src.suffix.lower() in COLLECTION_EXT else -1
    weight = None
    try:
        font = TTFont(str(src), fontNumber=number)
        axes = {a.axisTag: a.defaultValue for a in font["fvar"].axes}
        rename = "STAT" in font
        out_dir.mkdir(parents=True, exist_ok=True)
        for weight, out in weights:
            location = dict(axes, wght=weight) if "wght" in axes else dict(axes)
            try:
                static = instancer.instantiateVariableFont(font, location, updateFontNames=rename)
            except ValueError as e:
                # Renaming needs a STAT axis value for this exact weight; without one keep the original names.
                if not rename or "Cannot find Axis Values" not in str(e):
                    raise
                static = instancer.instantiateVariableFont(font, location)
            static.flavor = None
            static["OS/2"].usWeightClass = weight
            tmp = out.with_name(out.name + f".{os.getpid()}.tmp")
            static.save(str(tmp))
            tmp.replace(out)
    except Exception as e:
        at = f" at weight {weight}" if weight is not None else ""
        raise InputError(f"font {src}: cannot make a static instance{at} ({type(e).__name__}: {e})") from e


def static_instances(face: Face) -> list[tuple[int, Path]]:
    """Static (non-variable) copies of a variable face, one per hundred weight in its range, cached.

    Chrome writes variable fonts into the PDF as Type 3 fonts; a static instance embeds as TrueType.
    Other axes (such as opsz) are pinned to their defaults. A face without a wght axis gets one instance.
    """
    src = Path(face.path)
    lo, hi = face.weight_range
    weights = [w for w in INSTANCE_WEIGHTS if lo <= w <= hi and lo < hi] or [face.weight]
    stat = src.stat()
    key = f"{INSTANCE_RECIPE}:{src.resolve()}:{face.index}:{stat.st_mtime}:{stat.st_size}"
    tag = hashlib.sha1(key.encode()).hexdigest()[:16]
    out_dir = _cache_file().parent / "instances"
    paths = [(w, out_dir / f"{tag}-{w}.ttf") for w in weights]
    todo = [(w, p) for w, p in paths if not p.is_file()]
    if todo:
        _instantiate(src, face.index, todo, out_dir)
    return paths


def font_face_css(roles: dict[str, Role]) -> str:
    seen, rules = set(), []

    def rule(family: str, path: Path, fmt: str, weight: str, italic: bool) -> None:
        rules.append(
            "@font-face {\n"
            f'  font-family: "{family}";\n'
            f'  src: url("{path.resolve().as_uri()}") format("{fmt}");\n'
            f"  font-weight: {weight};\n"
            f"  font-style: {'italic' if italic else 'normal'};\n"
            "}"
        )

    for role in roles.values():
        for family in role.families:
            for face in role.faces[family]:
                ext = Path(face.path).suffix.lower()
                key = (face.path, face.index)
                if face.source == "system" or ext not in FORMATS or key in seen:
                    continue
                seen.add(key)
                if face.variable:
                    for weight, path in static_instances(face):
                        rule(family, path, "truetype", str(weight), face.italic)
                else:
                    rule(family, Path(face.path), FORMATS[ext], str(face.weight), face.italic)
    return "\n".join(rules) + ("\n" if rules else "")


def font_stack(role: Role, generic: str) -> str:
    return ", ".join(f'"{family}"' for family in role.families) + f", {generic}"


@lru_cache(maxsize=256)
def _cmap(path: str, index: int) -> frozenset:
    try:
        number = index if Path(path).suffix.lower() in COLLECTION_EXT else -1
        font = TTFont(path, fontNumber=number, lazy=True)
        return frozenset(font.getBestCmap() or {})
    except Exception:
        return frozenset()


def missing_glyphs(chars: set[str], role: Role) -> set[str]:
    covered: set[int] = set()
    for family in role.families:
        face = role.faces[family][0]
        covered |= _cmap(face.path, face.index)
    return {c for c in chars if not c.isspace() and unicodedata.category(c) not in ("Cc", "Cf") and ord(c) not in covered}


def allowed_font_keys(roles: dict[str, Role]) -> set[str]:
    keys = set()
    for role in roles.values():
        for family, faces in role.faces.items():
            keys.add(_norm(family))
            for face in faces:
                keys.add(_norm(face.family))
                keys.add(_norm(re.split(r"[-,]", face.psname)[0]))
                if face.variable and face.source != "system":
                    for _, path in static_instances(face):
                        with TTFont(str(path), lazy=True) as static:
                            keys.add(_norm(re.split(r"[-,]", _name(static, (6,)))[0]))
    keys.discard("")
    return keys


def pdf_font_key(basefont: str) -> str:
    name = basefont.split("+", 1)[-1]
    return _norm(re.split(r"[-,]", name)[0])
