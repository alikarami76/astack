#!/usr/bin/env python3
"""vecstore CLI. Examples:
  python3 lib/vecstore/cli.py status
  python3 lib/vecstore/cli.py index clu
  python3 lib/vecstore/cli.py search pp-kb "why does the buybox winner flip"
  python3 lib/vecstore/cli.py eval clu --check
  python3 lib/vecstore/cli.py eval flynn --matrix /tmp/flynn-matrix   # every upgrade step, needs build_eval()
  python3 lib/vecstore/cli.py dupes quorra --threshold 0.92
  python3 lib/vecstore/cli.py daemon start|stop|ping
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vecstore import client, config, evalkit, hybrid  # noqa: E402
from vecstore.errors import Unavailable  # noqa: E402
from vecstore.vecfile import VecFile  # noqa: E402


class StoreError(Exception):
    """Raised when a store configuration or loading fails."""
    pass


def registry(path=None) -> dict:
    p = Path(path or os.environ.get("VECSTORE_REGISTRY") or Path(__file__).with_name("stores.toml"))
    try:
        return tomllib.loads(p.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise StoreError(f"cannot read registry {p}: {e}") from e


def load_module(name: str, reg: dict | None = None):
    reg = reg if reg is not None else registry()
    if name not in reg:
        raise StoreError(f"unknown store {name!r}; registered: {', '.join(sorted(reg)) or 'none'}")
    if "adapter" not in reg[name]:
        raise StoreError(f"store {name!r} has no 'adapter' in the registry")
    path = Path(reg[name]["adapter"])
    path = path if path.is_absolute() else config.ASTACK / path
    if not path.exists():
        raise StoreError(f"adapter file not found: {path}")
    try:
        spec = importlib.util.spec_from_file_location(f"vec_adapter_{name.replace('-', '_')}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "make"):
            raise StoreError(f"cannot load adapter for {name!r}: adapter module has no make()")
        return mod
    except StoreError:
        raise
    except Exception as e:
        raise StoreError(f"cannot load adapter for {name!r}: {type(e).__name__}: {e}") from e


def load_adapter(name: str, reg: dict | None = None):
    mod = load_module(name, reg)
    try:
        return mod.make()
    except Exception as e:
        raise StoreError(f"cannot load adapter for {name!r}: {type(e).__name__}: {e}") from e


def _refresh(a) -> None:
    if hasattr(a, "refresh_index"):
        a.refresh_index()


def _search(a, query, k, etype, mode):
    if hasattr(a, "search"):
        return a.search(query, k=k, etype=etype, mode=mode)
    return hybrid.search(a, query, etype, k, mode)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vecstore")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    ix = sub.add_parser("index")
    ix.add_argument("store")
    ix.add_argument("--rebuild", action="store_true")
    ix.add_argument("--quiet", action="store_true")
    s = sub.add_parser("search")
    s.add_argument("store")
    s.add_argument("query")
    s.add_argument("--k", type=int, default=8)
    s.add_argument("--type")
    s.add_argument("--mode", choices=["auto", "lexical", "hybrid"], default="auto")
    s.add_argument("--json", action="store_true")
    ev = sub.add_parser("eval")
    ev.add_argument("store")
    ev.add_argument("--save-baseline", action="store_true")
    ev.add_argument("--check", action="store_true")
    ev.add_argument("--rerank", action="store_true", help="also score hybrid + rerank")
    ev.add_argument("--matrix", metavar="WORKDIR", help="build a private index per upgrade step under WORKDIR and "
                    "score each (the adapter module must define build_eval(workdir, norm))")
    ev.add_argument("--matrix-out", help="also write the matrix JSON here")
    du = sub.add_parser("dupes")
    du.add_argument("store")
    du.add_argument("--threshold", type=float, default=0.92)
    du.add_argument("--type")
    du.add_argument("--json", action="store_true")
    d = sub.add_parser("daemon")
    d.add_argument("action", choices=["start", "stop", "ping"])
    a = p.parse_args(argv)
    try:
        return _run(a)
    except (StoreError, Unavailable) as e:
        print(f"vecstore {a.cmd}: {e}", file=sys.stderr)
        return 1


def _run(a) -> int:
    if a.cmd == "daemon":
        if a.action == "start":
            client.ensure_daemon()
        elif a.action == "stop":
            client.stop()
            return 0
        info = client.ping()
        print(json.dumps(info) if info else "daemon not running")
        return 0 if info else 1
    if a.cmd == "status":
        info = client.ping()
        print(f"daemon: {'up, ' + info['model'] + ', pid ' + str(info['pid']) if info else 'not running'}")
        try:
            reg = registry()
        except StoreError as e:
            print(f"registry: error {e}")
            return 1
        for name in sorted(reg):
            try:
                ad = load_adapter(name, reg)
                _refresh(ad)
                vf = VecFile(ad.vec_path)
                todo, stale, total = vf.plan(ad.entries())
                print(f"{name}: vectors={vf.count()} entries={total} stale={len(todo) + len(stale)} "
                      f"last_sync={vf.meta('last_sync') or '-'} auto={hybrid.resolve_mode(ad)}")
            except StoreError as e:
                print(f"{name}: error {e}")
            except Exception as e:  # noqa: BLE001 - one broken store must not hide the others
                print(f"{name}: error {type(e).__name__}: {e}")
        return 0
    if a.cmd == "eval" and a.matrix:
        mod = load_module(a.store)
        if not hasattr(mod, "build_eval"):
            raise StoreError(f"store {a.store!r} has no build_eval(workdir, norm) for --matrix")
        work = Path(a.matrix)
        work.mkdir(parents=True, exist_ok=True)
        probe = mod.make()
        items = evalkit.load_set(Path(probe.eval_dir) / "recall_set.jsonl")
        client.ensure_daemon(wait=180)
        client.rerank("warm up", ["warm up the reranker"], 600.0)  # load it before the timed queries
        res = evalkit.matrix(lambda norm: mod.build_eval(work, norm), items)
        if a.matrix_out:
            Path(a.matrix_out).write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
        tags = tuple(sorted({it["tag"] for it in items if it.get("tag")}))
        print(evalkit.render_matrix(res, tags))
        return 0
    ad = load_adapter(a.store)
    if a.cmd == "dupes":
        from vecstore import dedupe
        rep_ = dedupe.report(ad, a.threshold, a.type)
        if not rep_["rows"]:
            print(f"vecstore dupes: no vectors for {a.store} (run `vecstore index {a.store}`)", file=sys.stderr)
            return 1
        print(json.dumps(rep_, ensure_ascii=False) if a.json else dedupe.render(rep_))
        return 0
    if a.cmd == "index":
        if a.rebuild:
            ad.rebuild() if hasattr(ad, "rebuild") else VecFile(ad.vec_path).clear()
        _refresh(ad)
        print(json.dumps(VecFile(ad.vec_path).sync(ad.entries(), client.embed, progress=not a.quiet)))
        return 0
    if a.cmd == "search":
        res = _search(ad, a.query, a.k, a.type, a.mode)
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
        for r in ([] if a.json else res):
            label = r.get("title") or r.get("heading") or r.get("path") or ""
            print(f"{r['id']}  [{r.get('type', '')}] {label} — {r.get('snippet', '')}")
        return 0
    if a.cmd == "eval":
        _refresh(ad)
        scores = evalkit.run(ad, rerank=a.rerank)
        for m, s in scores.items():
            print(json.dumps({"mode": m, **s}))
        base = Path(ad.eval_dir) / "baseline.json"
        if a.save_baseline:
            evalkit.save_baseline(base, scores)
            print(f"saved {base}", file=sys.stderr)
        if a.check:
            if not base.exists():
                print("vecstore eval: no baseline yet; run with --save-baseline", file=sys.stderr)
                return 1
            bad = evalkit.regressions(scores, json.loads(base.read_text()))
            for b in bad:
                print(f"REGRESSION {b}", file=sys.stderr)
            return 1 if bad else 0
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
