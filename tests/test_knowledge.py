import pytest

from conftest import MEETINGS, SAMPLES
from pmo import settings_store
from pmo.ingest import ingest_files
from pmo.knowledge import import_folder, import_note_export, search, search_all

KDIR = SAMPLES / "knowledge"
NOTE = SAMPLES / "note_export_sample.xml"


def test_import_folder_and_reimport(conn):
    res = import_folder(conn, KDIR, data_kind="test")
    assert res["status"] == "success" and res["new"] == 5
    assert import_folder(conn, KDIR, data_kind="test")["unchanged"] == 5
    rule = conn.execute("SELECT * FROM knowledge_docs WHERE title LIKE '販売管理刷新%'").fetchone()
    assert rule["project"] == "販売管理刷新" and rule["info_class"] == "project_rule"


def test_note_export(conn):
    res = import_note_export(conn, NOTE, data_kind="test")
    assert res["new"] == 2 and res["skipped_empty"] == 1          # 本文が無い記事は取り込まない
    assert res["statuses"] == {"publish": 2, "draft": 1}
    d = conn.execute("SELECT * FROM knowledge_docs WHERE kind='note' ORDER BY doc_id").fetchall()
    assert all(r["info_class"] == "personal_opinion" for r in d)
    assert "（draft）" in d[1]["title"]
    bad = import_note_export(conn, KDIR / "issue_management.md", data_kind="test")
    assert bad["status"] == "failed"


def test_real_data_blocked(conn, tmp_path):
    with pytest.raises(settings_store.RealDataDisabled):
        import_folder(conn, KDIR, data_kind="real")
    with pytest.raises(settings_store.RealDataDisabled):
        import_note_export(conn, NOTE, data_kind="real")
    assert conn.execute("SELECT COUNT(*) FROM knowledge_docs").fetchone()[0] == 0


def test_search_fts_like_and_flags(conn):
    import_folder(conn, KDIR, data_kind="test")
    import_note_export(conn, NOTE, data_kind="test")
    hits = search(conn, "担当者と期限")
    assert hits and hits[0]["title"].startswith("課題管理表")
    assert search(conn, "結論")                                    # 2文字は LIKE で探す
    rule = search(conn, "田中部長の承認", project="経費精算クラウド導入")
    assert rule and "このプロジェクトには当てはめない" in rule[0]["flags"][0]
    assert search(conn, "田中部長の承認", project="経費精算クラウド導入", exclude_other_projects=True) == []
    old = search(conn, "成果物ごとに分け")
    assert any("古い可能性" in f for f in old[0]["flags"])
    assert search(conn, "担当が未定")[0]["kind"] == "note"


def test_search_all(conn):
    ingest_files(conn, MEETINGS)
    import_folder(conn, KDIR, data_kind="test")
    r = search_all(conn, "データ移行")
    assert len(r["minutes"]) >= 2 and "データ移行" in r["minutes"][0]["snippet"]
    r2 = search_all(conn, "データ移行", project="経費精算クラウド導入")
    assert r2["minutes"] == []
