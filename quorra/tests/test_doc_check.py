import json
from pathlib import Path

import pytest

import doc_check
from gitfix import make_repo

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "<?php\n\npublic bool $isOutlet = false;\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    repo, sha = make_repo(tmp_path, {"src/Schema.php": SCHEMA})
    (tmp_path / "repos.json").write_text(json.dumps({"api-app": {"path": str(repo), "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(tmp_path / "repos.json"))
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "noindex"))
    runs = tmp_path / "runs"
    (runs / "r1").mkdir(parents=True)
    (runs / "r1" / "run.json").write_text(json.dumps({"notes": [], "publish": [{"items": []}]}))
    return tmp_path, sha, runs


def contract(sha, body=None, meta=None, run="r1"):
    meta = meta or f"<!-- quorra-doc: doc_id=d1 type=contract run={run} shas=api-app@{sha} -->"
    body = body or ("| Field | Type | Required | Meaning | Example | Source |\n|---|---|---|---|---|---|\n"
                    "| `is_outlet` | bool | no | outlet flag | false | api-app:src/Schema.php:3 |\n")
    return f"""{meta}
# Title

| Item | Value |
|---|---|
| Author | the user |

## Sign-off
<!-- section: sign-off; cite: none -->
| Name | Role | Status |
|---|---|---|

## Overview
<!-- section: overview; cite: none -->
Called by the seller panel.

## Endpoint: GET /x
<!-- section: endpoint; cite: required -->
### Response
{body}
## Links
<!-- section: links; cite: none -->
- Jira: none

## Change log
<!-- section: change-log; cite: none -->
| Date | Summary |
|---|---|
| 2026-10-03 | Drafted by quorra |

## Open questions
<!-- section: open-questions; cite: none -->
None.
"""


def write(tmp_path, text, name="d.md"):
    p = tmp_path / name
    p.write_text(text)
    return p


def reasons(problems):
    return [p["reason"] for p in problems]


def test_templates_parse_and_report_only_unfilled(env):
    tmp_path, _sha, runs = env
    for kind, req in (("trd", 8), ("contract", 1), ("decision", 1)):
        text = (ROOT / "templates" / f"{kind}.md").read_text()
        meta = doc_check.parse_meta(text)
        assert meta["type"] == kind and meta["shas"] == {}
        secs = doc_check.sections(text)
        assert all({"name", "cite", "start", "end", "body_lines"} <= set(s) for s in secs)
        assert sum(s["cite"] == "required" for s in secs) == req
        probs = doc_check.check(write(tmp_path, text, kind + ".md"), runs)
        assert sorted(set(reasons(probs))) == ["empty section", "metadata not filled", "placeholder left"]
        empty_lines = {p["line"] for p in probs if p["reason"] == "empty section"}
        assert {s["start"] for s in secs if s["cite"] == "required"} <= empty_lines
        assert not ({s["start"] for s in secs if s["name"] in ("sign-off", "links", "change-log")} & empty_lines)


def test_parse_meta_missing_and_shas(env):
    assert doc_check.parse_meta("# x\n") == {}
    m = doc_check.parse_meta("<!-- quorra-doc: doc_id=a type=trd run=r shas=api-app@abc1234,pricing@def5678 -->")
    assert m == {"doc_id": "a", "type": "trd", "run": "r", "shas": {"api-app": "abc1234", "pricing": "def5678"}}


def test_missing_meta_line_is_a_problem(env):
    tmp_path, _s, runs = env
    p = write(tmp_path, "# T\n\n## A\n<!-- section: a; cite: none -->\nbody\n")
    assert "missing quorra-doc metadata line" in reasons(doc_check.check(p, runs))


def test_good_contract_passes(env):
    tmp_path, sha, runs = env
    assert doc_check.check(write(tmp_path, contract(sha)), runs) == []


def test_rule1_empty_todo_tbd_dots(env):
    tmp_path, sha, runs = env
    for bad in ("TODO write this", "TBD", "..."):
        text = contract(sha).replace("Called by the seller panel.", bad)
        assert "empty section" in reasons(doc_check.check(write(tmp_path, text), runs)), bad
    text = contract(sha).replace("Called by the seller panel.", "<!-- hint: x -->\n\n")
    probs = doc_check.check(write(tmp_path, text), runs)
    assert [(p["reason"]) for p in probs] == ["empty section"]


def test_rule2_uncited_technical_claim(env):
    tmp_path, sha, runs = env
    text = contract(sha).replace("### Response", "### Response\nThe `isOutlet` flag is read from the view.\n")
    probs = doc_check.check(write(tmp_path, text), runs)
    assert reasons(probs) == ["uncited technical claim"]
    for tok in ("`GET /prices`", "`Foo::bar()`", "`price_rule`", "`prices.final`"):
        t = contract(sha).replace("### Response", f"### Response\nUses {tok} here.\n")
        expect = ["cite as repo:path:line", "uncited technical claim"] if "::" in tok else ["uncited technical claim"]
        assert reasons(doc_check.check(write(tmp_path, t), runs)) == expect, tok
    # plain words, citations and run fields are fine
    ok = contract(sha).replace("### Response", "### Response\nSet `true` for `dev`.\n\nSee `isOutlet` at api-app:src/Schema.php:3.\n\nThe `price_rule` is in run:notes.\n")
    assert doc_check.check(write(tmp_path, ok), runs) == []
    # none sections are exempt
    ex = contract(sha).replace("Called by the seller panel.", "Uses `price_rule` internally.")
    assert doc_check.check(write(tmp_path, ex), runs) == []


def test_rule2_table_rows_are_separate_units(env):
    tmp_path, sha, runs = env
    body = ("| Field | Type | Required | Meaning | Example | Source |\n|---|---|---|---|---|---|\n"
            "| `is_outlet` | bool | no | flag | false | api-app:src/Schema.php:3 |\n"
            "| `other_flag` | bool | no | flag | false | |\n")
    probs = doc_check.check(write(tmp_path, contract(sha, body)), runs)
    assert reasons(probs) == ["field other_flag has no source", "uncited technical claim"]
    assert {q["line"] for q in probs} == {contract(sha, body).split("\n").index("| `other_flag` | bool | no | flag | false | |") + 1}


def test_rule3_cite_as_repo_path_line(env):
    tmp_path, sha, runs = env
    for cite in ("src/Schema.php:3", "Schema.php:3", "`Foo::bar()`"):
        t = contract(sha).replace("Called by the seller panel.", f"Called via {cite}.")
        assert "cite as repo:path:line" in reasons(doc_check.check(write(tmp_path, t), runs)), cite


def test_rule4_repo_file_line(env):
    tmp_path, sha, runs = env
    def rows(src):
        return ("| Field | Type | Required | Meaning | Example | Source |\n|---|---|---|---|---|---|\n"
                f"| `is_outlet` | bool | no | flag | false | {src} |\n")
    assert reasons(doc_check.check(write(tmp_path, contract(sha, rows("unknown-repo:src/Schema.php:3"))), runs)) == ["unknown repo unknown-repo"]
    r = reasons(doc_check.check(write(tmp_path, contract(sha, rows("api-app:src/Nope.php:3"))), runs))
    assert r == [f"file not found at api-app@{sha[:7]}"]
    r = reasons(doc_check.check(write(tmp_path, contract(sha, rows("api-app:src/Schema.php:99"))), runs))
    assert r == ["line 99 beyond end of file (3 lines)"]
    nometa = contract(sha, meta="<!-- quorra-doc: doc_id=d1 type=contract run=r1 shas= -->")
    assert reasons(doc_check.check(write(tmp_path, nometa), runs)) == ["no commit recorded for api-app"]


def test_rule4_falls_back_to_indexed_sha(env, monkeypatch):
    import sqlite3
    tmp_path, sha, runs = env
    idx = tmp_path / "index"
    idx.mkdir()
    con = sqlite3.connect(idx / "api-app.sqlite")
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('sha', ?)", (sha,))
    con.commit()
    con.close()
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(idx))
    nometa = contract(sha, meta="<!-- quorra-doc: doc_id=d1 type=contract run=r1 shas= -->")
    assert doc_check.check(write(tmp_path, nometa), runs) == []


def test_rule5_run_fields(env):
    tmp_path, sha, runs = env
    t = lambda f: contract(sha).replace("Called by the seller panel.", f"Taken from {f}.")
    assert doc_check.check(write(tmp_path, t("run:notes")), runs) == []
    assert doc_check.check(write(tmp_path, t("run:publish.0.items")), runs) == []
    assert reasons(doc_check.check(write(tmp_path, t("run:missing")), runs)) == ["unknown run field run:missing"]
    assert reasons(doc_check.check(write(tmp_path, t("run:publish.3.items")), runs)) == ["unknown run field run:publish.3.items"]
    bad_run = contract(sha, run="nope").replace("Called by the seller panel.", "From run:notes.")
    assert reasons(doc_check.check(write(tmp_path, bad_run), runs)) == ["unknown run field run:notes"]


def test_rule6_secret_and_attribution(env):
    tmp_path, sha, runs = env
    t = contract(sha).replace("Called by the seller panel.", "Token: Bearer abcdef0123456789abcdef0123456789abcdef.")
    assert "possible secret" in reasons(doc_check.check(write(tmp_path, t), runs))
    t = contract(sha).replace("Called by the seller panel.", "Co-Authored-By: quorra <noreply@example.com>")
    assert "tool attribution line" in reasons(doc_check.check(write(tmp_path, t), runs))


def test_rule7_contract_field_declared(env):
    tmp_path, sha, runs = env
    body = ("| Field | Type | Required | Meaning | Example | Source |\n|---|---|---|---|---|---|\n"
            "| `is_outlet` | bool | no | flag | false | api-app:src/Schema.php:3 |\n"
            "| `is_vip` | bool | no | flag | false | api-app:src/Schema.php:3 |\n")
    probs = doc_check.check(write(tmp_path, contract(sha, body)), runs)
    assert reasons(probs) == ["field is_vip not declared in api-app:src/Schema.php"]


def test_problems_sorted_by_line(env):
    tmp_path, sha, runs = env
    t = contract(sha).replace("Called by the seller panel.", "TODO").replace("### Response", "### Response\nUses `price_rule`.\n")
    lines = [p["line"] for p in doc_check.check(write(tmp_path, t), runs)]
    assert lines == sorted(lines) and len(lines) == 2


FA_DRAFT = """<!-- quorra-doc: doc_id=d2 type=decision run=r1 shas=api-app@{sha} -->
# گزارش تصمیم

| مورد | مقدار |
|---|---|
| نویسنده | the user |

## تایید ذینفعان
<!-- section: sign-off; cite: none -->
| نام | نقش | وضعیت |
|---|---|---|

## لینک‌ها
<!-- section: links; cite: none -->
- Jira: none

## سوابق تغییرات
<!-- section: change-log; cite: none -->
| تاریخ | خلاصه |
|---|---|
| 2026-10-03 | Drafted by quorra |

## تصمیم‌ها
<!-- section: decisions; cite: required -->
### تصمیم اول
**سوال**: آیا فیلد اضافه شود؟
**گزینه‌ها**: بله یا خیر
**شواهد**: فیلد `isOutlet` در api-app:src/Schema.php:3 تعریف شده است.
**انتخاب**: بله
**تصمیم‌گیرنده**: رضا
**دلیل**: موجود است
**هزینه در صورت اشتباه**: کم

## استثنا از قواعد
<!-- section: exceptions; cite: none -->
ندارد.

## سوالات باز
<!-- section: open-questions; cite: none -->
ندارد.
"""


def test_filled_persian_decision_draft_passes(env):
    tmp_path, sha, runs = env
    assert doc_check.check(write(tmp_path, FA_DRAFT.format(sha=sha)), runs) == []


def test_missing_duplicate_and_unmarked_sections(env):
    tmp_path, sha, runs = env
    text = FA_DRAFT.format(sha=sha)
    lost = text.replace("<!-- section: exceptions; cite: none -->\n", "")
    r = reasons(doc_check.check(write(tmp_path, lost), runs))
    assert sorted(r) == ["heading without section marker", "missing section exceptions"]
    dup = text + "\n## سوالات باز\n<!-- section: open-questions; cite: none -->\nندارد.\n"
    assert reasons(doc_check.check(write(tmp_path, dup), runs)) == ["duplicate section open-questions"]
    gone = text.split("## استثنا از قواعد")[0]
    assert reasons(doc_check.check(write(tmp_path, gone), runs)) == ["missing section exceptions", "missing section open-questions"]


def test_field_without_source(env):
    tmp_path, sha, runs = env
    for src in ("", "see code"):
        body = ("| Field | Type | Required | Meaning | Example | Source |\n|---|---|---|---|---|---|\n"
                f"| `isOutlet` | bool | no | flag | false | {src} |\n")
        r = reasons(doc_check.check(write(tmp_path, contract(sha, body)), runs))
        assert "field isOutlet has no source" in r, src


def test_nested_field_names_use_last_segment(env):
    tmp_path, sha, runs = env
    body = ("| Field | Type | Required | Meaning | Example | Source |\n|---|---|---|---|---|---|\n"
            "| `data.isOutlet` | bool | no | flag | false | api-app:src/Schema.php:3 |\n"
            "| `items[].is_outlet` | bool | no | flag | false | api-app:src/Schema.php:3 |\n"
            "| `data.items[].is_vip` | bool | no | flag | false | api-app:src/Schema.php:3 |\n")
    r = reasons(doc_check.check(write(tmp_path, contract(sha, body)), runs))
    assert r == ["field data.items[].is_vip not declared in api-app:src/Schema.php"]


def test_dotted_field_name_not_a_claim_in_field_table(env):
    tmp_path, sha, runs = env
    body = ("| Field | Type | Required | Meaning | Example | Source |\n|---|---|---|---|---|---|\n"
            "| `data.price` | int | no | price | 1 | |\n")
    r = reasons(doc_check.check(write(tmp_path, contract(sha, body)), runs))
    assert r == ["field data.price has no source"]


def test_list_items_are_separate_units(env):
    tmp_path, sha, runs = env
    items = "- The `isOutlet` flag, api-app:src/Schema.php:3\n- Then `price_rule` runs\n"
    t = contract(sha).replace("### Response", "### Response\n" + items)
    probs = doc_check.check(write(tmp_path, t), runs)
    assert reasons(probs) == ["uncited technical claim"]
    assert t.split("\n")[probs[0]["line"] - 1] == "- Then `price_rule` runs"
    t = contract(sha).replace("### Response", "### Response\n1. Calls `Foo::bar` now\n")
    assert "uncited technical claim" in reasons(doc_check.check(write(tmp_path, t), runs))


def test_new_claim_patterns_and_plain_words(env):
    tmp_path, sha, runs = env
    for tok in ("`doThing()`", "`Foo::bar`", "`MAX_PRICE`"):
        t = contract(sha).replace("### Response", f"### Response\nUses {tok} here.\n")
        assert "uncited technical claim" in reasons(doc_check.check(write(tmp_path, t), runs)), tok
    t = contract(sha).replace("### Response", "### Response\nThe `offers` and `Offer` words.\n")
    assert doc_check.check(write(tmp_path, t), runs) == []


def test_placeholder_and_bad_line(env):
    tmp_path, sha, runs = env
    t = contract(sha).replace("Called by the seller panel.", "Due <date> and `Map<a, b>`.")
    assert reasons(doc_check.check(write(tmp_path, t), runs)) == ["placeholder left"]
    t = contract(sha).replace("Called by the seller panel.", "See api-app:src/Schema.php:0.")
    assert reasons(doc_check.check(write(tmp_path, t), runs)) == ["invalid line number 0"]


# ---- final-fix wave ----
FIELD_HEAD = "| Field | Type | Required | Meaning | Example | Source |\n|---|---|---|---|---|---|\n"


def with_status(text, status="Draft"):
    return text.replace("| Author | the user |", f"| Author | the user |\n| Status | {status} |")


def proposed_body(sha, changes="| `is_new` | - | added |"):
    return (FIELD_HEAD + f"| `is_outlet` | bool | no | flag | false | api-app:src/Schema.php:3 |\n"
            "| `is_new` | bool | no | proposed flag | false | new (design) |\n"
            "### Changes for callers\n| Item | Old | New |\n|---|---|---|\n" + changes + "\n")


def test_proposed_field_in_draft_contract_passes(env):
    tmp_path, sha, runs = env
    p = write(tmp_path, with_status(contract(sha, proposed_body(sha))))
    assert doc_check.check(p, runs) == []


def test_proposed_field_in_non_draft_contract_refused(env):
    tmp_path, sha, runs = env
    p = write(tmp_path, with_status(contract(sha, proposed_body(sha)), "Approved"))
    assert "proposed field is_new in a non-draft contract" in reasons(doc_check.check(p, runs))


def test_proposed_field_must_be_listed_under_changes(env):
    tmp_path, sha, runs = env
    p = write(tmp_path, with_status(contract(sha, proposed_body(sha, "| other | a | b |"))))
    rs = reasons(doc_check.check(p, runs))
    assert "proposed field is_new not listed under Changes for callers" in rs
    assert not any("no source" in r for r in rs)


def test_uncited_non_proposed_field_still_refused(env):
    tmp_path, sha, runs = env
    body = FIELD_HEAD + "| `is_outlet` | bool | no | flag | false | new (designs) |\n"
    p = write(tmp_path, with_status(contract(sha, body)))
    assert "field is_outlet has no source" in reasons(doc_check.check(p, runs))


def test_empty_field_table_row_is_skipped(env):
    tmp_path, sha, runs = env
    for word in ("None", "ندارد", "-"):
        p = write(tmp_path, contract(sha, FIELD_HEAD + f"| {word} | | | | | |\n"))
        assert doc_check.check(p, runs) == [], word


def test_commit_only_in_work_clone_is_read_from_it(env, monkeypatch):
    import subprocess
    from gitfix import commit
    tmp_path, sha, runs = env
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "clone", "-q", str(tmp_path / "repo"), str(work / "api-app")], check=True)
    new = commit(work / "api-app", {"src/New.php": "<?php\n\npublic bool $isFresh = false;\n"}, "only here")
    monkeypatch.setenv("QUORRA_WORK_DIR", str(work))
    body = FIELD_HEAD + "| `is_fresh` | bool | no | f | false | api-app:src/New.php:3 |\n"
    p = write(tmp_path, contract(new, body))
    assert doc_check.check(p, runs) == []


