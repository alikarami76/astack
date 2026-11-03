import json
from pathlib import Path

from gitfix import commit, make_repo
from qindex import build, db

FX = Path(__file__).parent / "fixtures"


def php(name):
    return (FX / "php" / name).read_text()


def test_should_index_filters():
    assert build.should_index("src/Service/A.php", "php")
    assert not build.should_index("vendor/x/A.php", "php")
    assert not build.should_index("tests/AServiceTest.php", "php")
    assert build.should_index("src/features/a/useX.ts", "ts")
    assert not build.should_index("src/a/X.test.tsx", "ts")
    assert not build.should_index("src/a/X.stories.tsx", "ts")
    assert not build.should_index("node_modules/a/index.ts", "ts")
    assert build.should_index("config/cron/crontab", "php")


def test_failures_recorded_not_silent(tmp_path, monkeypatch):
    repo, s1 = make_repo(tmp_path, {"src/A.php": "<?php class A {}", "src/B.php": "<?php class B {}"})

    def boom(path, text):
        if path.endswith("B.php"):
            raise ValueError("bad")
        return []
    monkeypatch.setattr(build, "extract_php", boom)
    r = build.build_index("x", repo, "php", s1, tmp_path / "i.sqlite")
    assert r["failures"] == 1
    conn = db.connect(tmp_path / "i.sqlite")
    assert conn.execute("SELECT file, error FROM failures").fetchall()[0][0] == "src/B.php"


def _norm(conn, name):
    return json.loads(conn.execute("SELECT extra FROM symbols WHERE name=?", (name,)).fetchone()[0])["url_norm"]


def _seed_ts(tmp_path):
    ts = FX / "ts"
    repo, s1 = make_repo(tmp_path, {"src/API/v/endpoint.ts": (ts / "endpoint.ts").read_text(),
                                    "src/API/v/index.ts": (ts / "index.ts").read_text()})
    dbp = tmp_path / "a.sqlite"
    build.build_index("front", repo, "ts", s1, dbp)
    return repo, s1, dbp


def _state(dbp):
    conn = db.connect(dbp)
    st = (db.get_meta(conn, "sha"), conn.execute("SELECT count(*) FROM symbols").fetchone()[0],
          conn.execute("SELECT count(*) FROM sym_fts").fetchone()[0], conn.execute("SELECT count(*) FROM failures").fetchone()[0])
    conn.close()
    return st


def _flaky_read(real):
    def read(repo, sha, paths):
        for i, item in enumerate(real(repo, sha, paths)):
            if i == 1:
                raise build.gitsrc.GitError("boom")
            yield item
    return read


def test_unreadable_blob_recorded_as_failure(tmp_path, monkeypatch):
    repo, s1 = make_repo(tmp_path, {"src/A.php": "<?php class A {}"})
    monkeypatch.setattr(build.gitsrc, "read_files", lambda r, s, paths: iter([(p, None) for p in paths]))
    r = build.build_index("x", repo, "php", s1, tmp_path / "i.sqlite")
    assert r["failures"] == 1
    conn = db.connect(tmp_path / "i.sqlite")
    assert conn.execute("SELECT error FROM failures").fetchone()[0] == "unreadable blob"


def test_insert_error_leaves_no_half_indexed_file(tmp_path, monkeypatch):
    repo, s1 = make_repo(tmp_path, {"src/A.php": "<?php class A {}", "src/B.php": "<?php class B {}"})
    real = build._insert

    def flaky(conn, repo_name, path, symbols):
        real(conn, repo_name, path, symbols[:1])
        if path.endswith("B.php"):
            raise RuntimeError("insert died")
    monkeypatch.setattr(build, "extract_php", lambda path, text: [build.Symbol("class", "X", "X", 1, "", {}), build.Symbol("method", "m", "X::m", 2, "", {})])
    monkeypatch.setattr(build, "_insert", flaky)
    r = build.build_index("x", repo, "php", s1, tmp_path / "i.sqlite")
    conn = db.connect(tmp_path / "i.sqlite")
    assert r["failures"] == 1
    assert {x[0] for x in conn.execute("SELECT file FROM symbols")} == {"src/A.php"}
    assert conn.execute("SELECT count(*) FROM sym_fts").fetchone()[0] == conn.execute("SELECT count(*) FROM symbols").fetchone()[0]


