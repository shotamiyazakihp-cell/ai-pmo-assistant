"""Phase 3〜5（実務支援・自動化・改善）を試験用データで確かめる。"""
import json
from datetime import date

import pytest

from conftest import MEETINGS, SAMPLES, FixedRunner
from pmo import settings_store
from pmo.automate import daily_job, hourly, pending_versions, render_daily
from pmo.config import Settings
from pmo.evaluate import GoldRunner
from pmo.extract import analyze
from pmo.ingest import ingest_files
from pmo.knowledge import import_folder
from pmo.practice import (analyze_project, build_doc_prompt, daily, export_table, generate_document,
                          project_status, set_document_status, set_finding_status)
from pmo.quality import add_missed, metrics, suggestions
from pmo.review import adopt, reject

TODAY = date(2026, 11, 4)
ALPHA, BETA = "販売管理刷新", "経費精算クラウド導入"


@pytest.fixture
def full(conn):
    ingest_files(conn, MEETINGS)
    import_folder(conn, SAMPLES / "knowledge", data_kind="test")
    for (vid,) in conn.execute("SELECT version_id FROM source_versions").fetchall():
        analyze(conn, vid, GoldRunner())
    for (eid,) in conn.execute("""SELECT e.extraction_id FROM extractions e JOIN source_versions v USING(version_id)
                                  JOIN sources s USING(source_id) ORDER BY s.meeting_date, e.extraction_id""").fetchall():
        adopt(conn, eid)
    return conn


def item_id(conn, text):
    return conn.execute("SELECT item_id FROM items WHERE content LIKE ?", (f"%{text}%",)).fetchone()[0]


def test_daily(full):
    d = daily(full, TODAY)
    assert [i["content"] for i in d["overdue"]] == ["本稼働の時期を経理部長に確認する"]   # 10/16 期限・未完了
    assert d["overdue"][0]["days_over"] == 19
    assert [i["content"] for i in d["due_soon"]] == ["データ移行方針を整理する"]           # 11/6
    assert any("添付ファイルの容量を確認" in i["content"] for i in d["due_unknown"]) is False  # 「なるべく早く」は不明ではない
    assert {i["content"] for i in d["open_questions"]} >= {"交通費の精算をラクケイで扱うかどうか"}
    assert all(i["status"] != "完了" for k in d for i in d[k])
    assert any(i["content"] == "本稼働の時期を経理部長に確認する" for i in d["stalled"])     # 10/14 から話題なし


def test_project_status_has_no_invented_progress(full):
    st = project_status(full, ALPHA, TODAY)
    assert st["progress_rate"].startswith("不明")
    assert st["counts"]["todo"] == {"open": 1, "closed": 2}
    assert st["overdue"] == 0 and len(st["recent_meetings"]) == 4


def test_export_table(full):
    csv_text = export_table(full, "issue", ALPHA)
    assert csv_text.startswith("﻿番号,プロジェクト")
    assert "現行システムの仕様書が古い" in csv_text and "添付ファイル" not in csv_text


def test_document_prompt_and_checks(full):
    prompt, meta = build_doc_prompt(full, "weekly_report", ALPHA, TODAY)
    assert "進捗率：不明" in prompt and "[K1]" in prompt
    assert "田中部長の承認" in prompt                      # 同じプロジェクトのルールは使う
    p2, _ = build_doc_prompt(full, "weekly_report", BETA, TODAY)
    assert "田中部長の承認" not in p2                       # 別プロジェクトのルールは渡さない
    good = item_id(full, "データ移行方針")
    body = f"# 週次報告\n- データ移行方針は山本さんが対応 [#{good}] [K1]\n- 進捗は80%\n- 謎の項目 [#9999]"
    res = generate_document(full, "weekly_report", ALPHA, FixedRunner(body), TODAY)
    assert res["status"] == "success"
    doc = full.execute("SELECT * FROM documents WHERE document_id=?", (res["document_id"],)).fetchone()
    w = json.loads(doc["warnings"])
    assert any("9999" in x for x in w) and any("%" in x for x in w)
    set_document_status(full, doc["document_id"], "approved", body.replace("80%", "不明"))
    with pytest.raises(ValueError):
        build_doc_prompt(full, "no_such", ALPHA, TODAY)