def test_missing_commit_reported_separately_from_missing_file(env, monkeypatch):
    tmp_path, sha, runs = env
    monkeypatch.setenv("QUORRA_WORK_DIR", str(tmp_path / "nowork"))
    ghost = "0123456789abcdef0123456789abcdef01234567"
    body = FIELD_HEAD + "| `is_outlet` | bool | no | f | false | api-app:src/Schema.php:3 |\n"
    rs = reasons(doc_check.check(write(tmp_path, contract(ghost, body)), runs))
    assert "commit 0123456 not found in api-app" in rs and not any("file not found" in r for r in rs)
    body = FIELD_HEAD + "| `is_outlet` | bool | no | f | false | api-app:src/Nope.php:3 |\n"
    rs = reasons(doc_check.check(write(tmp_path, contract(sha, body), "e.md"), runs))
    assert any(r.startswith("file not found at api-app@") for r in rs) and not any("commit" in r and "not found" in r for r in rs)


def prose_doc(sha, line):
    return contract(sha).replace("Called by the seller panel.", line)


def test_backticked_name_next_to_citation_passes(env):
    tmp_path, sha, runs = env
    p = write(tmp_path, prose_doc(sha, "The flag `Foo::bar()` is read in api-app:src/Schema.php:3."))
    assert "cite as repo:path:line" not in reasons(doc_check.check(p, runs))