def test_full_flag_removes_wal_files(tmp_path, monkeypatch):
    import index_repo
    repo, s1 = make_repo(tmp_path, {"src/A.php": "<?php class A {}"})
    dbp = tmp_path / "idx.sqlite"
    monkeypatch.setattr(index_repo.qpaths, "repo_cfg", lambda n: {"path": repo, "ref": "master", "lang": "php"})
    monkeypatch.setattr(index_repo.qpaths, "index_path", lambda n: dbp)
    index_repo.refresh("x")
    Path(str(dbp) + "-wal").write_bytes(b"stale")
    Path(str(dbp) + "-shm").write_bytes(b"stale")
    real = index_repo.build.build_index
    seen = {}

    def spy(*a, **k):
        seen["files"] = {suf: Path(str(a[4]) + suf).exists() for suf in ("", "-wal", "-shm")}
        seen["db"] = Path(a[4])
        return real(*a, **k)
    monkeypatch.setattr(index_repo.build, "build_index", spy)
    r = index_repo.refresh("x", full=True)
    assert seen["files"] == {"": False, "-wal": False, "-shm": False} and seen["db"] != dbp  # built fresh, off to the side
    assert not Path(str(dbp) + "-wal").exists() or Path(str(dbp) + "-wal").read_bytes() != b"stale"
    assert r["mode"] == "full" and r["failures"] == 0
    assert db.get_meta(db.connect(dbp), "sha") == s1


def test_refresh_fetch_failure_warns_and_builds(tmp_path, monkeypatch):
    import subprocess
    import index_repo
    repo, s1 = make_repo(tmp_path, {"src/A.php": "<?php class A {}"})
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(tmp_path / "nonexistent")], check=True)
    dbp = tmp_path / "idx.sqlite"
    monkeypatch.setattr(index_repo.qpaths, "repo_cfg", lambda n: {"path": repo, "ref": "master", "lang": "php"})
    monkeypatch.setattr(index_repo.qpaths, "index_path", lambda n: dbp)
    r = index_repo.refresh("x", fetch=True)
    assert "fetch failed" in r["warning"]
    assert r["mode"] == "full" and r["sha"] == s1


def jfx(group, name):
    return (FX / "java" / group / name).read_text()


def test_should_index_java_filters():
    ok = ["src/main/java/com/example/A.java", "src/main/resources/application.yml", "src/main/resources/application-dev.properties",
          "src/main/resources/application.yaml", "src/main/resources/db/migration/V1__x.sql",
          "src/main/resources/db/changelog/changelog-master.xml", "svc/src/main/java/B.java"]
    no = ["src/test/java/com/example/ATest.java", "build/generated/A.java", ".gradle/x/A.java", "graphify-out/A.java",
          "src/main/resources/messages/messages.yml", "src/main/resources/monitoring/otel.properties", "gradle.properties",
          "src/main/java/com/example/README.md", "src/main/resources/application-dev.txt"]
    assert all(build.should_index(p, "java") for p in ok)
    assert not any(build.should_index(p, "java") for p in no)
    assert build.should_index("src/main/java/com/example/CronConfig.java", "java")  # 'cron' in a name is not a crontab
    assert not build.should_index("src/main/java/com/example/A.java", "php")


