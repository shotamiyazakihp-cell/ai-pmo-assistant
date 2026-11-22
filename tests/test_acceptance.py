"""Phase 1 の完了の条件のうち、自動で確かめられるものを1本で通す。
（Claude の代わりに正解データを返す実行役を使う。本物の Claude での一致率は PC で evaluate を使って確かめる）"""
import json
import sqlite3

from conftest import MEETINGS, SAMPLES, gold
from pmo.evaluate import GoldRunner, evaluate
from pmo.extract import analyze
from pmo.ingest import ingest_files
from pmo.review import adopt


def analyze_all(conn, runner):
    for r in conn.execute("SELECT MAX(version_id) v FROM source_versions GROUP BY source_id").fetchall():
        assert analyze(conn, r["v"], runner)["status"] == "success"


def counts(conn):
    return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("sources", "source_versions", "extractions", "items", "item_events"))


def test_phase1_acceptance(conn):
    # 1) 取り込み：10件、漏れ0・重複0（重複ファイルを混ぜても増えない）
    ingest_files(conn, MEETINGS + [SAMPLES / "variants" / "alpha_01_duplicate.md"])
    # 2) 抽き出し
    analyze_all(conn, GoldRunner())
    rep = evaluate(conn)
    assert rep["sources_missing"] == [] and rep["sources_total"] == 10 and rep["duplicate_versions"] == 0
    assert rep["match_rate"] >= 0.9
    assert rep["fabricated_owner_or_due"] == 0
    # 3) 会議日の順に全件採用（オーナーの確認の代わり）
    ids = [r[0] for r in conn.execute(
        """SELECT e.extraction_id FROM extractions e JOIN source_versions v ON v.version_id=e.version_id
           JOIN sources s ON s.source_id=v.source_id ORDER BY s.meeting_date, e.extraction_id""")]
    for eid in ids:
        adopt(conn, eid)
    # 同じ事柄は1つの項目にまとまる（プロジェクトをまたがない）
    expected = len({(sid.split("-")[1], i["item_type"], i["content"])
                    for sid in [p.stem for p in (SAMPLES / "gold").glob("*.json")]
                    for i in gold(sid)["items"]})
    n_items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert n_items == expected, (n_items, expected)
    mixed = conn.execute(
        """SELECT COUNT(*) FROM item_events ev JOIN items i ON i.item_id=ev.item_id
           JOIN sources s ON s.source_id=ev.source_id WHERE s.project<>i.project""").fetchone()[0]
    assert mixed == 0
    # 根拠のない担当・期限が確定データに無い
    for it in conn.execute("SELECT * FROM items"):
        assert it["owner"] == "不明" or it["owner"] in json.dumps(gold_all(), ensure_ascii=False)
    # 4) 再処理しても増えない
    before = counts(conn)
    ingest_files(conn, MEETINGS)
    for r in conn.execute("SELECT version_id FROM source_versions").fetchall():
        analyze(conn, r[0], GoldRunner())
    assert counts(conn) == before
    # 5) 処理の履歴が残り、失敗が無い
    st = dict(conn.execute("SELECT status, COUNT(*) FROM runs GROUP BY status").fetchall())
    assert set(st) == {"success"}


def gold_all():
    return [gold(p.stem) for p in (SAMPLES / "gold").glob("*.json")]
