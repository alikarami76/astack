"""Team fonts in flynn desk: the fonts flynn's designs and documents use, uploaded to every team.

  fonts.py sync [--dry-run]     upload what is missing to every team
  fonts.py list                 what each team has

Families:
  Vazirmatn   the editorial theme (release notes, newsletters, decks, reports). Its source is one variable
              font, which the canvas cannot take, so each weight is cut out as a static TTF (fontTools instancer).

Uploads go through Penpot's chunked upload (create-upload-session, upload-chunk) and create-font-variant, as the owner
account (the owner of the teams). Already-present family + weight pairs are skipped, and a family keeps its font id, so
running it again is safe. push.py and library.py find these by family name (penpot.fonts.findByName).
"""
from __future__ import annotations

import io
import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLYNN = HERE.parents[1]
sys.path.insert(0, str(HERE))
CACHE = Path.home() / ".flynn" / "fonts"

VAZIR_VF = FLYNN / "adapters/html/themes/editorial/fonts/vazirmatn-variable.woff2"
VAZIR_WEIGHTS = [100, 200, 300, 400, 500, 600, 700, 800, 900]


def _vazir(weight: int) -> bytes:
    """One static weight of the Vazirmatn variable font, as TTF (cached)."""
    out = CACHE / f"Vazirmatn-{weight}.ttf"
    if out.exists():
        return out.read_bytes()
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    f = TTFont(str(VAZIR_VF))
    axes = {a.axisTag: a for a in f["fvar"].axes}
    loc = {"wght": max(axes["wght"].minValue, min(axes["wght"].maxValue, weight))}
    for tag, a in axes.items():
        if tag != "wght":
            loc[tag] = a.defaultValue
    st = instancer.instantiateVariableFont(f, loc, updateFontNames=False)
    st.flavor = None
    buf = io.BytesIO()
    st.save(buf)
    CACHE.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return buf.getvalue()


FAMILIES = {"Vazirmatn": (VAZIR_WEIGHTS, _vazir)}


def teams() -> dict[str, str]:
    """Every team the owner account belongs to (designs and letters both live in teams)."""
    import builder
    return {t["name"]: t["id"] for t in builder.owner()("get-teams")}


def upload(rpc, team: str, family: str, font_id: str, weight: int, data: bytes) -> None:
    sid = rpc("create-upload-session", {"totalChunks": 1})["sessionId"]
    r = rpc.s.post(rpc_url() + "upload-chunk", data={"session-id": sid, "index": "0"},
                   files={"content": (f"{family}-{weight}.ttf", data, "font/ttf")})
    if r.status_code >= 400:
        raise RuntimeError(f"upload-chunk failed ({r.status_code}): {r.text[:200]}")
    rpc("create-font-variant", {"teamId": team, "fontId": font_id, "fontFamily": family, "fontWeight": weight,
                                "fontStyle": "normal", "uploads": {"font/ttf": sid}})


def rpc_url() -> str:
    import builder
    return builder.RPC


def present(rpc, team: str) -> dict[str, dict]:
    """{family: {"id": font id, "weights": {..}}} for a team."""
    out: dict[str, dict] = {}
    for v in rpc("get-font-variants", {"teamId": team}) or []:
        e = out.setdefault(v["fontFamily"], {"id": v["fontId"], "weights": set()})
        if v.get("fontStyle", "normal") == "normal":
            e["weights"].add(int(v["fontWeight"]))
    return out


def sync(dry_run: bool = False, log=print) -> dict:
    import builder
    rpc = builder.owner()
    done = {}
    for tname, tid in teams().items():
        have = present(rpc, tid)
        for family, (weights, get) in FAMILIES.items():
            fid = (have.get(family) or {}).get("id") or str(uuid.uuid4())
            missing = [w for w in weights if w not in (have.get(family) or {}).get("weights", set())]
            for w in missing:
                if not dry_run:
                    upload(rpc, tid, family, fid, w, get(w))
                log(f"{tname}: {family} {w}" + (" (would upload)" if dry_run else " uploaded"))
            done[f"{tname}/{family}"] = {"uploaded": missing, "had": sorted((have.get(family) or {}).get("weights", []))}
    return done


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="fonts.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--dry-run", action="store_true")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    if a.cmd == "sync":
        print(json.dumps(sync(a.dry_run), indent=1))
    else:
        import builder
        rpc = builder.owner()
        for tname, tid in teams().items():
            print(tname, {k: sorted(v["weights"]) for k, v in present(rpc, tid).items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
