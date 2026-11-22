"""コマンドの入口。例（PowerShell）：

    python -m pmo check                    # PC の確認（API キーが無いか等）
    python -m pmo ingest-samples           # 試験用の議事録10件を取り込む
    python -m pmo ingest-notion            # Notion から取り込む（.env の設定が要る）
    python -m pmo analyze --all            # 未分析の議事録を Claude Code で分析
    python -m pmo analyze --all --runner gold   # Claude を呼ばず正解データで流れを試す
    python -m pmo evaluate                 # 完了の条件を数える
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys

from . import db
from .claude_runner import ClaudeCliRunner
from .config import ROOT, api_key_present, get_settings
from .evaluate import GoldRunner, evaluate
from .extract import analyze
from .ingest import ingest_files, ingest_notion
from .notion_reader import NotionReader


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="pmo")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("ingest-samples")
    n = sub.add_parser("ingest-notion")
    n.add_argument("--force", action="store_true", help="更新日時が同じでも本文を取り直す")
    a = sub.add_parser("analyze")
    a.add_argument("--all", action="store_true", help="未分析の最新版をすべて分析")
    a.add_argument("--source", help="この source_id だけ分析")
    a.add_argument("--force", action="store_true", help="分析済みでもやり直す（候補は増えない）")
    a.add_argument("--runner", choices=["claude", "gold"], default="claude")
    sub.add_parser("evaluate")
    args = p.parse_args(argv)

    s = get_settings()
    if args.cmd == "check":
        _print({
            "ANTHROPIC_API_KEY が設定されているか": api_key_present(),
            "claude コマンド": shutil.which("claude") or "見つからない",
            "NOTION_TOKEN": "設定あり" if s.notion_token else "未設定",
            "NOTION_DATABASE_ID": "設定あり" if s.notion_database_id else "未設定",
            "DB": str(s.db_path),
        })
        return 1 if api_key_present() else 0

    conn = db.connect(s.db_path)
    if args.cmd == "ingest-samples":
        _print(ingest_files(conn, list((ROOT / "samples" / "meetings").glob("*.md"))))
    elif args.cmd == "ingest-notion":
        if not (s.notion_token and s.notion_database_id):
            print("NOTION_TOKEN と NOTION_DATABASE_ID を .env に設定してください", file=sys.stderr)
            return 2
        res = ingest_notion(conn, NotionReader(s.notion_token), s.notion_database_id,
                            s.notion_project_property, s.notion_date_property, force=args.force)
        _print(res)
        return 0 if res["status"] == "success" else 1
    elif args.cmd == "analyze":
        runner = GoldRunner() if args.runner == "gold" else ClaudeCliRunner()
        q = """SELECT MAX(v.version_id) AS vid, v.source_id FROM source_versions v
               JOIN sources s ON s.source_id=v.source_id WHERE s.archived=0"""
        params: list = []
        if args.source:
            q += " AND v.source_id=?"
            params.append(args.source)
        elif not args.all:
            print("--all か --source を指定してください", file=sys.stderr)
            return 2
        q += " GROUP BY v.source_id"
        rows = conn.execute(q, params).fetchall()
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