def test_extractor_version_bump_forces_full_rebuild_at_same_sha(tmp_path, monkeypatch):
    # fake credential: an index built by an older extractor holds it verbatim until a version bump re-extracts
    repo, s1 = make_repo(tmp_path, {"src/K.php": "<?php\nclass K {\n    const API_KEY = 'fake-key-value';\n}\n"})
    dbp = tmp_path / "i.sqlite"
    import qindex.php as php_mod
    monkeypatch.setattr(php_mod, "redact_symbols", lambda syms: syms)
    assert build.build_index("x", repo, "php", s1, dbp)["mode"] == "full"
    conn = db.connect(dbp)
    assert "fake-key-value" in conn.execute("SELECT signature FROM symbols WHERE name='API_KEY'").fetchone()[0]
    assert db.get_meta(conn, "extractor_version") == build.EXTRACTOR_VERSION
    conn.close()
    monkeypatch.undo()
    assert build.build_index("x", repo, "php", s1, dbp)["mode"] == "noop"  # same sha, same version
    monkeypatch.setattr(build, "EXTRACTOR_VERSION", build.EXTRACTOR_VERSION + "-next")
    r = build.build_index("x", repo, "php", s1, dbp)
    assert r["mode"] == "full" and r["files"] == 1
    conn = db.connect(dbp)
    assert conn.execute("SELECT signature FROM symbols WHERE name='API_KEY'").fetchone()[0] == "API_KEY = <redacted>"
    assert db.get_meta(conn, "extractor_version") == build.EXTRACTOR_VERSION


def test_index_without_extractor_version_is_rebuilt_in_full(tmp_path):
    repo, s1 = make_repo(tmp_path, {"src/A.php": "<?php class A {}"})
    dbp = tmp_path / "i.sqlite"
    build.build_index("x", repo, "php", s1, dbp)
    conn = db.connect(dbp)
    conn.execute("DELETE FROM meta WHERE key='extractor_version'")
    conn.commit()
    conn.close()
    s2 = commit(repo, {"src/B.php": "<?php class B {}"})
    r = build.build_index("x", repo, "php", s2, dbp)
    assert r["mode"] == "full" and r["files"] == 2  # not incremental: old rows came from an unknown extractor


def test_full_rebuild_failure_keeps_the_old_index(tmp_path, monkeypatch):
    import index_repo
    repo, s1 = make_repo(tmp_path, {"src/A.php": "<?php\nclass A {}\n"})
    monkeypatch.setattr(index_repo.qpaths, "repo_cfg", lambda n: {"path": repo, "ref": "master", "lang": "php"})
    dbp = tmp_path / "idx" / "x.sqlite"
    monkeypatch.setattr(index_repo.qpaths, "index_path", lambda n: dbp)
    index_repo.refresh("x")
    assert dbp.exists()

    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(index_repo.build, "build_index", boom)
    try:
        index_repo.refresh("x", full=True)
        raise AssertionError("expected the failure to surface")
    except RuntimeError:
        pass
    conn = db.connect(dbp)
    assert conn.execute("SELECT name FROM symbols").fetchall()[0][0] == "A"  # the old index survived
    assert sorted(p.name for p in dbp.parent.iterdir() if not p.name.startswith("x.sqlite")) == []  # no temp left behind


def test_full_rebuild_replaces_atomically(tmp_path, monkeypatch):
    import index_repo
    repo, s1 = make_repo(tmp_path, {"src/A.php": "<?php\nclass A {}\n"})
    monkeypatch.setattr(index_repo.qpaths, "repo_cfg", lambda n: {"path": repo, "ref": "master", "lang": "php"})
    dbp = tmp_path / "idx" / "x.sqlite"
    monkeypatch.setattr(index_repo.qpaths, "index_path", lambda n: dbp)
    index_repo.refresh("x")
    commit(repo, {"src/B.php": "<?php\nclass B {}\n"})
    r = index_repo.refresh("x", full=True)
    assert r["mode"] == "full" and r["files"] == 2
    names = sorted(x[0] for x in db.connect(dbp).execute("SELECT name FROM symbols"))
    assert names == ["A", "B"]
    assert not [p for p in dbp.parent.iterdir() if ".tmp" in p.name]
