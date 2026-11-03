import json
import re
from pathlib import Path

import pytest

import doc
import doc_check
import kb_cite
from gitfix import commit, make_repo

ROOT = Path(__file__).resolve().parent.parent


class FakeConfluence:
    def __init__(self):
        self.pages, self.calls, self.n = {"9": {"id": "9", "title": "parent", "space": {"key": "DOCS"}, "version": {"number": 1}, "body": {"storage": {"value": ""}}}}, [], 100
        self.drop_version = False

    def fetch(self, page_id):
        self.calls.append(("fetch", page_id))
        return self.pages[str(page_id)]

    def create(self, space, title, body, parent_id):
        self.calls.append(("create", space, title, body, parent_id))
        self.n += 1
        p = {"id": str(self.n), "title": title, "space": {"key": space}, "version": {"number": 1}, "body": {"storage": {"value": body}}}
        self.pages[p["id"]] = p
        if self.drop_version:
            return {k: v for k, v in p.items() if k != "version"}
        return p

    def update(self, page_id, title, body, version):
        self.calls.append(("update", str(page_id), title, body, version))
        p = {"id": str(page_id), "title": title, "space": {"key": "DOCS"}, "version": {"number": version + 1}, "body": {"storage": {"value": body}}}
        self.pages[str(page_id)] = p
        return p

    def page_url(self, page):
        return f"https://conf/pages/{page['id']}"

    def writes(self):
        return [c for c in self.calls if c[0] in ("create", "update")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("QUORRA_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "noindex"))
    repo, sha = make_repo(tmp_path, {"src/Schema.php": "<?php\n\npublic bool $isOutlet = false;\n"})
    (tmp_path / "repos.json").write_text(json.dumps({"api-app": {"path": str(repo), "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(tmp_path / "repos.json"))
    monkeypatch.setattr(doc.confluence_client, "md_to_storage", lambda t: "<p>" + t + "</p>")
    run = tmp_path / "runs" / "2026-10-03-x"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run_id": run.name, "docs": [], "request": "r"}))
    return tmp_path, run, repo, sha


def fill(path: Path):
    """Make a new decision draft pass the checker."""
    t = path.read_text()
    t = t.replace("| 	| 	|", "|")
    t = re.sub(r"(?m)^\| \| \| \|$", "| the user | PM | Approved |", t)
    t = re.sub(r"(?m)^\| سایر تیم‌ها \| \|$", "| سایر تیم‌ها | none |", t)
    t = re.sub(r"(?m)^- (Jira|MR|Dashboard|KB): $", r"- \1: n/a", t)
    for lab in ("سوال", "گزینه‌ها", "انتخاب", "تصمیم‌گیرنده", "دلیل", "هزینه در صورت اشتباه"):
        t = t.replace(f"**{lab}**:\n", f"**{lab}**: text\n")
    t = t.replace("**شواهد**:\n", "**شواهد**: run:request\n")
    t = t.replace("### <decision title>", "### Choose A")
    t = t.replace("## سوالات باز\n<!-- section: open-questions; cite: none -->\n", "## سوالات باز\n<!-- section: open-questions; cite: none -->\nnone\n")
    t = t.replace("## استثنا از قواعد\n<!-- section: exceptions; cite: none -->\n", "## استثنا از قواعد\n<!-- section: exceptions; cite: none -->\nnone\n")
    path.write_text(t)


def good_draft(run, slug="d1"):
    p = doc.new(run, "decision", slug)
    fill(p)
    return p


def test_new_fills_meta_and_refuses_second(env):
    _t, run, _r, _s = env
    p = doc.new(run, "decision", "pricing-choice")
    assert p == run / "docs" / "decision-pricing-choice.md"
    text = p.read_text()
    meta = doc_check.parse_meta(text)
    assert meta["doc_id"] == "decision-pricing-choice" and meta["type"] == "decision" and meta["run"] == run.name
    assert "<date>" not in text and "# pricing choice" in text
    with pytest.raises(doc.DocError, match="exists"):
        doc.new(run, "decision", "pricing-choice")


def test_good_draft_passes_check(env):
    _t, run, _r, _s = env
    p = good_draft(run)
    assert doc_check.check(p) == []


def test_plan_refusals(env):
    _t, run, _r, _s = env
    fake = FakeConfluence()
    p = good_draft(run)
    with pytest.raises(doc.DocError, match="DOCS"):
        doc.plan(p, "ENG", "9", None, client=fake)
    with pytest.raises(doc.DocError, match="parent"):
        doc.plan(p, "DOCS", None, None, client=fake)
    with pytest.raises(doc.DocError, match="only updates pages it created"):
        doc.plan(p, "DOCS", "9", "123", client=fake)
    raw = doc.new(run, "decision", "raw")
    with pytest.raises(doc.DocError) as e:
        doc.plan(raw, "DOCS", "9", None, client=fake)
    assert re.search(r"line \d+", str(e.value))
    assert fake.calls == []


def publish_new(run, fake, slug="d1"):
    p = good_draft(run, slug)
    plan = doc.plan(p, "DOCS", "9", None, client=fake)
    return p, plan, doc.publish(p, plan["fingerprint"], client=fake)


def test_create_path(env):
    tmp, run, _r, _s = env
    fake = FakeConfluence()
    p, plan, res = publish_new(run, fake)
    assert plan["mode"] == "create" and plan["space"] == "DOCS" and plan["parent"] == "9"
    assert (tmp / "state" / "pages" / "decision-d1.html").read_text() == fake.writes()[0][3]
    w = fake.writes()
    assert len(w) == 1 and w[0][0] == "create" and w[0][1] == "DOCS" and w[0][4] == "9"
    assert w[0][3].rstrip().endswith("-->") and "<!-- quorra-doc: doc_id=decision-d1" in w[0][3]
    reg = json.loads((tmp / "state" / "pages.json").read_text())["pages"]["decision-d1"]
    assert reg["page_id"] == "101" and reg["version"] == 1 and reg["space"] == "DOCS" and reg["run"] == run.name
    assert res == {"doc_id": "decision-d1", "url": "https://conf/pages/101", "version": 1, "mode": "create"}
    docs = json.loads((run / "run.json").read_text())["docs"]
    assert docs == [{"doc_id": "decision-d1", "type": "decision", "file": str(p), "url": "https://conf/pages/101", "version": 1}]
    assert json.loads(Path(str(p) + ".plan.json").read_text())["fingerprint"] == plan["fingerprint"]


def test_wrong_fingerprint_and_edited_draft(env):
    _t, run, _r, _s = env
    fake = FakeConfluence()
    p = good_draft(run)
    plan = doc.plan(p, "DOCS", "9", None, client=fake)
    with pytest.raises(doc.DocError, match="fingerprint"):
        doc.publish(p, "000000000000", client=fake)
    p.write_text(p.read_text().replace("text", "other text", 1))
    with pytest.raises(doc.DocError, match="fingerprint"):
        doc.publish(p, plan["fingerprint"], client=fake)
    assert fake.writes() == []


def test_update_path(env):
    tmp, run, _r, _s = env
    fake = FakeConfluence()
    p, _plan, _res = publish_new(run, fake)
    p.write_text(p.read_text().replace("**دلیل**: text", "**دلیل**: better reason"))
    plan = doc.plan(p, "DOCS", None, None, client=fake)
    assert plan["mode"] == "update" and plan["version"] == 1 and plan["page_id"] == "101"
    res = doc.publish(p, plan["fingerprint"], client=fake)
    w = fake.writes()
    assert len(w) == 2 and w[1][0] == "update" and w[1][4] == 1 and w[1][1] == "101"
    assert res["version"] == 2 and res["mode"] == "update"
    reg = json.loads((tmp / "state" / "pages.json").read_text())["pages"]["decision-d1"]
    assert reg["version"] == 2
    assert len(json.loads((run / "run.json").read_text())["docs"]) == 2
    with pytest.raises(doc.DocError, match="differs"):
        doc.plan(p, "DOCS", None, "555", client=fake)


def test_edited_by_others_diff_and_status(env):
    _t, run, _r, _s = env
    fake = FakeConfluence()
    p, _plan, _res = publish_new(run, fake)
    assert doc.status(client=fake)[0]["edited_by_others"] is False
    fake.pages["101"]["version"] = {"number": 3}
    fake.pages["101"]["body"]["storage"]["value"] += "\n<p>someone's change</p>"
    with pytest.raises(doc.DocError, match="edited since quorra last wrote it"):
        doc.plan(p, "DOCS", None, None, client=fake)
    assert "+<p>someone's change</p>" in doc.diff("decision-d1", client=fake)
    st = doc.status(client=fake)[0]
    assert st["edited_by_others"] is True and st["live_version"] == 3 and st["doc_id"] == "decision-d1"
    assert len(fake.writes()) == 1


def test_no_delete():
    assert not hasattr(doc, "delete")
    assert not hasattr(FakeConfluence(), "delete") and not hasattr(FakeConfluence(), "move")


def test_status_stale_citations(env, monkeypatch):
    tmp, run, repo, sha = env
    fake = FakeConfluence()
    p = good_draft(run)
    t = p.read_text().replace("run:request", "api-app:src/Schema.php:3")
    t = re.sub(r"shas=", f"shas=api-app@{sha}", t, count=1)
    p.write_text(t)
    plan = doc.plan(p, "DOCS", "9", None, client=fake)
    doc.publish(p, plan["fingerprint"], client=fake)
    assert doc.status(client=fake)[0]["stale_citations"] == 0
    (repo / "src" / "Schema.php").write_text("<?php\n\npublic bool $isOutlet = true;\n")
    new_sha = commit(repo, {"src/Schema.php": "<?php\n\npublic bool $isOutlet = true;\n"}, "change")
    monkeypatch.setattr(kb_cite, "indexed_sha", lambda r: new_sha)
    assert doc.status(client=fake)[0]["stale_citations"] == 1


def test_parent_and_live_space_must_be_cts(env):
    _t, run, _r, _s = env
    fake = FakeConfluence()
    fake.pages["8"] = {"id": "8", "space": {"key": "ENG"}, "version": {"number": 1}, "body": {"storage": {"value": ""}}}
    p = good_draft(run)
    with pytest.raises(doc.DocError, match="ENG"):
        doc.plan(p, "DOCS", "8", None, client=fake)
    publish_new(run, fake, "d2")
    p2 = run / "docs" / "decision-d2.md"
    fake.pages["101"]["space"] = {"key": "ENG"}
    with pytest.raises(doc.DocError, match="ENG"):
        doc.plan(p2, "DOCS", None, None, client=fake)


def test_create_response_without_version(env):
    tmp, run, _r, _s = env
    fake = FakeConfluence()
    fake.drop_version = True
    p = good_draft(run)
    plan = doc.plan(p, "DOCS", "9", None, client=fake)
    with pytest.raises(doc.DocError, match=r"page 101 was written at https://conf/pages/101 but quorra could not record it"):
        doc.publish(p, plan["fingerprint"], client=fake)
    assert len(fake.writes()) == 1
    assert json.loads((tmp / "state" / "pages.json").read_text())["pages"]["decision-d1"]["page_id"] == "101"


def test_meta_line_not_on_first_line(env):
    _t, run, _r, _s = env
    p = good_draft(run)
    lines = p.read_text().split("\n")
    p.write_text("\n".join([lines[1], lines[0]] + lines[2:]))
    # title line now precedes the meta line; the meta line must still be removed from the body
    title, body = doc._body(p.read_text())
    assert "quorra-doc: doc_id" in body.rsplit("\n", 1)[1]
    assert "quorra-doc" not in body.rsplit("\n", 1)[0]


def test_bad_doc_id_refused(env):
    _t, run, _r, _s = env
    p = good_draft(run)
    p.write_text(p.read_text().replace("doc_id=decision-d1", "doc_id=../evil", 1))
    with pytest.raises(doc.DocError, match="doc_id"):
        doc.plan(p, "DOCS", "9", None, client=FakeConfluence())


def test_publish_does_not_rewrite_plan_file(env):
    _t, run, _r, _s = env
    fake = FakeConfluence()
    p = good_draft(run)
    plan = doc.plan(p, "DOCS", "9", None, client=fake)
    pp = Path(str(p) + ".plan.json")
    before = pp.stat().st_mtime_ns
    doc.publish(p, plan["fingerprint"], client=fake)
    assert pp.stat().st_mtime_ns == before


def test_edit_between_plan_and_publish_refused(env):
    _t, run, _r, _s = env
    fake = FakeConfluence()
    p, _plan, _res = publish_new(run, fake)
    p.write_text(p.read_text().replace("**دلیل**: text", "**دلیل**: better"))
    plan = doc.plan(p, "DOCS", None, None, client=fake)
    fake.pages["101"]["version"] = {"number": 2}
    with pytest.raises(doc.DocError, match="edited since"):
        doc.publish(p, plan["fingerprint"], client=fake)
    assert len(fake.writes()) == 1


def test_failing_create_leaves_registry_unchanged(env):
    tmp, run, _r, _s = env
    fake = FakeConfluence()

    def boom(*a):
        raise doc.confluence_client.ConfluenceError("create failed")
    fake.create = boom
    p = good_draft(run)
    plan = doc.plan(p, "DOCS", "9", None, client=fake)
    with pytest.raises(doc.confluence_client.ConfluenceError):
        doc.publish(p, plan["fingerprint"], client=fake)
    assert not (tmp / "state" / "pages.json").exists()
    assert json.loads((run / "run.json").read_text())["docs"] == []


def test_status_records_per_page_error(env):
    _t, run, _r, _s = env
    fake = FakeConfluence()
    publish_new(run, fake)
    del fake.pages["101"]
    st = doc.status(client=fake)
    assert st[0]["doc_id"] == "decision-d1" and "error" in st[0]


def test_body_strips_comments_and_title_and_wraps_decision_rtl(env, monkeypatch):
    _t, run, _r, _s = env
    monkeypatch.setattr(doc.confluence_client, "md_to_storage", doc.confluence_client.md_to_storage.__wrapped__
                        if hasattr(doc.confluence_client.md_to_storage, "__wrapped__") else (lambda t: "<p>" + t.replace("<", "&lt;") + "</p>"))
    p = good_draft(run)
    title, body = doc._body(p.read_text())
    assert title == "d1" and "&lt;!--" not in body and "<!-- section" not in body and "<h1>" not in body and "# d1" not in body
    assert body.startswith('<div dir="rtl">') and "</div>\n<!-- quorra-doc:" in body
    assert body.rstrip().endswith("-->") and body.count("<!--") == 1


def test_body_contract_is_not_rtl(env):
    _t, run, _r, _s = env
    p = doc.new(run, "contract", "c1")
    assert not doc._body(p.read_text())[1].startswith("<div dir")


def test_create_error_gets_check_cts_hint(env):
    _t, run, _r, _s = env
    fake = FakeConfluence()
    p = good_draft(run)
    plan = doc.plan(p, "DOCS", "9", None, client=fake)

    def boom(*a):
        raise doc.confluence_client.ConfluenceError("timeout")
    fake.create = boom
    with pytest.raises(doc.confluence_client.ConfluenceError, match=r"may exist already; check DOCS for 'd1' before retrying"):
        doc.publish(p, plan["fingerprint"], client=fake)


def test_plan_refuses_cited_repo_without_sha(env):
    _t, run, _r, _s = env
    p = good_draft(run)
    p.write_text(p.read_text().replace("run:request", "api-app:src/Schema.php:3"))
    with pytest.raises(doc.DocError, match=r"no commit recorded for api-app in the meta line"):
        doc.plan(p, "DOCS", "9", None, client=FakeConfluence())


def test_status_stale_citation_with_commit_only_in_work_clone(env, monkeypatch):
    import subprocess
    tmp, run, repo, sha = env
    work = tmp / "work"
    work.mkdir()
    subprocess.run(["git", "clone", "-q", str(repo), str(work / "api-app")], check=True)
    old = commit(work / "api-app", {"src/Schema.php": "<?php\n\npublic bool $isOutlet = 1;\n"}, "work only")
    monkeypatch.setenv("QUORRA_WORK_DIR", str(work))
    fake = FakeConfluence()
    p = good_draft(run)
    p.write_text(re.sub(r"shas=", f"shas=api-app@{old}", p.read_text().replace("run:request", "api-app:src/Schema.php:3"), count=1))
    plan = doc.plan(p, "DOCS", "9", None, client=fake)
    doc.publish(p, plan["fingerprint"], client=fake)
    new = commit(work / "api-app", {"src/Schema.php": "<?php\n\npublic bool $isOutlet = 2;\n"}, "again")
    monkeypatch.setattr(kb_cite, "indexed_sha", lambda r: new)
    assert doc.status(client=fake)[0]["stale_citations"] == 1


def test_new_author_from_profile_then_git(env, tmp_path, monkeypatch):
    _t, run, _r, _s = env
    home = tmp_path / "home"
    (home / ".claude" / "astack").mkdir(parents=True)
    (home / ".claude" / "astack" / "profile.env").write_text("ASTACK_FULL_NAME=Sara Example\nCOMPANY_USERNAME=sara.e\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    text = doc.new(run, "trd", "a1").read_text()
    assert "| Author | Sara Example |" in text and "<author>" not in text
    (home / ".claude" / "astack" / "profile.env").unlink()
    monkeypatch.setattr(doc.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "Git Name\n"})())
    assert "| Author | Git Name |" in doc.new(run, "contract", "a2").read_text()