def test_bare_name_without_citation_refused(env):
    tmp_path, sha, runs = env
    p = write(tmp_path, prose_doc(sha, "The flag Foo::bar() decides it."))
    assert "cite as repo:path:line" in reasons(doc_check.check(p, runs))
    p = write(tmp_path, prose_doc(sha, "The flag `Foo::bar()` decides it."), "b.md")
    assert "cite as repo:path:line" in reasons(doc_check.check(p, runs))


def test_bare_name_beside_citation_still_refused(env):
    tmp_path, sha, runs = env
    p = write(tmp_path, prose_doc(sha, "Foo::bar() is read in api-app:src/Schema.php:3."))
    assert "cite as repo:path:line" in reasons(doc_check.check(p, runs))


def test_fenced_example_is_ignored(env):
    tmp_path, sha, runs = env
    text = contract(sha).replace("### Response", "### Example\n```\nsrc/Foo/Bar.php:12 Foo::bar() <placeholder> is_outlet\n```\n### Response")
    assert doc_check.check(write(tmp_path, text), runs) == []


def test_persian_digits_in_citation_refused(env):
    tmp_path, sha, runs = env
    p = write(tmp_path, prose_doc(sha, "Declared in api-app:src/Schema.php:۳."))
    assert "use ASCII digits in citations" in reasons(doc_check.check(p, runs))
