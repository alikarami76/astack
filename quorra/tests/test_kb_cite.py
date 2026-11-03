from pathlib import Path

import gitsrc
import kb_cite
import qpaths
from gitfix import commit, make_repo

REPOS = {"shared-lib", "api-app", "pricing"}


def forms(text):
    return [(c["form"], c["repo"], c["path"] or c["base"] or c["cls"], c["meth"], c["line"], c["end"])
            for c in kb_cite.parse_text(text, "d.md", REPOS)]


def test_parse_each_citation_form():
    assert forms("see `shared-lib:src/Service/A/B.php:93`") == [
        ("repo_path", "shared-lib", "src/Service/A/B.php", None, 93, None)]
    assert forms("at src/Service/A/B.php:15-19 ok") == [("path", None, "src/Service/A/B.php", None, 15, 19)]
    assert forms("in `src/Service/A/B.php`") == [("path", None, "src/Service/A/B.php", None, None, None)]
    assert forms("line `PeriodicPriceChatBotService.php:6`") == [
        ("file", None, "PeriodicPriceChatBotService.php", None, 6, None)]
    assert forms("call `ChatBotService::approveRequest()`") == [
        ("method", None, "ChatBotService", "approveRequest", None, None)]
    assert forms("literal at `ChatBotService:536`") == [("class_line", None, "ChatBotService", None, 536, None)]


def test_parse_does_not_double_count_overlaps_and_records_doc_line():
    cs = kb_cite.parse_text("a\n`pricing:src/X/Y.php:3` and Y.php:4\n", "f/d.md", REPOS)
    assert [(c["form"], c["doc_line"], c["doc"]) for c in cs] == [("repo_path", 2, "f/d.md"), ("file", 2, "f/d.md")]


def test_unknown_repo_prefix_falls_back_to_path():
    assert forms("`unknown-repo:src/A/B.php:3`") == [("path", None, "src/A/B.php", None, 3, None)]


def test_markdown_links_and_prose_are_not_citations():
    assert forms("see [doc](../0A-Database/README.md) and Step 1: done, C3: bug") == []


def test_parse_footer_any_order():
    t = "x\n<!-- quorra-verified: shared-lib@34793c5 api-app@b960e21 on 2026-09-30 -->\n"
    assert kb_cite.parse_footer(t) == {"shared-lib": "34793c5", "api-app": "b960e21"}
    assert kb_cite.parse_footer("no footer") == {}


def test_feature_kb_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("QUORRA_FEATURE_KB", str(tmp_path))
    assert qpaths.feature_kb_path() == tmp_path


def test_gitsrc_blob_and_commits(tmp_path):
    repo, c1 = make_repo(tmp_path, {"a.php": "one\n", "b.php": "keep\n"})
    c2 = commit(repo, {"a.php": None, "b.php": "keep2\n"}, "drop a")
    assert gitsrc.blob_text(repo, c1, "a.php") == "one\n"
    assert gitsrc.blob_text(repo, c2, "a.php") is None
    assert gitsrc.deleting_commit(repo, c1, c2, "a.php") == c2[:10]
    assert gitsrc.deleting_commit(repo, c1, c2, "b.php") is None
    assert gitsrc.last_commit(repo, c1, c2, "b.php") == c2[:10]
    assert gitsrc.last_commit(repo, c2, c2, "b.php") is None


import json
import sqlite3


