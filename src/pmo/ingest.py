"""取り込み：Notion または試験用のファイルから議事録を読み、版として保存する。

- ページ ID と本文の指紋が同じなら、新しい版は作らない（重複の防止）。
- 本文が変わったら新しい版を作る（差分）。
- 取得できなかったものは「取得できず」と記録し、成功扱いにしない。
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import db
from .config import ROOT, UNKNOWN
from .notion_reader import NotionError, NotionReader, page_property_text, page_title
from .textutil import content_hash


@dataclass
class SourceMeta:
    source_id: str
    origin: str
    url: str
    title: str
    project: str = UNKNOWN
    meeting_date: str | None = None
    last_edited_time: str | None = None
    archived: bool = False


def upsert_source(conn: sqlite3.Connection, meta: SourceMeta, body: str,
                  unretrieved: list[dict] | None = None) -> str:
    """原文を保存する。戻り値：'new'（初めて）/ 'new_version'（本文が変わった）/ 'unchanged'。"""
    ts = db.now()
    row = conn.execute("SELECT source_id FROM sources WHERE source_id=?", (meta.source_id,)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO sources(source_id, origin, url, title, project, meeting_date,
                   last_edited_time, archived, first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (meta.source_id, meta.origin, meta.url, meta.title, meta.project or UNKNOWN,
             meta.meeting_date, meta.last_edited_time, int(meta.archived), ts, ts),
        )
    else:
        conn.execute(
            """UPDATE sources SET url=?, title=?, project=?, meeting_date=?, last_edited_time=?,
                   archived=?, last_seen_at=? WHERE source_id=?""",
            (meta.url, meta.title, meta.project or UNKNOWN, meta.meeting_date, meta.last_edited_time,
             int(meta.archived), ts, meta.source_id),
        )
    h = content_hash(body)
    exists = conn.execute(
        "SELECT 1 FROM source_versions WHERE source_id=? AND content_hash=?", (meta.source_id, h)
    ).fetchone()
    if exists:
        conn.commit()
        return "unchanged"
    had_version = conn.execute(
        "SELECT 1 FROM source_versions WHERE source_id=?", (meta.source_id,)
    ).fetchone()
    conn.execute(
        """INSERT INTO source_versions(source_id, content_hash, body_text, last_edited_time,
               fetched_at, unretrieved_blocks) VALUES (?,?,?,?,?,?)""",
        (meta.source_id, h, body, meta.last_edited_time, ts,
         json.dumps(unretrieved or [], ensure_ascii=False)),
    )
    conn.commit()
    return "new_version" if had_version else "new"


# ---------------- 試験用のファイル ----------------

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_markdown_file(path: Path) -> tuple[SourceMeta, str]:
    text = path.read_text(encoding="utf-8")
    m = _FRONT.match(text)
    if not m:
        raise ValueError(f"先頭の情報（--- で囲んだ部分）がありません: {path}")
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    for key in ("source_id", "title"):
        if not meta.get(key):
            raise ValueError(f"{key} がありません: {path}")
    try:
        rel = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        rel = path.as_posix()
    return SourceMeta(
        source_id=meta["source_id"],
        origin="file",
        url=meta.get("url") or rel,
        title=meta["title"],
        project=meta.get("project") or UNKNOWN,
        meeting_date=meta.get("meeting_date") or None,
        last_edited_time=meta.get("last_edited_time") or None,
    ), text[m.end():].strip()


def ingest_files(conn: sqlite3.Connection, paths: list[Path]) -> dict:
    run_id = db.start_run(conn, "ingest_files")
    counts = {"new": 0, "new_version": 0, "unchanged": 0}
    errors: list[str] = []
    for p in sorted(paths):
        try:
            meta, body = parse_markdown_file(p)
            counts[upsert_source(conn, meta, body)] += 1
        except Exception as e:  # 1件の失敗で全体を止めず、記録する
            errors.append(f"{p.name}: {e}")
    detail = {**counts, "files": len(paths), "errors": errors}
    status = "success" if not errors else ("partial" if len(errors) < len(paths) else "failed")
    db.finish_run(conn, run_id, status, detail, "; ".join(errors) or None)
    return {"run_id": run_id, "status": status, **detail}


# ---------------- Notion ----------------

def _meta_from_page(page: dict, reader: NotionReader, project_prop: str, date_prop: str,
                    title_cache: dict[str, str]) -> SourceMeta:
    def resolve(pid: str) -> str:
        if pid not in title_cache:
            try:
                title_cache[pid] = page_title(reader.get_page(pid)) or pid
            except NotionError:
                title_cache[pid] = pid  # 名前が読めない時は ID のまま（勝手に名前を付けない）
        return title_cache[pid]

    date_val = page_property_text(page, date_prop)
    return SourceMeta(
        source_id=page["id"],
        origin="notion",
        url=page.get("url", ""),
        title=page_title(page),
        project=page_property_text(page, project_prop, resolve) or UNKNOWN,
        meeting_date=date_val[:10] if date_val else None,
        last_edited_time=page.get("last_edited_time"),
        archived=bool(page.get("archived") or page.get("in_trash")),
    )


def ingest_notion(conn: sqlite3.Connection, reader: NotionReader, database_id: str,
                  project_prop: str, date_prop: str, force: bool = False) -> dict:
    run_id = db.start_run(conn, "ingest_notion")
    counts = {"listed": 0, "skipped_not_edited": 0, "new": 0, "new_version": 0,
              "unchanged": 0, "archived": 0, "missing_marked": 0, "unretrieved_blocks": 0}
    errors: list[str] = []
    seen: set[str] = set()
    title_cache: dict[str, str] = {}
    listing_complete = False
    try:
        for page in reader.query_database(database_id):
            pid = page["id"]
            if pid in seen:  # 一覧の途中で順番がずれて同じページが2回来ても、1回だけ処理する
                continue
            seen.add(pid)
            counts["listed"] += 1
            try:
                meta = _meta_from_page(page, reader, project_prop, date_prop, title_cache)
                row = conn.execute(
                    "SELECT last_edited_time FROM sources WHERE source_id=?", (pid,)
                ).fetchone()
                if meta.archived:
                    counts["archived"] += 1
                    conn.execute("UPDATE sources SET archived=1, last_seen_at=? WHERE source_id=?",
                                 (db.now(), pid))
                    conn.commit()
                    continue
                if row and row["last_edited_time"] == meta.last_edited_time and not force:
                    counts["skipped_not_edited"] += 1
                    conn.execute("UPDATE sources SET last_seen_at=?, archived=0 WHERE source_id=?",
                                 (db.now(), pid))
                    conn.commit()
                    continue
                body = reader.get_body(pid)
                counts["unretrieved_blocks"] += len(body.unretrieved)
                counts[upsert_source(conn, meta, body.text, body.unretrieved)] += 1
            except NotionError as e:
                errors.append(f"{pid}: {e}")
        listing_complete = True
    except NotionError as e:
        errors.append(f"一覧の取得に失敗: {e}")

    # 一覧を最後まで取れた時だけ、見つからなくなったページに印を付ける
    if listing_complete:
        stored = [r["source_id"] for r in conn.execute(
            "SELECT source_id FROM sources WHERE origin='notion' AND archived=0")]
        for sid in stored:
            if sid not in seen:
                conn.execute("UPDATE sources SET archived=1 WHERE source_id=?", (sid,))
                counts["missing_marked"] += 1
        conn.commit()

    if not listing_complete:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "success"
    detail = {**counts, "errors": errors}
    db.finish_run(conn, run_id, status, detail, "; ".join(errors) or None)
    return {"run_id": run_id, "status": status, **detail}
