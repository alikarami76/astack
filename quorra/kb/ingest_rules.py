#!/usr/bin/env python3
"""Load research output into the KB: rules.json (validated at sha, snippets scrubbed), verdicts, cross-repo sharing.
  ingest_rules.py ingest runs/<run>/<repo>/rules.json
  ingest_rules.py ingest <rules.json> --retire-missing   (also retire live rules of the same repo/scope the file lacks)
  ingest_rules.py plan <rules.json>                      (dry run: new / tier_changed / missing / rejected)
  ingest_rules.py supersede <old_id> --by <new_id>
  ingest_rules.py drop <id> --run-id <run> --reason "..."   (give up a rule: superseded_by dropped:<run>, plus a lesson)
  ingest_rules.py verdicts runs/<run>/<repo>/verdicts.json --run-id <run> [--round N]
  ingest_rules.py share runs/<run>/consistency.json --run-id <run>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "scripts")]
import store  # noqa: E402
import gitsrc  # noqa: E402
import qpaths  # noqa: E402
from qindex.redact import scrub  # noqa: E402

RANK = {t: i for i, t in enumerate(store.TIERS)}


def _line_counts(repo_path: Path, sha: str, files: set[str]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for path, data in gitsrc.read_files(repo_path, sha, sorted(files)):
        out[path] = None if data is None else data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
    return out


def _examples(r: dict, key: str) -> list[dict]:
    ex = r.get(key) or []
    if not isinstance(ex, list) or not all(isinstance(e, dict) and isinstance(e.get("file"), str) for e in ex):
        raise ValueError(f"{key} must be a list of objects with a string file")
    return ex


def _problem(r: dict, counts: dict) -> str | None:
    if not isinstance(r, dict):
        return "rule entry is not an object"
    for k in ("area", "rule"):
        if not str(r.get(k) or "").strip():
            return f"missing {k}"
    if r.get("tier") not in store.TIERS:
        return f"tier must be one of {store.TIERS}"
    if r.get("source") not in store.SOURCES:
        return f"source must be one of {store.SOURCES}"
    try:
        ex, cex = _examples(r, "examples"), _examples(r, "counter_examples")
    except ValueError as e:
        return str(e)
    need = 2 if r["source"] == "code" else 1
    if len(ex) < need:
        return f"needs at least {need} examples" if need == 1 else "needs at least 2 examples"
    for e in ex + cex:
        if not isinstance(e.get("line"), int) or isinstance(e["line"], bool) or e["line"] < 1:
            return f"bad line in {e.get('file')}"
        if e.get("file") == "reviews.jsonl" and r["source"] == "review":
            continue
        n = counts.get(e.get("file"))
        if n is None:
            return f"example file not found at sha: {e.get('file')}"
        if e["line"] > n:
            return f"line {e['line']} beyond end of {e['file']} ({n} lines)"
    return None


def _clean(examples: list[dict]) -> list[dict]:
    return [{"file": e["file"], "line": e["line"], "snippet": scrub(str(e.get("snippet", "")))[:160]} for e in examples]


EVIDENCE_KEYS = ("files_following", "files_checked", "recent_following", "recent_checked")
WHOLE_REPO_ONLY = "belongs to the whole-repo pass"


def _evidence(ev: dict) -> dict:
    """The evidence counts as integers. A numeric string is coerced; anything else raises ValueError naming the key."""
    out = {}
    for k in EVIDENCE_KEYS:
        if k not in ev:
            continue
        v = ev[k]
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"evidence {k} must be an integer")
        out[k] = v
    return out


def _rule_text(r) -> str | None:
    """A rule's wording for a `rejected` entry, scrubbed like everything else that is printed or stored."""
    return _text(r.get("rule")) or None if isinstance(r, dict) else None


def _text(v) -> str:
    return scrub(str(v or "")).strip()


