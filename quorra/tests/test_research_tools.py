import json
import math
import research_tools as rt
import store
from gitfix import commit, make_repo
from pathlib import Path


def rows(tiers):
    return [{"id": f"conv_{i}", "tier": t} for i, t in enumerate(tiers)]


def test_sample_includes_all_weak_tiers_and_30pct_of_rest_deterministically():
    rs = rows(["transitional", "stated_only"] + ["dominant"] * 10)
    s1 = rt.pick_sample(rs, "run-a")
    assert {"conv_0", "conv_1"} <= set(s1)
    assert len(s1) == 2 + 3                      # ceil(0.3 * 10)
    assert rt.pick_sample(rs, "run-a") == s1     # deterministic per run
    assert len(rt.pick_sample(rs, "run-b")) == 5


def test_status_counts_commits_and_suggests_rerun(tmp_path):
    repo, s1 = make_repo(tmp_path, {"a.php": "1"})
    for i in range(3):
        commit(repo, {f"f{i}.php": "1"})
    conn = store.connect()
    store.upsert(conn, "convention", {"repo": "x", "area": "naming", "rule": "r", "tier": "dominant", "source": "code",
                                      "checked_at_sha": s1})
    [st] = rt.status(conn, {"x": {"path": repo, "ref": "master"}}, threshold=2)
    assert st["commits_since"] == 3 and st["suggest_rerun"] is True and st["research_sha"] == s1


def test_status_flags_refuted_in_use(tmp_path):
    repo, s1 = make_repo(tmp_path, {"a.php": "1"})
    conn = store.connect()
    store.upsert(conn, "convention", {"repo": "x", "area": "naming", "rule": "r", "tier": "dominant", "source": "code",
                                      "checked_at_sha": s1}, tags=["refuted-in-use"])
    [st] = rt.status(conn, {"x": {"path": repo, "ref": "master"}})
    assert st["refuted_in_use"] == 1 and st["suggest_rerun"] is True


def test_diff_rules():
    old = [{"area": "a", "rule": "r1", "tier": "dominant"}, {"area": "a", "rule": "r2", "tier": "dominant"}]
    new = [{"area": "a", "rule": "r1", "tier": "transitional"}, {"area": "b", "rule": "r3", "tier": "enforced"}]
    d = rt.diff_rules(old, new)
    assert d["new"] == [{"area": "b", "rule": "r3", "tier": "enforced"}]
    assert d["missing"] == [{"area": "a", "rule": "r2", "tier": "dominant"}]
    assert d["tier_changed"] == [{"area": "a", "rule": "r1", "old": "dominant", "new": "transitional"}]


def test_snapshot_creates_json_with_id_area_rule_tier(tmp_path):
    repo, s1 = make_repo(tmp_path, {"a.php": "1"})
    conn = store.connect()
    store.upsert(conn, "convention", {"repo": "x", "area": "naming", "rule": "r1", "tier": "dominant", "source": "code",
                                      "checked_at_sha": s1})
    snap = rt.snapshot(conn, "x")
    assert len(snap) == 1
    assert snap[0]["id"] == snap[0]["id"]  # just verify id is present
    assert snap[0]["area"] == "naming"
    assert snap[0]["rule"] == "r1"
    assert snap[0]["tier"] == "dominant"


def test_snapshot_diff_after_upsert(tmp_path):
    repo, s1 = make_repo(tmp_path, {"a.php": "1"})
    conn = store.connect()
    # Insert initial rules
    store.upsert(conn, "convention", {"repo": "x", "area": "a", "rule": "r1", "tier": "dominant", "source": "code",
                                      "checked_at_sha": s1})
    store.upsert(conn, "convention", {"repo": "x", "area": "a", "rule": "r2", "tier": "dominant", "source": "code",
                                      "checked_at_sha": s1})
    snap = rt.snapshot(conn, "x")

    # Now upsert: change tier, add new rule, supersede old rule
    store.upsert(conn, "convention", {"repo": "x", "area": "a", "rule": "r1", "tier": "transitional", "source": "code",
                                      "checked_at_sha": s1})  # tier change
    store.upsert(conn, "convention", {"repo": "x", "area": "b", "rule": "r3", "tier": "enforced", "source": "code",
                                      "checked_at_sha": s1})  # new rule
    store.upsert(conn, "convention", {"repo": "x", "area": "a", "rule": "r2", "tier": "dominant", "source": "code",
                                      "checked_at_sha": s1, "superseded_by": "new-id"})  # supersede

    new = rt.snapshot(conn, "x")
    d = rt.diff_rules(snap, new)
    assert d["tier_changed"] == [{"area": "a", "rule": "r1", "old": "dominant", "new": "transitional"}]
    assert d["new"] == [{"area": "b", "rule": "r3", "tier": "enforced"}]
    assert d["missing"] == [{"area": "a", "rule": "r2", "tier": "dominant"}]


