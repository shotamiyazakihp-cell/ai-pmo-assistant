"""コマンドの入口（PowerShell から `python -m pmo ...`）。

    check                          PC の確認（API キーが無いか等。鍵の中身は表示しない）
    ingest-samples                 試験用の議事録10件を取り込む
    ingest-notion [--force]        Notion から取り込む（NOTION_DATABASE_KIND=test なら試験用として扱う）
    analyze --all|--source ID [--force] [--runner claude|gold]
    evaluate                       完了の条件を数える（試験用の議事録）
    import-knowledge --samples|--real   ナレッジの取り込み（--real は KNOWLEDGE_DIR、実データの許可が要る）
    import-note --samples|--real        note の書き出しの取り込み（--real は NOTE_EXPORT_PATH）
    search 語                       議事録・項目・ナレッジの横断検索
    daily [--date YYYY-MM-DD]      今日の業務の整理を表示
    document 種類 プロジェクト       資料の下書き（Claude）
    analyze-project プロジェクト     横断分析（Claude）
    auto hourly|daily|weekly [--analyze]   自動実行（タスクスケジューラ用）
    quality                        品質の数字と改善案
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date

from . import db, settings_store
from .automate import daily_job, hourly, render_daily, weekly_job
from .claude_runner import ClaudeCliRunner
from .config import ROOT, api_key_present, get_settings
from .evaluate import GoldRunner, evaluate
from .extract import analyze
from .ingest import ingest_files, ingest_notion
from .knowledge import import_folder, import_note_export, search_all
from .notion_reader import NotionReader
from .practice import DOC_TYPES, analyze_project, generate_document
from .quality import metrics, suggestions


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _runner(name: str):
    return GoldRunner() if name == "gold" else ClaudeCliRunner()


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="pmo")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("ingest-samples")
    n = sub.add_parser("ingest-notion")
    n.add_argument("--force", action="store_true")
    a = sub.add_parser("analyze")
    a.add_argument("--all", action="store_true")
    a.add_argument("--source")
    a.add_argument("--force", action="store_true")
    a.add_argument("--runner", choices=["claude", "gold"], default="claude")
    sub.add_parser("evaluate")
    for name in ("import-knowledge", "import-note"):
        k = sub.add_parser(name)
        g = k.add_mutually_exclusive_group(required=True)
        g.add_argument("--samples", action="store_true")
        g.add_argument("--real", action="store_true")
    s_ = sub.add_parser("search")
    s_.add_argument("query")
    d = sub.add_parser("daily")
    d.add_argument("--date")
    doc = sub.add_parser("document")
    doc.add_argument("doc_type", choices=list(DOC_TYPES))
    doc.add_argument("project")
    ap = sub.add_parser("analyze-project")
    ap.add_argument("project")
    au = sub.add_parser("auto")
    au.add_argument("job", choices=["hourly", "daily", "weekly"])
    au.add_argument("--analyze", action="store_true", help="Claude での分析も行う")
    sub.add_parser("quality")
    args = p.parse_args(argv)

    s = get_settings()
    if args.cmd == "check":
        conn = db.connect(s.db_path)
        _print({
            "ANTHROPIC_API_KEY が設定されているか": api_key_present(),
            "claude コマンド": shutil.which("claude") or "見つからない",
            "NOTION_TOKEN": "設定あり" if s.notion_token else "未設定",
            "NOTION_DATABASE_ID": "設定あり" if s.notion_database_id else "未設定",
            "NOTION_DATABASE_KIND": s.notion_database_kind,
            "実データを扱うか（設定）": settings_store.real_data_enabled(conn),
            "FTS5（日本語の部分一致検索）": db.fts_available(conn),
            "DB": str(s.db_path),
        })
        return 1 if api_key_present() else 0

    conn = db.connect(s.db_path)
    today = date.fromisoformat(args.date) if getattr(args, "date", None) else date.today()
    try:
        if args.cmd == "ingest-samples":
            _print(ingest_files(conn, list((ROOT / "samples" / "meetings").glob("*.md"))))
        elif args.cmd == "ingest-notion":
            if not (s.notion_token and s.notion_database_id):
                print("NOTION_TOKEN と NOTION_DATABASE_ID を .env に設定してください", file=sys.stderr)
                return 2
            res = ingest_notion(conn, NotionReader(s.notion_token), s.notion_database_id, s.notion_project_property,
                                s.notion_date_property, force=args.force, data_kind=s.notion_database_kind)
            _print(res)
            return 0 if res["status"] == "success" else 1
        elif args.cmd == "analyze":
            runner = _runner(args.runner)
            q = """SELECT MAX(v.version_id) AS vid, v.source_id FROM source_versions v
                   JOIN sources s ON s.source_id=v.source_id WHERE s.archived=0"""
            params: list = []
            if args.source:
                q += " AND v.source_id=?"
                params.append(args.source)
            elif not args.all:
                print("--all か --source を指定してください", file=sys.stderr)
                return 2
            rows = conn.execute(q + " GROUP BY v.source_id", params).fetchall()
            if args.source and not rows:
                print(f"議事録がありません: {args.source}", file=sys.stderr)
                return 2
            results, failed = [], 0
            for r in rows:
                done = conn.execute("SELECT analyzed_at FROM source_versions WHERE version_id=?",
                                    (r["vid"],)).fetchone()["analyzed_at"]
                if done and not args.force:
                    continue
                res = analyze(conn, r["vid"], runner)
                failed += res["status"] != "success"
                results.append({"source_id": r["source_id"], **res})
            _print({"analyzed": len(results), "failed": failed, "results": results})
            return 1 if failed else 0
        elif args.cmd == "evaluate":
            _print(evaluate(conn))
        elif args.cmd == "import-knowledge":
            if args.samples:
                res = import_folder(conn, ROOT / "samples" / "knowledge", data_kind="test")
            else:
                if not s.knowledge_dir:
                    print("KNOWLEDGE_DIR を .env に設定してください", file=sys.stderr)
                    return 2
                res = import_folder(conn, s.knowledge_dir, data_kind="real")
            _print(res)
            return 0 if res["status"] == "success" else 1
        elif args.cmd == "import-note":
            if args.samples:
                res = import_note_export(conn, ROOT / "samples" / "note_export_sample.xml", data_kind="test")
            else:
                if not s.note_export_path:
                    print("NOTE_EXPORT_PATH を .env に設定してください", file=sys.stderr)
                    return 2
                res = import_note_export(conn, s.note_export_path, data_kind="real")
            _print(res)
            return 0 if res["status"] == "success" else 1
        elif args.cmd == "search":
            _print(search_all(conn, args.query))
        elif args.cmd == "daily":
            print(render_daily(conn, today))
        elif args.cmd == "document":
            res = generate_document(conn, args.doc_type, args.project, ClaudeCliRunner(), today)
            if res["status"] == "success":
                print(conn.execute("SELECT body FROM documents WHERE document_id=?", (res["document_id"],)).fetchone()[0])
            _print(res)
            return 0 if res["status"] == "success" else 1
        elif args.cmd == "analyze-project":
            res = analyze_project(conn, args.project, ClaudeCliRunner(), today)
            _print(res)
            return 0 if res["status"] == "success" else 1
        elif args.cmd == "auto":
            if args.job == "hourly":
                res = hourly(conn, s)
            elif args.job == "daily":
                res = daily_job(conn, today, s.db_path.parent / "reports",
                                ClaudeCliRunner() if args.analyze else None)
            else:
                if not args.analyze:
                    print("weekly は Claude を使うため --analyze が要ります", file=sys.stderr)
                    return 2
                res = weekly_job(conn, today, ClaudeCliRunner())
            _print(res)
            return 0 if res["status"] in ("success", "skipped") else 1
        elif args.cmd == "quality":
            m = metrics(conn)
            _print({"metrics": m, "suggestions": suggestions(conn, m)})
    except settings_store.RealDataDisabled as e:
        print(str(e), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
