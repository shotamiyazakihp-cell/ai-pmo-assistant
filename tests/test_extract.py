import json

from conftest import MEETINGS, FixedRunner, gold
from pmo.extract import analyze, analyze_pasted, build_prompt, latest_version_id, parse_output
from pmo.ingest import ingest_files
from pmo.review import adopt


def setup(conn):
    ingest_files(conn, MEETINGS)
    return latest_version_id(conn, "sample-alpha-01")


def out(items):
    return json.dumps({"meeting": {"title": "t"}, "items": items}, ensure_ascii=False)


def item(**kw):
    base = {"item_type": "todo", "content": "現行帳票の一覧を作成する", "owner": "不明", "due": "不明",
            "status": "未着手", "classification": "fact",
            "quote": "佐藤さんが現行帳票の一覧を10/9までに作成する。", "same_as_item_id": None}
    base.update(kw)
    return base


def rows(conn, vid):
    return [dict(r) for r in conn.execute("SELECT * FROM extractions WHERE version_id=? ORDER BY extraction_id", (vid,))]


def test_prompt_contains_rules_and_body(conn):
    vid = setup(conn)
    p = build_prompt(conn, vid)
    assert "SOURCE_ID: sample-alpha-01" in p
    assert "推測で埋めない" in p
    assert "佐藤さんが現行帳票の一覧を10/9までに作成する。" in p


def test_valid_item_saved_with_due_date(conn):
    vid = setup(conn)
    res = analyze(conn, vid, FixedRunner(out([item(owner="佐藤", due="10/9")])))
    assert res["status"] == "success" and res["saved"] == 1
    e = rows(conn, vid)[0]
    assert e["owner"] == "佐藤" and e["due"] == "10/9" and e["due_date"] == "2026-10-09"
    assert e["review_status"] == "pending" and e["quote_offset"] is not None


def test_quote_not_in_source_is_invalid(conn):
    vid = setup(conn)
    res = analyze(conn, vid, FixedRunner(out([item(quote="佐藤さんが来月までにシステムを完成させる。")])))
    assert res["invalid"] == 1
    e = rows(conn, vid)[0]
    assert e["review_status"] == "invalid" and "引用が原文に見つからない" in e["notes"]


def test_owner_and_due_without_evidence_become_unknown(conn):
    vid = setup(conn)
    # 担当・期限を作り話した返事
    analyze(conn, vid, FixedRunner(out([item(content="データ移行方針を整理する", owner="山本", due="10/20",
                                             quote="データ移行方針を誰かが整理する必要がある。")])))
    e = rows(conn, vid)[0]
    assert e["owner"] == "不明" and e["due"] == "不明" and e["due_date"] is None
    assert e["review_status"] == "pending"
    notes = json.loads(e["notes"])
    assert any("担当「山本」" in n for n in notes) and any("期限「10/20」" in n for n in notes)


def test_bad_type_and_classification(conn):
    vid = setup(conn)
    analyze(conn, vid, FixedRunner(out([item(item_type="memo"), item(classification="guess")])))
    r = rows(conn, vid)
    assert r[0]["review_status"] == "invalid"
    assert r[1]["classification"] == "ai_inference"


def test_parse_output_variants():
    assert parse_output('前置き\n```json\n{"items": []}\n```')["items"] == []
    assert parse_output('はい {"items": [], "meeting": {}} です')["items"] == []


def test_broken_output_is_failed_not_success(conn):
    vid = setup(conn)
    for bad in ["すみません、分析できませんでした", '{"items": "x"}', "{壊れた"]:
        res = analyze(conn, vid, FixedRunner(bad))
        assert res["status"] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE kind='analyze' AND status='failed'").fetchone()[0] == 3
    assert conn.execute("SELECT analyzed_at FROM source_versions WHERE version_id=?", (vid,)).fetchone()[0] is None


def test_reanalysis_does_not_increase(conn):
    vid = setup(conn)
    g = json.dumps(gold("sample-alpha-01"), ensure_ascii=False)
    analyze(conn, vid, FixedRunner(g))
    n1 = len(rows(conn, vid))
    # 1件採用してから再処理（言い回しが少し違う返事）
    adopt(conn, rows(conn, vid)[0]["extraction_id"])
    data = gold("sample-alpha-01")
    data["items"][0]["content"] = "要件定義は10月末までに完了とする。"
    analyze(conn, vid, FixedRunner(json.dumps(data, ensure_ascii=False)))
    analyze(conn, vid, FixedRunner(g))
    r = rows(conn, vid)
    assert len(r) == n1
    assert sum(1 for x in r if x["review_status"] == "adopted") == 1


def test_manual_paste(conn):
    vid = setup(conn)
    assert analyze_pasted(conn, vid, json.dumps(gold("sample-alpha-01"), ensure_ascii=False))["status"] == "success"
    assert analyze_pasted(conn, vid, "")["status"] == "failed"


def test_ai_same_as_other_project_is_ignored(conn):
    ingest_files(conn, MEETINGS)
    vb = latest_version_id(conn, "sample-beta-01")
    analyze(conn, vb, FixedRunner(json.dumps(gold("sample-beta-01"), ensure_ascii=False)))
    beta_item = adopt(conn, rows(conn, vb)[1]["extraction_id"])  # 経費精算の TODO
    va = latest_version_id(conn, "sample-alpha-01")
    analyze(conn, va, FixedRunner(out([item(same_as_item_id=beta_item)])))
    e = rows(conn, va)[0]
    assert e["matched_item_id"] is None
    assert "同じプロジェクト・種類に無い" in e["notes"]
