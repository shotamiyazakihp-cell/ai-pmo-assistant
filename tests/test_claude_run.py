"""2026-11-22 の盲検の抽出（samples/claude_run_20261122）を流し直し、合格基準を確かめる。"""
from conftest import MEETINGS, SAMPLES
from pmo.evaluate import evaluate
from pmo.extract import analyze_pasted
from pmo.ingest import ingest_files

RUN = SAMPLES / "claude_run_20261122"


def test_blind_claude_run_meets_criteria(conn):
    ingest_files(conn, MEETINGS)
    for r in conn.execute("SELECT version_id, source_id FROM source_versions").fetchall():
        res = analyze_pasted(conn, r[0], (RUN / f"{r[1]}.json").read_text(encoding="utf-8"))
        assert res["status"] == "success"
    rep = evaluate(conn)
    assert rep["sources_missing"] == [] and rep["duplicate_versions"] == 0
    assert rep["invalid_items"] == 0              # 引用はすべて原文にある
    assert rep["fabricated_owner_or_due"] == 0    # 引用に根拠の無い担当・期限は無い
    assert rep["match_rate"] >= 0.9               # 正解データとの一致（35/38）