def _setup(tmp_path, monkeypatch, files, kb_docs):
    repo, sha = make_repo(tmp_path, files)
    (tmp_path / "repos.json").write_text(json.dumps({"shared-lib": {"path": str(repo), "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(tmp_path / "repos.json"))
    idx = tmp_path / "index"
    idx.mkdir()
    con = sqlite3.connect(idx / "shared-lib.sqlite")
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('sha', ?)", (sha,))
    con.commit()
    con.close()
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(idx))
    kb = tmp_path / "kb"
    for rel, text in kb_docs.items():
        p = kb / "features" / "demo" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return repo, sha, kb


def test_extract_resolves_each_form_and_lists_unresolved(tmp_path, monkeypatch):
    files = {"src/Service/Pricing/ChatBotFacade.php": "<?php\nclass ChatBotFacade {}\n",
             "src/Lib/Other/ChatBotFacade.php": "<?php\n", "src/Service/ChatService.php": "<?php\n"}
    _repo, _sha, kb = _setup(tmp_path, monkeypatch, files, {"01.md": (
        "`shared-lib:src/Service/ChatService.php:1`\n"
        "`Service/ChatService.php:2`\n"
        "`ChatService.php:3`\n"
        "`ChatService::send()`\n"
        "`ChatBotFacade.php:1`\n"
        "`Missing.php:9`\n")})
    res = kb_cite.extract("demo", kb)
    got = [(c["form"], c.get("resolved", {}).get("file")) for c in res["citations"]]
    assert got[:4] == [("repo_path", "src/Service/ChatService.php"), ("path", "src/Service/ChatService.php"),
                       ("file", "src/Service/ChatService.php"), ("method", "src/Service/ChatService.php")]
    reasons = {c["raw"]: c["unresolved"]["reason"] for c in res["unresolved"]}
    assert reasons == {"ChatBotFacade.php:1": "ambiguous", "Missing.php:9": "no match"}
    assert len(res["unresolved"][0]["unresolved"]["candidates"]) == 2


def test_resolver_uses_the_given_baseline_sha(tmp_path, monkeypatch):
    repo, c1, kb = _setup(tmp_path, monkeypatch, {"src/Old.php": "<?php\n"}, {"01.md": "`Old::run()`\n"})
    commit(repo, {"src/Old.php": None, "src/New.php": "<?php\n"}, "rm")
    r = kb_cite.Resolver(qpaths.load_repos())
    c = kb_cite.parse_text("`Old::run()`", "d.md", {"shared-lib"})[0]
    assert r.resolve(dict(c), {"shared-lib": c1})["resolved"]["file"] == "src/Old.php"


def test_read_docs_from_a_kb_commit(tmp_path, monkeypatch):
    _repo, _sha, kb = _setup(tmp_path, monkeypatch, {"src/A.php": "x\n"}, {"01.md": "`A.php:1`\n"})
    import subprocess
    subprocess.run(["git", "-C", str(kb), "init", "-q", "-b", "main"], check=True)
    commit(kb, {"features/demo/01.md": "`A.php:1`\n"}, "kb")
    (kb / "features/demo/01.md").write_text("changed in the working tree\n")
    assert kb_cite.read_docs(kb, "demo", "HEAD") == [("features/demo/01.md", "`A.php:1`\n")]
    assert kb_cite.read_docs(kb, "demo", None) == [("features/demo/01.md", "changed in the working tree\n")]


BODY = "".join(f"line {i}\n" for i in range(1, 31))


def test_classify_unchanged_moved_changed_gone(tmp_path):
    repo, c1 = make_repo(tmp_path, {"a.php": BODY, "b.php": BODY, "c.php": BODY, "d.php": BODY})
    c2 = commit(repo, {"a.php": BODY + "tail\n",                                    # far from line 10: unchanged
                       "b.php": "new top\nnew top 2\n" + BODY,                      # shifted by 2: moved
                       "c.php": BODY.replace("line 11\n", "line 11 edited\n"),       # edit inside the window: changed
                       "d.php": None}, "edit")                                      # deleted: gone
    assert kb_cite.classify(repo, c1, c2, "a.php", 10, None)["status"] == "unchanged"
    assert kb_cite.classify(repo, c1, c2, "b.php", 10, None) == {"status": "moved", "new_line": 12}
    ch = kb_cite.classify(repo, c1, c2, "c.php", 10, None)
    assert ch["status"] == "changed" and ch["new_line"] == 10 and ch["commit"] == c2[:10]
    assert kb_cite.classify(repo, c1, c2, "d.php", 10, None) == {"status": "gone", "commit": c2[:10]}
    assert kb_cite.classify(repo, c1, c1, "d.php", 10, None) == {"status": "unchanged"}


def test_classify_method_body(tmp_path):
    php = "<?php\nclass A {\n    public function run()\n    {\n        return 1;\n    }\n\n    public function other()\n    {\n    }\n}\n"
    repo, c1 = make_repo(tmp_path, {"A.php": php})
    c2 = commit(repo, {"A.php": php.replace("    public function other()\n    {\n    }\n", "    public function other()\n    {\n        return 2;\n    }\n")}, "o")
    assert kb_cite.classify(repo, c1, c2, "A.php", None, "run")["status"] == "unchanged"
    assert kb_cite.classify(repo, c1, c2, "A.php", None, "other")["status"] == "changed"
    c3 = commit(repo, {"A.php": php.replace("function run()", "function start()")}, "rename")
    assert kb_cite.classify(repo, c2, c3, "A.php", None, "run") == {"status": "gone", "commit": c3[:10]}


def test_stale_end_to_end_with_footer_and_no_baseline(tmp_path, monkeypatch):
    repo, c1, kb = _setup(tmp_path, monkeypatch, {"src/Old.php": BODY, "src/Keep.php": BODY}, {})
    c2 = commit(repo, {"src/Old.php": None}, "rm old")
    idx = sqlite3.connect(tmp_path / "index" / "shared-lib.sqlite")
    idx.execute("UPDATE meta SET value = ? WHERE key = 'sha'", (c2,))
    idx.commit()
    idx.close()
    d = kb / "features" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "01.md").write_text(f"`Old.php:3` and `Keep.php:4`\n<!-- quorra-verified: shared-lib@{c1[:7]} on 2026-09-01 -->\n")
    (d / "02.md").write_text("`Keep.php:4` without a footer\n")
    res = kb_cite.stale("demo", kb)
    st = {(i["doc"].rsplit("/", 1)[-1], i["raw"]): i["status"] for i in res["items"]}
    assert st == {("01.md", "Old.php:3"): "gone", ("01.md", "Keep.php:4"): "unchanged", ("02.md", "Keep.php:4"): "no_baseline"}
    assert res["summary"]["gone"] == 1 and res["summary"]["no_baseline"] == 1 and res["summary"]["stale"] == 2
    gone = [i for i in res["items"] if i["status"] == "gone"][0]
    assert gone["commit"] == c2[:10]


def test_stamp_is_idempotent(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("# T\nbody\n")
    kb_cite.stamp(p, {"pricing": "664722c36750", "api-app": "b960e21af10a"}, "2026-09-30")
    once = p.read_text()
    assert once.endswith("<!-- quorra-verified: api-app@b960e21 pricing@664722c on 2026-09-30 -->\n")
    kb_cite.stamp(p, {"pricing": "664722c36750", "api-app": "b960e21af10a"}, "2026-09-30")
    assert p.read_text() == once
    kb_cite.stamp(p, {"pricing": "7e1f0f86aaaa"}, "2026-10-01")
    assert p.read_text().count("quorra-verified") == 1 and "pricing@7e1f0f8 on 2026-10-01" in p.read_text()


def test_classify_moved_with_duplicated_line(tmp_path):
    # File with a line that appears twice - when the whole block shifts, both appear in new locations
    # but only the first one at the specified line should be identified as moved
    body_lines = [f"line {i}\n" for i in range(1, 30)]
    # Insert "return x;" at position 9 (line 10) and position 19 (line 20)
    old_lines = body_lines[:9] + ["return x;\n"] + body_lines[9:19] + ["return x;\n"] + body_lines[19:]
    old_content = "".join(old_lines)
    repo, c1 = make_repo(tmp_path, {"A.php": old_content})
    # Shift the entire block by 3 lines
    new_content = "new 1\nnew 2\nnew 3\n" + old_content
    c2 = commit(repo, {"A.php": new_content}, "shift")
    # Check the first "return x;" at line 10 -> should be at line 13
    result = kb_cite.classify(repo, c1, c2, "A.php", 10, None)
    assert result["status"] == "moved" and result["new_line"] == 13


def test_stamp_removes_multiple_footers(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("# T\nbody\n<!-- quorra-verified: old@12345 on 2026-01-01 -->\nmore\n<!-- quorra-verified: another@67890 on 2026-02-02 -->\n")
    kb_cite.stamp(p, {"pricing": "7e1f0f86aaaa"}, "2026-10-01")
    text = p.read_text()
    assert text.count("quorra-verified") == 1 and "pricing@7e1f0f8 on 2026-10-01" in text


def test_stamp_raises_on_empty_shas(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("# T\nbody\n")
    import pytest
    with pytest.raises(ValueError, match="shas cannot be empty"):
        kb_cite.stamp(p, {}, "2026-10-01")


def _index_at(tmp_path, sha):
    idx = sqlite3.connect(tmp_path / "index" / "shared-lib.sqlite")
    idx.execute("UPDATE meta SET value = ? WHERE key = 'sha'", (sha,))
    idx.commit()
    idx.close()


def test_sha_flag_is_a_fallback_and_the_footer_wins(tmp_path, monkeypatch):
    repo, c1, kb = _setup(tmp_path, monkeypatch, {"src/Keep.php": BODY}, {})
    c2 = commit(repo, {"src/Keep.php": BODY.replace("line 4\n", "line 4 edited\n")}, "edit")
    _index_at(tmp_path, c2)
    d = kb / "features" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "01.md").write_text(f"`Keep.php:4`\n<!-- quorra-verified: shared-lib@{c2[:7]} on 2026-09-30 -->\n")
    (d / "02.md").write_text("`Keep.php:4`\n")
    res = kb_cite.stale("demo", kb, None, {"shared-lib": c1})
    st = {i["doc"].rsplit("/", 1)[-1]: i["status"] for i in res["items"]}
    assert st == {"01.md": "unchanged", "02.md": "changed"}


def test_resolved_by_is_reported(tmp_path, monkeypatch):
    _repo, _sha, kb = _setup(tmp_path, monkeypatch, {"src/Service/ChatService.php": "<?php\n"}, {"01.md": (
        "`src/Service/ChatService.php:1` `ChatService.php:1` `ChatService::send()`\n")})
    res = kb_cite.extract("demo", kb)
    assert [c["resolved"]["resolved_by"] for c in res["citations"]] == ["path", "basename", "class"]


def test_inherited_method_is_unresolved_not_gone(tmp_path):
    php = "<?php\nclass A extends B {\n    public function run()\n    {\n    }\n}\n"
    repo, c1 = make_repo(tmp_path, {"A.php": php})
    c2 = commit(repo, {"A.php": php + "// more\n"}, "edit")
    assert kb_cite.classify(repo, c1, c2, "A.php", None, "inherited") == {
        "status": "unresolved", "reason": "method not defined in file"}


def test_line_range_window_covers_the_whole_range(tmp_path):
    repo, c1 = make_repo(tmp_path, {"a.php": BODY})
    c2 = commit(repo, {"a.php": BODY.replace("line 24\n", "line 24 edited\n")}, "edit")  # end(20)+4
    assert kb_cite.classify(repo, c1, c2, "a.php", 10, None)["status"] == "unchanged"
    assert kb_cite.classify(repo, c1, c2, "a.php", 10, None, 20)["status"] == "changed"


def test_missing_feature_folder_is_an_error(tmp_path, monkeypatch, capsys):
    _repo, _sha, kb = _setup(tmp_path, monkeypatch, {"src/A.php": "x\n"}, {})
    import pytest
    with pytest.raises(kb_cite.KbCiteError):
        kb_cite.extract("nope", kb)
    assert kb_cite.main(["stale", "nope", "--kb", str(kb)]) == 2
    assert kb_cite.main(["extract", "nope", "--kb", str(kb)]) == 2


def test_unknown_sha_repo_and_unreachable_footer(tmp_path, monkeypatch, capsys):
    _repo, c1, kb = _setup(tmp_path, monkeypatch, {"src/Keep.php": BODY}, {})
    d = kb / "features" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "01.md").write_text("`Keep.php:4`\n<!-- quorra-verified: shared-lib@deadbee on 2026-09-30 -->\n")
    (d / "02.md").write_text("`Keep.php:4`\n<!-- quorra-verified: shared-lib@" + c1[:7] + " on 2026-09-30 -->\n")
    assert kb_cite.main(["stale", "demo", "--kb", str(kb), "--sha", "unknown-repo=abc"]) == 2
    assert "unknown repo" in capsys.readouterr().err
    res = kb_cite.stale("demo", kb)
    st = {i["doc"].rsplit("/", 1)[-1]: (i["status"], i.get("reason")) for i in res["items"]}
    assert st == {"01.md": ("unresolved", "baseline sha not found"), "02.md": ("unchanged", None)}


def test_utf8_docs_round_trip(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("# قیمت\n", encoding="utf-8")
    kb_cite.stamp(p, {"pricing": "664722c36750"}, "2026-09-30")
    assert p.read_text(encoding="utf-8").startswith("# قیمت\n")