def test_findings_validation(full):
    a = item_id(full, "要件定義は10月末")
    b = item_id(full, "承認フローの現状")                    # 別プロジェクト
    out = json.dumps({"findings": [
        {"finding_type": "inconsistency", "text": "要件定義の期限が延びた", "evidence_item_ids": [a], "classification": "ai_inference"},
        {"finding_type": "risk", "text": "根拠なし", "evidence_item_ids": [], "classification": "ai_inference"},
        {"finding_type": "risk", "text": "別プロジェクト", "evidence_item_ids": [b], "classification": "ai_inference"},
        {"finding_type": "unknown", "text": "種類が不正", "evidence_item_ids": [a]},
    ]}, ensure_ascii=False)
    res = analyze_project(full, ALPHA, FixedRunner(out), TODAY)
    assert res["status"] == "success" and res["saved"] == 1 and res["dropped"] == 3
    fid = full.execute("SELECT finding_id FROM findings").fetchone()[0]
    set_finding_status(full, fid, "accepted", "妥当")
    assert analyze_project(full, ALPHA, FixedRunner("分析できません"), TODAY)["status"] == "failed"


def test_real_project_not_sent_to_claude(full):
    full.execute("UPDATE sources SET data_kind='real' WHERE project=?", (BETA,))
    r = FixedRunner("x")
    assert generate_document(full, "weekly_report", BETA, r, TODAY)["status"] == "failed"
    assert analyze_project(full, BETA, r, TODAY)["status"] == "failed"
    assert r.prompts == []


def test_hourly_and_daily(conn, tmp_path):
    s = Settings(tmp_path / "x.db", None, None, "p", "d", "real", None, None)
    assert hourly(conn, s)["status"] == "skipped"
    s2 = Settings(tmp_path / "x.db", "tok", "db", "p", "d", "real", None, None)
    assert "実データ" in hourly(conn, s2)["reason"]           # 本物は止める
    ingest_files(conn, MEETINGS)
    assert len(pending_versions(conn)) == 10
    res = daily_job(conn, TODAY, tmp_path / "reports", runner=GoldRunner())
    assert res["analyzed"] == 10 and res["status"] == "success"
    assert pending_versions(conn) == []
    text = (tmp_path / "reports" / "daily_2026-11-04.md").read_text(encoding="utf-8")
    assert "確認待ちの抽出候補：38 件" in text
    assert daily_job(conn, TODAY, tmp_path / "reports", runner=GoldRunner())["analyzed"] == 0   # 何度動かしても増えない


def test_render_daily(full):
    text = render_daily(full, TODAY)
    assert "期限超過の TODO：1 件" in text and "19日超過" in text


def test_quality(full):
    eid = full.execute("SELECT extraction_id FROM extractions WHERE review_status='adopted' LIMIT 1").fetchone()[0]
    m = metrics(full)
    assert m["adoption_rate"] == 1.0 and m["invalid_rate"] == 0.0
    add_missed(full, "sample-alpha-03", "帳票の出力形式の希望を聞く宿題が抜けた")
    with pytest.raises(ValueError):
        add_missed(full, "no-such", "x")
    s = suggestions(full)
    assert any("見落とし" in x for x in s)
    settings_store.change(full, "stale_days", "21", "定例が隔週のため", "放置の誤検知が減るか2週間後に確認")
    assert full.execute("SELECT reason FROM rule_changes").fetchone()[0] == "定例が隔週のため"
    assert settings_store.get_int(full, "stale_days") == 21
    with pytest.raises(ValueError):
        settings_store.change(full, "match_threshold", "2", "誤り")
