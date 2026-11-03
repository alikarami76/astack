# tests/test_eval_mrs.py
import eval_mrs as em


def R(label, tier="dominant", objected=False, exc=False, repo="pricing"):
    return {"repo": repo, "mr_iid": 1, "kind": "rule", "rule_id": "conv_x", "tier": tier, "label": label,
            "reviewer_objected": objected, "exception": exc, "evidence": "f:1"}


def test_score_counts_three_kinds_of_wrong():
    labels = [R("follows")] * 6 + [R("n/a")] * 5 + [
        R("breaks"),                          # (a) binding rule broken, merged unchallenged
        R("breaks", objected=True),           # right: reviewers objected
        R("breaks", exc=True),                # right: recorded exception
        R("breaks", tier="transitional"),     # right: transitional allows old style
        R("follows", objected=True),          # (b)
        {"repo": "pricing", "mr_iid": 1, "kind": "uncovered", "evidence": "…"},  # (c)
    ]
    s = em.score(labels)["pricing"]
    assert s["judgments"] == 12 and s["wrong"] == 3 and s["fail"] is True


def test_score_passes_under_threshold():
    s = em.score([R("follows")] * 19 + [R("breaks")])["pricing"]
    assert s["rate"] == 0.05 and s["fail"] is False


def test_fetch_filters_by_merge_time_excludes_and_scrubs():
    def get_json(endpoint, params):
        if endpoint.endswith("/merge_requests"):
            return [{"iid": 3, "merged_at": "2026-09-25T00:00:00Z", "title": "c"},
                    {"iid": 2, "merged_at": "2026-09-22T00:00:00Z", "title": "b"},
                    {"iid": 1, "merged_at": "2026-09-10T00:00:00Z", "title": "a"}]
        if endpoint.endswith("/changes"):
            return {"changes": [{"new_path": "src/A.php", "old_path": "src/A.php",
                                 "diff": "+ $u = 'redis://:hunter2hunter2@h';\n"}]}
        return []
    mrs = em.fetch("g/x", "master", "2026-09-20T00:00:00Z", 10, {3}, get_json)
    assert [m["iid"] for m in mrs] == [2]
    assert "hunter2hunter2" not in mrs[0]["changes"][0]["diff"]


import json

import pytest


def test_fetch_scrubs_sensitive_assignments_in_diffs_titles_and_notes():
    diff = "+define('DB_PASS','fakepw12345');\n+$password = 'fakepw12345';\n+'api_key' => 'fakepw12345',\n"

    def get_json(endpoint, params):
        if endpoint.endswith("/merge_requests"):
            return [{"iid": 2, "merged_at": "2026-09-22T00:00:00Z", "title": "set $password = 'fakepw12345'"}]
        if endpoint.endswith("/changes"):
            return {"changes": [{"new_path": "a", "old_path": "a", "diff": diff}]}
        return [{"notes": [{"system": False, "body": "remove token=fakepw12345 from here please", "author": {"username": "r"}}]}]
    mrs = em.fetch("g/x", "master", "2026-09-20T00:00:00Z", 10, set(), get_json)
    assert "fakepw12345" not in json.dumps(mrs)


def test_fetch_compares_instants_not_strings():
    def get_json(endpoint, params):
        if endpoint.endswith("/merge_requests"):  # 00:30+03:30 is 21:00Z the day before
            return [{"iid": 1, "merged_at": "2026-09-21T00:30:00.000+03:30", "title": "a"},
                    {"iid": 2, "merged_at": "2026-09-21T00:30:00.000Z", "title": "b"}]
        return {"changes": []} if endpoint.endswith("/changes") else []
    mrs = em.fetch("g/x", "master", "2026-09-21T00:00:00Z", 10, set(), get_json)
    assert [m["iid"] for m in mrs] == [2]
    with pytest.raises(ValueError):
        em.fetch("g/x", "master", "not-a-date", 10, set(), get_json)


def test_validate_rejects_malformed_rows():
    good, bad = em.validate([R("follows"), {"repo": "p", "kind": "rule", "label": "maybe"}, {"kind": "rule", "label": "follows"},
                             {"repo": "p", "kind": "uncovered", "mr_iid": 1}, {"repo": "p", "kind": "other"}, "x"])
    assert len(good) == 2 and len(bad) == 4
    assert em.score([{"repo": "p", "kind": "rule", "label": "maybe"}]) == {}


def test_score_cli_exit_codes(tmp_path, capsys):
    def run(text):
        p = tmp_path / "l.jsonl"
        p.write_text(text)
        return em.main(["score", str(p)])
    row = lambda **k: json.dumps({**R("follows"), **k}) + "\n"
    assert run(row()) == 0
    assert run(row() * 5 + row(label="breaks")) == 1
    assert run("") == 2
    assert run(row(label="n/a")) == 2
    assert run(row() + "{oops\n") == 2 and "l.jsonl:2" in capsys.readouterr().err
    assert run(row() + row(label="maybe")) == 2


def test_exclude_from_skips_malformed_rows(tmp_path, capsys, monkeypatch):
    ex = tmp_path / "r.jsonl"
    ex.write_text('{"mr_iid": 3}\n{bad\n{"note": 1}\n')
    seen = {}
    monkeypatch.setattr(em, "fetch", lambda project, ref, after, limit, exclude, *a: seen.update(ex=exclude) or [])
    monkeypatch.setattr(em.qpaths, "repo_cfg", lambda n: {"gitlab": "g/x", "ref": "master"})
    assert em.main(["fetch", "pricing", "--after", "2026-09-20T00:00:00Z", "--exclude-from", str(ex), "--out", str(tmp_path / "o")]) == 0
    assert seen["ex"] == {3} and "2 malformed" in capsys.readouterr().err


def test_minor_validate_requires_ids_and_tier_and_score_reports_wrong_over_breaks_uncovered():
    base = R("follows")
    bad_rows = [{**base, "mr_iid": "1"}, {**base, "mr_iid": True}, {k: v for k, v in base.items() if k != "mr_iid"},
                {**base, "rule_id": 5}, {k: v for k, v in base.items() if k != "rule_id"}, {**base, "tier": "mostly"},
                {"repo": "p", "kind": "uncovered", "note": "x"}, {"repo": "p", "kind": "uncovered", "mr_iid": "2"}]
    good, bad = em.validate([base, {"repo": "p", "kind": "uncovered", "mr_iid": 2}] + bad_rows)
    assert len(good) == 2 and bad == bad_rows
    labels = [R("follows")] * 6 + [R("breaks"), R("breaks", objected=True), R("follows", objected=True),
                                   {"repo": "pricing", "mr_iid": 1, "kind": "uncovered"}]
    s = em.score(labels)["pricing"]
    assert s["wrong"] == 3 and s["rate"] == 0.3
    assert s["wrong_over_breaks_uncovered"] == 1.0  # 3 wrong / (2 breaks + 1 uncovered)
    assert em.score([R("follows")])["pricing"]["wrong_over_breaks_uncovered"] is None
