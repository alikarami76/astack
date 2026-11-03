#!/usr/bin/env python3
"""Read-only GrowthBook REST client for clu. Stdlib only. Never prints or stores the API key.

  growthbook.py experiments [--status running|stopped|draft] [--q TEXT] [--limit 50]
  growthbook.py experiment <id> [--cache <run>/gb] [--record]
  growthbook.py results <id> [--phase N] [--cache <run>/gb]
  growthbook.py metrics [--q TEXT]

Credentials: GROWTHBOOK_API_URL (the API host, e.g. https://gb-api.example.com) and GROWTHBOOK_API_KEY
(a read-only secret key), from the environment or ~/.claude/secrets/growthbook.env.
"""
from __future__ import annotations

import argparse
import email.utils
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

SECRETS = Path(os.environ.get("CLU_GB_SECRETS", str(Path.home() / ".claude/secrets/growthbook.env")))
MAX_RETRIES = 4


class GBError(RuntimeError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block redirects to prevent re-sending the Authorization header to another host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _creds() -> tuple[str, str]:
    env = dict(os.environ)
    if SECRETS.exists():
        for line in SECRETS.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    url, key = env.get("GROWTHBOOK_API_URL", "").rstrip("/"), env.get("GROWTHBOOK_API_KEY", "")
    if not url or not key:
        raise GBError(f"GrowthBook credentials missing: set GROWTHBOOK_API_URL and GROWTHBOOK_API_KEY in {SECRETS}")
    parts = urllib.parse.urlsplit(url)
    # the API key travels in a header, so plain http is allowed only to this machine
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")):
        raise GBError("GrowthBook API URL must use https")
    return url, key


def _parse_retry_after(retry_after: str, attempt: int) -> float:
    """Parse Retry-After header. Can be seconds (int) or HTTP date. Returns sleep time capped at 60s."""
    try:
        w = float(retry_after)
        if math.isnan(w):
            return 2.0 ** attempt
        return max(0.0, min(60.0, w))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(retry_after)
        sleep_time = (dt - datetime.now(dt.tzinfo)).total_seconds()
        return max(0.0, min(60.0, sleep_time))
    except (TypeError, ValueError):
        return 2.0 ** attempt


def _ssl_context():
    """python.org builds on macOS ship without root certificates; use certifi's bundle when it is installed."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def get(path: str, params: dict | None = None) -> dict:
    """GET only. Retries on 429 and 5xx with backoff (the API allows 60 requests per minute)."""
    url, key = _creds()
    q = ("?" + urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})) if params else ""
    req = urllib.request.Request(f"{url}/api/v1/{path.lstrip('/')}{q}", method="GET",
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    opener = urllib.request.build_opener(NoRedirectHandler(), urllib.request.HTTPSHandler(context=_ssl_context()))
    for attempt in range(MAX_RETRIES):
        try:
            with opener.open(req, timeout=30) as r:
                if "html" in (r.headers.get("Content-Type") or ""):
                    raise GBError(f"GrowthBook API returned an HTML page on {path}: wrong host (use the API host, "
                                  f"not the app URL) or the request was blocked by a firewall")
                try:
                    return json.loads(r.read().decode())
                except (OSError, ValueError) as e:
                    raise GBError(f"GrowthBook API returned an unreadable response on {path}") from None
        except urllib.error.HTTPError as e:
            if e.code in (300, 301, 302, 303, 304, 305, 307, 308):
                raise GBError(f"GrowthBook API redirected ({e.code}) on {path}; refusing to resend the key") from None
            if e.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                sleep_time = _parse_retry_after(e.headers.get("Retry-After", str(2 ** attempt)), attempt)
                time.sleep(sleep_time)
                continue
            detail = ""
            try:
                detail = str(json.loads(e.read().decode()).get("message") or "")[:200]
            except Exception:  # noqa: BLE001 - the body is optional detail only
                pass
            raise GBError(f"GrowthBook API {e.code} on {path}" + (f": {detail}" if detail else "")) from None
        except urllib.error.URLError as e:
            raise GBError(f"GrowthBook unreachable: {e.reason}") from None
    raise GBError(f"GrowthBook API kept failing on {path}")


def _paged(path: str, key: str, params: dict, limit: int) -> list[dict]:
    out, offset = [], 0
    while len(out) < limit:
        page = get(path, {**params, "limit": min(100, limit - len(out)), "offset": offset})
        items = page.get(key, []) or []
        out += items
        if not page.get("hasMore") or len(items) == 0:
            break
        new_offset = page.get("nextOffset", offset + len(items))
        if new_offset <= offset:
            break
        offset = new_offset
    return out[:limit]


def list_experiments(status: str | None = None, q: str = "", limit: int = 50) -> list[dict]:
    exps = _paged("experiments", "experiments", {}, 500)
    rows = [e for e in exps if (not status or e.get("status") == status)
            and (not q or q.lower() in ((e.get("name") or "") + " " + (e.get("hypothesis") or "")).lower())]
    return [{"id": e.get("id"), "name": e.get("name"), "status": e.get("status"), "hashAttribute": e.get("hashAttribute"),
             "dateCreated": e.get("dateCreated"), "tags": (e.get("tags") or [])} for e in rows[:limit]]


def experiment(eid: str) -> dict:
    e = (get(f"experiments/{urllib.parse.quote(eid)}").get("experiment") or {})
    return {"id": e.get("id"), "name": e.get("name"), "status": e.get("status"), "hypothesis": e.get("hypothesis"),
            "hashAttribute": e.get("hashAttribute"), "variations": [{"id": v.get("variationId") or v.get("id"),
                                                                    "key": v.get("key"), "name": v.get("name")}
                                                                   for v in (e.get("variations") or [])],
            "phases": [{"name": p.get("name"), "dateStarted": p.get("dateStarted"), "dateEnded": p.get("dateEnded"),
                        "trafficSplit": p.get("trafficSplit"), "coverage": p.get("coverage")} for p in (e.get("phases") or [])],
            "settings": (e.get("settings") or {}), "datasourceId": e.get("datasourceId"),
            "assignmentQueryId": e.get("assignmentQueryId"), "resultSummary": e.get("resultSummary")}


def results(eid: str, phase: int | None = None) -> dict:
    r = (get(f"experiments/{urllib.parse.quote(eid)}/results", {"phase": phase}).get("result") or {})
    out = {"experimentId": r.get("experimentId", eid), "phase": r.get("phase"), "dateStart": r.get("dateStart"),
           "dateEnd": r.get("dateEnd"), "dateUpdated": r.get("dateUpdated"), "settings": (r.get("settings") or {}), "dimensions": []}
    for dim in (r.get("results") or []):
        d = {"dimension": dim.get("dimension"), "totalUsers": dim.get("totalUsers"),
             "srm_p": ((dim.get("checks") or {}).get("srm")), "metrics": []}
        for m in (dim.get("metrics") or []):
            vs = []
            for v in (m.get("variations") or []):
                a = ((v.get("analyses") or [{}])[0])
                vs.append({"variationId": v.get("variationId"), "variationName": v.get("variationName"),
                           "users": v.get("users"), "engine": a.get("engine"),
                           "numerator": a.get("numerator"), "denominator": a.get("denominator"),
                           "mean": a.get("mean"), "stddev": a.get("stddev"), "percentChange": a.get("percentChange"),
                           "ciLow": a.get("ciLow"), "ciHigh": a.get("ciHigh"), "pValue": a.get("pValue"),
                           "chanceToWin": a.get("chanceToBeatControl", a.get("chanceToWin"))})
            d["metrics"].append({"metricId": m.get("metricId"), "metricName": m.get("metricName"), "variations": vs})
        out["dimensions"].append(d)
    return out


def list_metrics(q: str = "") -> list[dict]:
    """Fact metrics (GrowthBook 3+) first, then legacy metrics; either endpoint may be missing or blocked."""
    ms: list[dict] = []
    for path, key in (("fact-metrics", "factMetrics"), ("metrics", "metrics")):
        try:
            ms += [{**m, "type": m.get("metricType") or m.get("type")} for m in _paged(path, key, {}, 1000)]
        except GBError:
            continue
    return [{"id": m.get("id"), "name": m.get("name"), "type": m.get("type"), "description": ((m.get("description") or ""))[:160]}
            for m in ms if not q or q.lower() in ((m.get("name") or "") + " " + (m.get("description") or "")).lower()]


def record(x: dict) -> str:
    """Upsert the experiment's facts (not its conclusions) into the clu KB as an `experiment` row."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kb"))
    import store

    # Check for name collisions
    name = x.get("name") or x["id"]
    existing_id = store.make_id("experiment", {"name": name})
    existing = store.get(store.connect(), existing_id)
    if existing and existing.get("growthbook_id") and existing.get("growthbook_id") != x["id"]:
        name = f"{name} [{x['id']}]"

    phases = x.get("phases") or [{}]
    row = {"name": name, "kind": "rct", "status": x.get("status"), "growthbook_id": x["id"],
           "unit": x.get("hashAttribute"), "window": f"{phases[0].get('dateStarted') or ''} to {phases[-1].get('dateEnded') or 'running'}",
           "design": f"{len(x.get('variations') or [])} variations; " + "; ".join(v.get("name") or "" for v in (x.get("variations") or [])),
           "estimand": "", "primary_metric": ""}
    if x.get("hypothesis"):
        row["design"] += f". Hypothesis: {x['hypothesis']}"
    return store.upsert(store.connect(), "experiment", row, origin="growthbook", tags=["growthbook"])


def _cache(d: str | None, name: str, obj) -> None:
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        (Path(d) / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="growthbook.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("experiments")
    e.add_argument("--status")
    e.add_argument("--q", default="")
    e.add_argument("--limit", type=int, default=50)
    for name in ("experiment", "results"):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--cache")
        if name == "experiment":
            p.add_argument("--record", action="store_true", help="upsert the experiment into the clu KB")
        if name == "results":
            p.add_argument("--phase", type=int)
    m = sub.add_parser("metrics")
    m.add_argument("--q", default="")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "experiments":
            for x in list_experiments(a.status, a.q, a.limit):
                print(f"{x['id']}  [{x['status']}] {x['name']}  unit={x['hashAttribute']}")
        elif a.cmd == "experiment":
            x = experiment(a.id)
            _cache(a.cache, f"experiment-{a.id}.json", x)
            if a.record:
                print(f"recorded {record(x)}", file=sys.stderr)
            print(json.dumps(x, ensure_ascii=False, indent=2))
        elif a.cmd == "results":
            x = results(a.id, a.phase)
            _cache(a.cache, f"results-{a.id}.json", x)
            print(json.dumps(x, ensure_ascii=False, indent=2))
        else:
            for x in list_metrics(a.q):
                print(f"{x['id']}  [{x['type']}] {x['name']} — {x['description']}")
    except GBError as err:
        print(f"growthbook.py: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