def _check_doc(doc, repo_path: Path) -> None:
    if not isinstance(doc, dict):
        raise ValueError("rules document must be an object")
    for k in ("repo", "run_id"):
        if not isinstance(doc.get(k), str) or not doc[k].strip():
            raise ValueError(f"rules document needs a non-empty {k}")
    sha = doc.get("sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("rules document needs a 40-hex sha")
    if not isinstance(doc.get("rules"), list):
        raise ValueError("rules document needs a rules list")
    if doc.get("scope") is not None and not isinstance(doc["scope"], str):
        raise ValueError("scope must be a string (a module path prefix, or empty)")
    doc["scope"] = qpaths.normalize_prefix(_text(doc.get("scope"))) or ""  # same rule as prep_repo.py --path-prefix
    try:
        gitsrc._run(Path(repo_path), "cat-file", "-e", f"{sha}^{{commit}}")
    except Exception as e:  # noqa: BLE001 - any git failure means the sha is not usable here
        raise ValueError(f"sha {sha} is not a commit in {repo_path}") from e
    _cross_check(doc["rules"])


def _cross_check(entries: list) -> None:
    """Whole-file checks of a revision file, before anything is written: each old id may be revised by one rule or dropped
    once, never both. A conflict is a document error (the CLI exits 1 and nothing is written)."""
    replaced: dict[str, int] = {}
    dropped: dict[str, int] = {}
    for r in entries:
        if _is_drop(r):
            if isinstance(r.get("drop"), str):
                k = r["drop"].strip()
                dropped[k] = dropped.get(k, 0) + 1
        elif isinstance(r, dict) and isinstance(r.get("replaces"), str) and r["replaces"].strip():
            k = r["replaces"].strip()
            replaced[k] = replaced.get(k, 0) + 1
    problems = []
    split = sorted(k for k, n in replaced.items() if n > 1)
    if split:
        problems.append(f"more than one rule replaces {', '.join(split)}: keep `replaces` on the one rule that revises it and "
                        "add the other rules as new rules without `replaces`")
    both = sorted(replaced.keys() & dropped.keys())
    if both:
        problems.append(f"{', '.join(both)} is both revised (`replaces`) and dropped: either revise it or drop it, not both")
    twice = sorted(k for k, n in dropped.items() if n > 1)
    if twice:
        problems.append(f"{', '.join(twice)} is dropped more than once: list each dropped id once")
    if problems:
        raise ValueError("revision file conflicts, nothing was written: " + "; ".join(problems))


def _validated(doc: dict, repo_path: Path) -> tuple[list[dict], list[dict]]:
    """Validate the document and every rule; return (rows ready to store, rejected). Writes nothing."""
    _check_doc(doc, repo_path)
    files = set()
    for r in doc["rules"]:
        if isinstance(r, dict) and not _is_drop(r):
            for key in ("examples", "counter_examples"):
                try:
                    files |= {e["file"] for e in _examples(r, key) if e["file"] != "reviews.jsonl"}
                except ValueError:
                    pass
    counts = _line_counts(Path(repo_path), doc["sha"], files)
    rows, rejected = [], []
    for r in doc["rules"]:
        if _is_drop(r):
            continue
        try:
            why = _problem(r, counts)
            if not why and _scope(doc) and (r["source"] in ("tooling", "review") or r["tier"] == "enforced"):
                what = f"source: {r['source']}" if r["source"] in ("tooling", "review") else "tier: enforced"
                why = f"{what} {WHOLE_REPO_ONLY}, not a module run (scope {_scope(doc)})"
            ev = r.get("evidence") if isinstance(r, dict) else None
            if not why and isinstance(ev, dict) and ev:
                try:
                    ev = _evidence(ev)
                except ValueError as e:
                    why = str(e)
            if why:
                rejected.append({"rule": _rule_text(r), "reason": why})
                continue
            rationale = _text(r.get("rationale"))
            if isinstance(ev, dict) and ev:
                rationale += _text(f" Evidence: {ev.get('files_following', '?')}/{ev.get('files_checked', '?')} files, "
                                   f"{ev.get('recent_following', '?')}/{ev.get('recent_checked', '?')} recent.")
            rows.append({"repo": doc["repo"], "scope": _scope(doc), "area": _text(r["area"]), "rule": _text(r["rule"]), "tier": r["tier"], "source": r["source"],
                         "rationale": rationale.strip(), "examples": _clean(r["examples"]),
                         "counter_examples": _clean(r.get("counter_examples") or []), "checked_at_sha": doc["sha"],
                         "verified": 0, "run_id": doc["run_id"]})
            if r.get("replaces") not in (None, ""):
                if not isinstance(r["replaces"], str):
                    raise ValueError("replaces must be a convention id string")
                rows[-1]["_replaces"] = r["replaces"].strip()
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            rejected.append({"rule": _rule_text(r), "reason": _text(f"malformed: {e}")})
    return rows, rejected


def _is_drop(r) -> bool:
    """A revision-file entry {"drop": "<id>", "reason": "..."}: the synthesizer gave the rule up."""
    return isinstance(r, dict) and "drop" in r and "rule" not in r


def _pairing(conn, doc: dict, rows: list[dict], rejected: list[dict]) -> list[dict]:
    """Check each row's `replaces` before anything is written. A rule whose `replaces` is unknown, of another repo, or no longer
    live (unless it already points at this rule) is moved to `rejected`; the others keep their `_replaces`."""
    keep = []
    for row in rows:
        old_id = row.get("_replaces")
        if old_id:
            new_id = _key(doc, row)
            old = store.get(conn, old_id)
            why = None
            if not old or old["type"] != "convention":
                why = f"replaces {old_id}, which is not a known convention id"
            elif old["repo"] != doc["repo"]:
                why = f"replaces {old_id}, which belongs to repo {old['repo']}"
            elif (old.get("scope") or "") != _scope(doc):
                why = f"replaces {old_id}, which belongs to scope {old.get('scope') or '(whole repo)'}, not {_scope(doc) or '(whole repo)'}"
            elif old_id != new_id and old.get("superseded_by") not in (None, "", new_id):
                why = f"replaces {old_id}, which is not live (superseded_by {old['superseded_by']})"
            if why:
                rejected.append({"rule": row["rule"], "reason": why})
                continue
        keep.append(row)
    return keep


def _drops(conn, doc: dict, rejected: list[dict]) -> list[dict]:
    """The valid drop entries of a revision file, as (id, reason); invalid ones go to `rejected`."""
    out = []
    for r in doc["rules"]:
        if not _is_drop(r):
            continue
        rid = r.get("drop")
        row = store.get(conn, rid) if isinstance(rid, str) else None
        if not row or row["type"] != "convention":
            rejected.append({"rule": None, "reason": f"drop {rid}: not a known convention id"})
        elif row["repo"] != doc["repo"]:
            rejected.append({"rule": row["rule"], "reason": f"drop {rid}: belongs to repo {row['repo']}"})
        elif row.get("superseded_by"):
            rejected.append({"rule": row["rule"], "reason": f"drop {rid}: not live (superseded_by {row['superseded_by']})"})
        else:
            out.append({"id": rid, "reason": str(r.get("reason") or "")})
    return out


def _scope(doc: dict) -> str:
    return qpaths.normalize_prefix(_text(doc.get("scope"))) or ""


def _key(doc: dict, row: dict) -> str:
    return store.make_id("convention", {"repo": doc["repo"], "scope": _scope(doc), "area": _text(row.get("area")), "rule": _text(row.get("rule"))})


def _existing(conn, doc: dict, row: dict) -> dict | None:
    """The stored row with the same natural key (repo, scope, area, rule), compared through the id."""
    return store.get(conn, _key(doc, row))


def _dead(row: dict | None) -> bool:
    return bool(row and str(row.get("superseded_by") or "").startswith(("retired:", "dropped:")))


def _chain_end(conn, row: dict | None) -> dict | None:
    """Follow superseded_by through conv_ ids; the last row reached (live, retired or dropped). None for a row that was never
    superseded by another rule, a broken link or a cycle."""
    if not row or not str(row.get("superseded_by") or "").startswith("conv_"):
        return None
    seen = set()
    while row and str(row.get("superseded_by") or "").startswith("conv_"):
        if row["id"] in seen:
            return None
        seen.add(row["id"])
        row = store.get(conn, row["superseded_by"])
    return row


def _reworded_to(conn, row: dict | None) -> str | None:
    """If the row was superseded by a reworded rule, the id of the live rule at the end of the chain, else None.
    Shared rules (repo shared-rules) and chains that end dead do not count as rewordings."""
    end = _chain_end(conn, row)
    if end is None or end.get("superseded_by") or end["repo"] == "shared-rules":
        return None
    return end["id"]


def _kept_by(conn, row: dict | None) -> str | None:
    """The live rule at the end of the chain (a rewording, or a shared-rules rule it moved to), else None."""
    end = _chain_end(conn, row)
    return None if end is None or end.get("superseded_by") else end["id"]


def _dead_via_chain(conn, row: dict | None) -> dict | None:
    """The dead row at the end of the chain when a rewording's replacement was later retired or dropped, else None:
    the old wording is then dead too, and re-finding it revives it."""
    end = _chain_end(conn, row)
    return end if _dead(end) else None


def _revivable(conn, row: dict | None) -> bool:
    return _dead(row) or _dead_via_chain(conn, row) is not None


def _reverted(conn, row: dict | None, replaces: str | None) -> bool:
    """A revision that goes back to an older wording: the stored row was reworded, and the rule now names the id it was
    reworded to in `replaces`. The older row becomes live again and the rewording is superseded by it."""
    return bool(replaces and row and _reworded_to(conn, row) == replaces)


def _missing(conn, doc: dict) -> list[dict]:
    """Live rules of the same repo and scope that the document does not contain. Rejected rules count as contained, and so
    does the live replacement of a rule the document re-found under its old wording."""
    present = set()
    for r in doc["rules"]:
        if isinstance(r, dict) and not _is_drop(r):
            k = _key(doc, r)
            present.add(k)
            end = _reworded_to(conn, store.get(conn, k))
            if end:
                present.add(end)
    out = []
    for r in conn.execute("SELECT * FROM conventions WHERE repo = ? AND COALESCE(scope, '') = ? AND (superseded_by IS NULL OR superseded_by = '')",
                          (doc["repo"], _scope(doc))):
        r = dict(r)
        if r["id"] not in present:
            out.append({"id": r["id"], "area": r["area"], "rule": r["rule"], "tier": r["tier"]})
    return out


def ingest(conn, doc: dict, repo_path: Path, retire_missing: bool = False) -> dict:
    """Upsert every valid rule. A rule with `replaces` (revision files) supersedes that id when its own id differs, carrying
    the refutation tags over; a {"drop": id, "reason": ...} entry gives the rule up (see drop())."""
    rows, rejected = _validated(doc, repo_path)
    rows = _pairing(conn, doc, rows, rejected)
    drops = _drops(conn, doc, rejected)
    res = {"ingested": [], "rejected": rejected, "unchanged_via_rewording": [], "superseded": [], "dropped": []}
    for row in rows:
        replaces = row.pop("_replaces", None)
        old = _existing(conn, doc, row)
        revert = _reverted(conn, old, replaces)
        if _kept_by(conn, old) and not revert:  # old wording of a reworded or shared rule: leave it superseded, its replacement stays live
            res["unchanged_via_rewording"].append(old["id"])
            continue
        revive = _revivable(conn, old) or revert
        if old and old["tier"] == row["tier"] and not revive:
            row["verified"] = old["verified"] or 0  # same tier: earlier verification still stands
        eid = store.upsert(conn, "convention", row, origin="research")
        if revive:  # found again: clear the retired/dropped marker and old refutations (store._merge skips empty values, so write directly)
            tags = [x for x in json.loads(old.get("tags") or "[]") if not x.startswith("refuted:")]
            conn.execute("UPDATE conventions SET superseded_by = '', verified = 0, tags = ? WHERE id = ?",
                         (json.dumps(sorted(tags), ensure_ascii=False), eid))
            conn.commit()
        # after any revive, so the new id is live when the old one is pointed at it
        if replaces and replaces != eid and store.get(conn, replaces).get("superseded_by") != eid:
            supersede(conn, replaces, eid)
            res["superseded"].append([replaces, eid])
        res["ingested"].append(eid)
    for d in drops:
        drop(conn, d["id"], doc["run_id"], d["reason"])
        res["dropped"].append(d["id"])
    # `ingested` feeds round 2, so it lists only rules that are live now; an id this file reworded moves to unchanged_via_rewording
    live = []
    for i in dict.fromkeys(res["ingested"]):
        sup = str((store.get(conn, i) or {}).get("superseded_by") or "")
        if not sup:
            live.append(i)
        elif sup.startswith("conv_") and i not in res["unchanged_via_rewording"]:
            res["unchanged_via_rewording"].append(i)
    res["ingested"] = live
    if retire_missing:
        res["retired"] = _missing(conn, doc)
        for m in res["retired"]:
            store.upsert(conn, "convention", {**store.get(conn, m["id"]), "superseded_by": f"retired:{doc['run_id']}"})
    return res


def plan(conn, doc: dict, repo_path: Path) -> dict:
    """What ingesting this document would change. Writes nothing."""
    rows, rejected = _validated(doc, repo_path)
    rows = _pairing(conn, doc, rows, rejected)
    out = {"new": [], "tier_changed": [], "revived": [], "unchanged": 0, "unchanged_via_rewording": [],
           "supersedes": [], "drop": [d["id"] for d in _drops(conn, doc, rejected)],
           "missing": _missing(conn, doc), "rejected": rejected}
    for row in rows:
        replaces = row.pop("_replaces", None)
        new_id = _key(doc, row)
        if replaces and replaces != new_id:
            out["supersedes"].append([replaces, new_id])
        old = _existing(conn, doc, row)
        end = _kept_by(conn, old)
        dead_end = _dead_via_chain(conn, old)
        if not old:
            out["new"].append({"area": row["area"], "rule": row["rule"], "tier": row["tier"]})
        elif _reverted(conn, old, replaces):
            out["revived"].append({"area": row["area"], "rule": row["rule"], "tier": row["tier"],
                                   "was": f"reworded to {replaces}; this revision goes back to it"})
        elif end:
            out["unchanged_via_rewording"].append({"area": row["area"], "rule": row["rule"], "replaced_by": end})
        elif _dead(old):
            out["revived"].append({"area": row["area"], "rule": row["rule"], "tier": row["tier"], "was": old["superseded_by"]})
        elif dead_end:
            out["revived"].append({"area": row["area"], "rule": row["rule"], "tier": row["tier"],
                                   "was": f"{dead_end['superseded_by']} (via its rewording {dead_end['id']})"})
        elif old["tier"] != row["tier"]:
            out["tier_changed"].append({"rule": row["rule"], "area": row["area"], "old": old["tier"], "new": row["tier"]})
        else:
            out["unchanged"] += 1
    return out


def supersede(conn, old_id: str, new_id: str) -> dict:
    if old_id == new_id:  # a case/whitespace-only rewording maps to the same id: nothing to do
        return {"superseded": False, "reason": "same id"}
    old, new = store.get(conn, old_id), store.get(conn, new_id)
    for i, r in ((old_id, old), (new_id, new)):
        if not r or r["type"] != "convention":
            raise ValueError(f"{i} is not a known convention id")
    if old["repo"] != new["repo"]:
        raise ValueError("both rules must belong to the same repo")
    for i, r in ((old_id, old), (new_id, new)):
        if r.get("superseded_by"):
            raise ValueError(f"{i} is not live (superseded_by {r['superseded_by']})")
    refuted = [x for x in json.loads(old.get("tags") or "[]") if x.startswith("refuted:")]
    store.upsert(conn, "convention", {**new}, tags=refuted)  # refutation tags carry their run id, so they only count within that run
    store.upsert(conn, "convention", {**old, "superseded_by": new_id})
    return {"superseded": old_id, "by": new_id}


def _drop_lesson(conn, row: dict, run_id: str, why: str) -> None:
    store.upsert(conn, "lesson", {"rule": f"Dropped convention: {row['rule']}", "why": why,
                                  "applies_to": f"{row['repo']}/{row['area']}", "run_id": run_id}, origin="research")


def drop(conn, rid: str, run_id: str, reason: str = "") -> dict:
    """Give up a live rule (the synthesizer could not revise it): superseded_by dropped:<run> plus a lesson."""
    row = store.get(conn, rid)
    if not row or row["type"] != "convention":
        raise ValueError(f"{rid} is not a known convention id")
    if row.get("superseded_by"):
        raise ValueError(f"{rid} is not live (superseded_by {row['superseded_by']})")
    store.upsert(conn, "convention", {**row, "superseded_by": f"dropped:{run_id}", "verified": 0})
    _drop_lesson(conn, row, run_id, f"given up after refutation: {scrub(str(reason or ''))}")
    return {"dropped": rid, "by": f"dropped:{run_id}"}


VERDICTS = ("sustained", "refuted")


def apply_verdicts(conn, verdicts: list[dict], run_id: str, round: int = 1) -> dict:
    """Validates the whole list first; malformed or unknown items are skipped and reported, never treated as refutations."""
    res = {"verified": 0, "refuted": 0, "dropped": [], "skipped": []}
    todo = []
    for v in verdicts if isinstance(verdicts, list) else []:
        vid = v.get("id") if isinstance(v, dict) else None
        if not isinstance(vid, str) or v.get("verdict") not in VERDICTS:
            res["skipped"].append({"id": vid, "reason": f"verdict must be one of {VERDICTS}"})
            continue
        row = store.get(conn, vid)
        if not row or row["type"] != "convention":
            res["skipped"].append({"id": vid, "reason": "not a known convention id"})
            continue
        todo.append((v, row))
    if not isinstance(verdicts, list):
        res["skipped"].append({"id": None, "reason": "verdicts must be a list"})
    for v, row in todo:
        if v["verdict"] == "sustained":
            store.upsert(conn, "convention", {**row, "verified": 1})
            res["verified"] += 1
            continue
        tags = json.loads(row.get("tags") or "[]") + [f"refuted:{run_id}:{round}"]
        res["refuted"] += 1
        upd = {**row, "verified": 0}
        # dropped after refutations in two distinct rounds of THIS run; tags from other runs never count
        if sum(t.startswith(f"refuted:{run_id}:") for t in set(tags)) >= 2 and not row.get("superseded_by"):
            upd["superseded_by"] = f"dropped:{run_id}"
            res["dropped"].append(v["id"])
            _drop_lesson(conn, row, run_id, f"refuted twice: {scrub(str(v.get('evidence', '')))}")
        store.upsert(conn, "convention", upd, tags=tags)
    return res


def share_report(conn, groups: list[dict], run_id: str) -> dict:
    out = {"shared": [], "skipped": []}
    for g in groups if isinstance(groups, list) else []:
        if not isinstance(g, dict) or not g.get("rule") or not g.get("area") or not isinstance(g.get("members"), list):
            out["skipped"].append({"group": g.get("rule") if isinstance(g, dict) else None, "reason": "malformed group"})
            continue
        members = []
        for i in g["members"]:
            m = store.get(conn, i) if isinstance(i, str) else None
            if not m or m["type"] != "convention":
                out["skipped"].append({"id": i, "reason": "not a known convention id"})
            elif m.get("superseded_by"):
                out["skipped"].append({"id": i, "reason": f"already superseded by {m['superseded_by']}"})
            else:
                members.append(m)
        if len(members) < 2:
            out["skipped"].append({"group": g["rule"], "reason": "fewer than 2 live members"})
            continue
        # the WEAKEST member tier (enforced > dominant > transitional > stated_only): a shared rule is only as binding as the
        # least binding repo, so a repo that merely leans to the style is not told the style is dominant everywhere
        tier = max((m["tier"] for m in members), key=RANK.get)
        ex = [{**e, "file": f"{m['repo']}:{e['file']}"} for m in members for e in json.loads(m.get("examples") or "[]")][:3]
        sid = store.upsert(conn, "convention", {
            "repo": "shared-rules", "area": _text(g["area"]), "rule": _text(g["rule"]), "tier": tier,
            "source": members[0]["source"], "rationale": f"Shared by {', '.join(sorted({m['repo'] for m in members}))}.",
            "examples": ex, "checked_at_sha": "", "verified": int(all(m.get("verified") for m in members)),
            "run_id": run_id}, origin="research")
        for m in members:
            if m["id"] != sid:
                store.upsert(conn, "convention", {**m, "superseded_by": sid})
        out["shared"].append(sid)
    return out


def share(conn, groups: list[dict], run_id: str) -> list[str]:
    return share_report(conn, groups, run_id)["shared"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ingest_rules.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest"); i.add_argument("file"); i.add_argument("--retire-missing", action="store_true")
    pl = sub.add_parser("plan"); pl.add_argument("file")
    sp = sub.add_parser("supersede"); sp.add_argument("old_id"); sp.add_argument("--by", required=True)
    dp = sub.add_parser("drop"); dp.add_argument("id"); dp.add_argument("--run-id", required=True); dp.add_argument("--reason", default="")
    v = sub.add_parser("verdicts"); v.add_argument("file"); v.add_argument("--run-id", required=True); v.add_argument("--round", type=int, default=1)
    s = sub.add_parser("share"); s.add_argument("file"); s.add_argument("--run-id", required=True)
    a = ap.parse_args(argv)
    conn = store.connect()
    try:
        if a.cmd == "supersede":
            res = supersede(conn, a.old_id, a.by)
            data = None
        elif a.cmd == "drop":
            res = drop(conn, a.id, a.run_id, a.reason)
            data = None
        else:
            data = json.loads(Path(a.file).read_text(encoding="utf-8"))
        if a.cmd == "ingest":
            res = ingest(conn, data, qpaths.repo_cfg(data["repo"])["path"], retire_missing=a.retire_missing)
        elif a.cmd == "plan":
            res = plan(conn, data, qpaths.repo_cfg(data["repo"])["path"])
            print(json.dumps(res, ensure_ascii=False))
            return 0
        elif a.cmd in ("supersede", "drop"):
            pass
        elif a.cmd == "verdicts":
            res = apply_verdicts(conn, data, a.run_id, a.round)
        else:
            res = share_report(conn, data, a.run_id)
    except OSError as e:
        print(f"ingest_rules: cannot read {getattr(e, 'filename', None) or e}: {e.strerror or e}", file=sys.stderr)
        return 2
    except (ValueError, KeyError, TypeError) as e:
        print(f"ingest_rules: {e}", file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False))
    try:
        store.export(conn, etype="convention")
        store.export(conn, etype="lesson")
    except store.ExportRefused as e:
        print(f"ingest_rules: export refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
