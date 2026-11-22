import json

import pytest

from conftest import MEETINGS, FixedRunner, gold
from pmo.extract import analyze, latest_version_id
from pmo.ingest import ingest_files
from pmo.review import NEW_ITEM, ReviewError, adopt, reject


def run(conn, sid):
    vid = latest_version_id(conn, sid)
    analyze(conn, vid, FixedRunner(json.dumps(gold(sid), ensure_ascii=False)))
    return [dict(r) for r in conn.execute("SELECT * FROM extractions WHERE version_id=? ORDER BY extraction_id", (vid,))]


def test_adopt_links_followup_and_records_changes(conn):
    ingest_files(conn, MEETINGS)
    e1 = run(conn, "sample-alpha-01")
    todo_id = adopt(conn, e1[2]["extraction_id"])            # 佐藤さん 帳票一覧 10/9
    e2 = run(conn, "sample-alpha-02")
    assert e2[0]["matched_item_id"] == todo_id                # 次の回の「完了」は同じ項目の候補
    assert adopt(conn, e2[0]["extraction_id"]) == todo_id
    item = conn.execute("SELECT * FROM items WHERE item_id=?", (todo_id,)).fetchone()
    assert item["status"] == "完了"
    assert item["due"] == "10/9"                               # 「不明」で上書きしない
    events = [r["event_type"] for r in conn.execute("SELECT event_type FROM item_events WHERE item_id=?", (todo_id,))]
    assert events == ["created", "mentioned", "status_changed"]
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_adopt_twice_and_reject(conn):
    ingest_files(conn, MEETINGS)
    e = run(conn, "sample-gamma-01")
    a = adopt(conn, e[0]["extraction_id"])
    assert adopt(conn, e[0]["extraction_id"]) == a
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    reject(conn, e[1]["extraction_id"], "テスト")
    with pytest.raises(ReviewError):
        reject(conn, e[0]["extraction_id"])
    assert conn.execute("SELECT decision FROM reviews ORDER BY review_id").fetchall()[-1][0] == "rejected"


def test_force_new_item_and_cross_project_guard(conn):
    ingest_files(conn, MEETINGS)
    a1 = run(conn, "sample-alpha-01")
    t = adopt(conn, a1[3]["extraction_id"])                   # データ移行方針
    a2 = run(conn, "sample-alpha-02")
    assert a2[1]["matched_item_id"] == t
    new_id = adopt(conn, a2[1]["extraction_id"], link_to=NEW_ITEM)
    assert new_id != t
    b = run(conn, "sample-beta-01")
    with pytest.raises(ReviewError):
        adopt(conn, b[1]["extraction_id"], link_to=t)        # 別プロジェクトとは結ばない


def test_invalid_cannot_be_adopted(conn):
    ingest_files(conn, MEETINGS)
    vid = latest_version_id(conn, "sample-alpha-01")
    bad = {"items": [{"item_type": "todo", "content": "x", "quote": "原文に無い文", "classification": "fact"}]}
    analyze(conn, vid, FixedRunner(json.dumps(bad, ensure_ascii=False)))
    eid = conn.execute("SELECT extraction_id FROM extractions").fetchone()[0]
    with pytest.raises(ReviewError):
        adopt(conn, eid)