def test_status_picks_oldest_checked_at_sha(tmp_path):
    repo, s1 = make_repo(tmp_path, {"a.php": "1"})
    for i in range(2):
        commit(repo, {f"f{i}.php": "1"})
    s3 = commit(repo, {"f3.php": "1"})

    conn = store.connect()
    # Insert two rows with different shas; s3 is updated last but s1 is older
    store.upsert(conn, "convention", {"repo": "x", "area": "a", "rule": "r1", "tier": "dominant", "source": "code",
                                      "checked_at_sha": s1})
    store.upsert(conn, "convention", {"repo": "x", "area": "a", "rule": "r2", "tier": "dominant", "source": "code",
                                      "checked_at_sha": s3})

    [st] = rt.status(conn, {"x": {"path": repo, "ref": "master"}})
    assert st["research_sha"] == s1  # oldest, not s3


def test_status_detects_diverged_research_sha(tmp_path):
    repo, s1 = make_repo(tmp_path, {"a.php": "1"})
    # Create a side branch with a commit
    import subprocess
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "side"], check=True)
    commit(repo, {"side.php": "1"})
    side_sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
    # Back to master
    subprocess.run(["git", "-C", str(repo), "checkout", "master"], check=True)

    conn = store.connect()
    store.upsert(conn, "convention", {"repo": "x", "area": "a", "rule": "r", "tier": "dominant", "source": "code",
                                      "checked_at_sha": side_sha})

    [st] = rt.status(conn, {"x": {"path": repo, "ref": "master"}})
    assert st["diverged"] is True
    assert st["suggest_rerun"] is True


def test_status_detects_unknown_sha(tmp_path):
    repo, s1 = make_repo(tmp_path, {"a.php": "1"})
    conn = store.connect()
    store.upsert(conn, "convention", {"repo": "x", "area": "a", "rule": "r", "tier": "dominant", "source": "code",
                                      "checked_at_sha": "0000000000000000000000000000000000000000"})

    [st] = rt.status(conn, {"x": {"path": repo, "ref": "master"}})
    assert st["unknown_sha"] is True
    assert st["suggest_rerun"] is True


def test_status_detects_never_researched(tmp_path):
    repo, s1 = make_repo(tmp_path, {"a.php": "1"})
    conn = store.connect()
    store.upsert(conn, "convention", {"repo": "x", "area": "a", "rule": "r", "tier": "dominant", "source": "code"})

    [st] = rt.status(conn, {"x": {"path": repo, "ref": "master"}})
    assert st["never_researched"] is True
    assert st["suggest_rerun"] is True


def test_sample_differs_for_different_run_ids_and_works_with_empty_rest():
    # Test with ≥ 20 rest rows
    rs = [{"id": f"weak_{i}", "tier": "transitional"} if i < 2 else {"id": f"rest_{i}", "tier": "dominant"}
          for i in range(25)]
    s1 = rt.pick_sample(rs, "run-a")
    s2 = rt.pick_sample(rs, "run-b")
    assert len(s1) == 2 + math.ceil(0.3 * 23)  # 2 weak + 30% of 23 rest
    assert s1 != s2  # different runs should differ

    # Test with empty rest
    rs_weak_only = [{"id": f"weak_{i}", "tier": "stated_only"} for i in range(5)]
    s3 = rt.pick_sample(rs_weak_only, "run-c")
    assert s3 == [f"weak_{i}" for i in range(5)]  # all weak, no rest
