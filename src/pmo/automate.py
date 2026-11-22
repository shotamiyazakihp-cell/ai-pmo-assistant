"""Phase 4：自動化。Windows のタスクスケジューラから呼ぶ想定（scripts/register_tasks.ps1）。

- hourly：Notion の更新確認と差分の取り込みだけ（Claude は使わない＝利用枠を使わない）。
- daily：未対応事項の再評価（Python）と日次レポートの書き出し。--analyze を付けた時だけ、新しい議事録を Claude で分析する。
- weekly：プロジェクトごとの横断分析（Claude）。--analyze を付けた時だけ。
通知はレポートのファイル（data/reports）と処理の履歴。外部サービスへの送信はしない。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from . import db, settings_store
from .claude_runner import Runner
from .config import Settings
from .extract import ITEM_TYPES, analyze
from .ingest import ingest_notion
from .notion_reader import NotionReader
from .practice import analyze_project, daily


def hourly(conn: sqlite3.Connection, s: Settings, reader: NotionReader | None = None) -> dict:
    run_id = db.start_run(conn, "auto_hourly")
    if not (s.notion_token and s.notion_database_id):
        db.finish_run(conn, run_id, "success", {"skipped": "Notion の設定が無い"})
        return {"status": "skipped", "reason": "Notion の設定が無い"}
    if s.notion_database_kind == "real" and not settings_store.real_data_enabled(conn):
        db.finish_run(conn, run_id, "success", {"skipped": "実データが止まっている"})
        return {"status": "skipped", "reason": "実データが止まっている（NOTION_DATABASE_KIND=test なら試験用として取り込む）"}
    res = ingest_notion(conn, reader or NotionReader(s.notion_token), s.notion_database_id,
                        s.notion_project_property, s.notion_date_property, data_kind=s.notion_database_kind)
    db.finish_run(conn, run_id, res["status"], {"ingest_run_id": res["run_id"], "new": res["new"],
                                                 "new_version": res["new_version"]})
    return res


def pending_versions(conn: sqlite3.Connection) -> list[int]:
    """分析していない最新版（実データは許可されている時だけ）。"""
    real_ok = settings_store.real_data_enabled(conn)
    rows = conn.execute(
        """SELECT MAX(v.version_id) vid, s.data_kind FROM source_versions v JOIN sources s ON s.source_id=v.source_id
           WHERE s.archived=0 GROUP BY v.source_id""").fetchall()
    out = []
    for r in rows:
        done = conn.execute("SELECT analyzed_at FROM source_versions WHERE version_id=?", (r["vid"],)).fetchone()[0]
        if not done and (r["data_kind"] == "test" or real_ok):
            out.append(r["vid"])
    return sorted(out)


def render_daily(conn: sqlite3.Connection, today: date) -> str:
    d = daily(conn, today)
    titles = {
        "overdue": "期限超過の TODO", "due_today": "今日が期限", "due_soon": "期限が近い TODO",
        "stalled": "放置ぎみの項目", "owner_unknown": "担当が決まっていない TODO（決める必要）",
        "due_unknown": "期限が決まっていない TODO", "open_questions": "未決事項（会議・顧客に確認）",
        "open_issues": "未解決の課題", "open_risks": "対応中のリスク",
    }
    lines = [f"# 日次の確認（{today.isoformat()}）", "",
             "Python が確定データから判定したもの。AI の推測は入っていない。", ""]
    for key, title in titles.items():
        rows = d[key]
        lines.append(f"## {title}：{len(rows)} 件")
        for it in rows:
            extra = f"（{it['days_over']}日超過）" if "days_over" in it else f"（{it['days_idle']}日話題なし）" if "days_idle" in it else ""
            lines.append(f"- [#{it['item_id']}] {it['project']}｜{ITEM_TYPES.get(it['item_type'])}｜{it['content']}"
                         f"｜担当：{it['owner']}｜期限：{it['due']}｜状態：{it['status']}{extra}")
        lines.append("")
    pend = len(conn.execute("SELECT 1 FROM extractions WHERE review_status='pending'").fetchall())
    lines.append(f"## 確認待ちの抽出候補：{pend} 件")
    return "\n".join(lines)


def daily_job(conn: sqlite3.Connection, today: date, report_dir: Path, runner: Runner | None = None) -> dict:
    run_id = db.start_run(conn, "auto_daily")
    analyzed, failed = 0, 0
    if runner is not None:
        for vid in pending_versions(conn):
            res = analyze(conn, vid, runner)
            analyzed += 1
            failed += res["status"] != "success"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"daily_{today.isoformat()}.md"
    path.write_text(render_daily(conn, today), encoding="utf-8")
    detail = {"report": str(path), "analyzed": analyzed, "analyze_failed": failed}
    db.finish_run(conn, run_id, "success" if not failed else "partial", detail)
    return {"status": "success" if not failed else "partial", **detail}


def weekly_job(conn: sqlite3.Connection, today: date, runner: Runner) -> dict:
    run_id = db.start_run(conn, "auto_weekly")
    results = {}
    for (pj,) in conn.execute("SELECT DISTINCT project FROM items ORDER BY 1").fetchall():
        results[pj] = analyze_project(conn, pj, runner, today)["status"]
    status = "success" if all(v == "success" for v in results.values()) else "partial"
    db.finish_run(conn, run_id, status, {"projects": results})
    return {"status": status, "projects": results}
