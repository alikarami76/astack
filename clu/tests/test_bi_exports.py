import json

import bi_exports
import store


def test_scan_and_index(kbenv, tmp_path):
    day = tmp_path / "inbox" / "2026-09-27"
    day.mkdir(parents=True)
    (day / "Flash Sale Timeline.csv").write_text("date,gmv\n2026-09-20,10\n2026-09-21,12\n")
    (day / "Sales Daily - page 1.png").write_bytes(b"png")
    (day / "notes.txt").write_text("ignored")
    items = bi_exports.scan(tmp_path / "inbox")
    assert [i["kind"] for i in items] == ["csv", "png"]
    assert items[0]["rows"] == 2 and items[0]["columns"] == ["date", "gmv"]
    c = store.connect()
    assert bi_exports.index(c, tmp_path / "inbox") == 2
    hit = store.get(c, store.search(c, "Flash Sale Timeline", etype="source")[0]["id"])
    assert hit["kind"] == "bi-export" and "rows 2" in hit["notes"]
    assert "bi-export" in json.loads(hit["tags"])


def test_missing_inbox_is_empty(tmp_path):
    assert bi_exports.scan(tmp_path / "nope") == []
