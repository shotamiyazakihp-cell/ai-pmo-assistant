"""Phase 2：ナレッジの取り込み・分類・検索。

- フォルダ（Markdown / テキスト）：KB の資料作成知識や過去の成果物。ドライブの PMO ナレッジを PC に書き出したもの。
- note の公式の書き出し（WordPress 形式の XML）：個人の経験・考え方として分類する。
- 実データ（data_kind=real）は設定で許可した時だけ取り込む。試験用（samples/knowledge）はいつでも使える。
- プロジェクト固有のルールは project を付け、ほかのプロジェクトの資料づくりには使わない。
"""
from __future__ import annotations

import html
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from . import db, settings_store
from .config import ROOT
from .textutil import content_hash

KINDS = {"kb": "資料作成知識", "note": "個人の経験・考え（note）", "deliverable": "過去の成果物"}
INFO_CLASSES = {
    "general_knowhow": "一般的なノウハウ",
    "personal_opinion": "個人の意見・経験",
    "project_rule": "プロジェクト固有のルール",
    "fact": "事実",
}
SUFFIXES = {".md", ".txt", ".markdown"}
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
OLD_YEARS = 2  # これより古い資料には「古い可能性」の印を付ける


def _chunks(body: str, size: int = 700) -> list[tuple[str | None, str]]:
    """見出しごとに分け、長い所は段落で区切る。"""
    out: list[tuple[str | None, str]] = []
    heading: str | None = None
    buf: list[str] = []

    def flush():
        text = "\n".join(buf).strip()
        while text:
            out.append((heading, text[:size]))
            text = text[size:]
        buf.clear()

    for line in body.splitlines():
        if re.match(r"^#{1,6}\s", line):
            flush()
            heading = line.lstrip("#").strip()
        else:
            buf.append(line)
    flush()
    return out or [(None, body.strip())]


def upsert_doc(conn: sqlite3.Connection, *, origin: str, location: str, title: str, kind: str,
               info_class: str, body: str, project: str | None = None, updated_at: str | None = None,
               data_kind: str = "test") -> str:
    """戻り値：new / updated / unchanged"""
    if kind not in KINDS or info_class not in INFO_CLASSES:
        raise ValueError(f"分類が不正: {kind} / {info_class}")
    if data_kind == "real":
        settings_store.require_real_data(conn, "実データのナレッジの取り込み")
    h = content_hash(body)
    row = conn.execute("SELECT doc_id, content_hash FROM knowledge_docs WHERE location=?", (location,)).fetchone()
    if row and row["content_hash"] == h:
        return "unchanged"
    if row:
        doc_id = row["doc_id"]
        conn.execute("""UPDATE knowledge_docs SET title=?, kind=?, info_class=?, project=?, updated_at=?,
                        content_hash=?, imported_at=?, data_kind=? WHERE doc_id=?""",
                     (title, kind, info_class, project, updated_at, h, db.now(), data_kind, doc_id))
        _delete_chunks(conn, doc_id)
        status = "updated"
    else:
        cur = conn.execute("""INSERT INTO knowledge_docs(origin, location, title, kind, info_class, project,
                              data_kind, updated_at, content_hash, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                           (origin, location, title, kind, info_class, project, data_kind, updated_at, h, db.now()))
        doc_id = cur.lastrowid
        status = "new"
    fts = db.fts_available(conn)
    for heading, text in _chunks(body):
        cur = conn.execute("INSERT INTO knowledge_chunks(doc_id, heading, text) VALUES (?,?,?)", (doc_id, heading, text))
        if fts:
            conn.execute("INSERT INTO knowledge_fts(text, heading, chunk_id) VALUES (?,?,?)",
                         (text, heading or "", cur.lastrowid))
    conn.commit()
    return status


def _delete_chunks(conn: sqlite3.Connection, doc_id: int) -> None:
    ids = [r[0] for r in conn.execute("SELECT chunk_id FROM knowledge_chunks WHERE doc_id=?", (doc_id,))]
    if db.fts_available(conn) and ids:
        conn.executemany("DELETE FROM knowledge_fts WHERE chunk_id=?", [(i,) for i in ids])
    conn.execute("DELETE FROM knowledge_chunks WHERE doc_id=?", (doc_id,))


def _parse_front(text: str) -> tuple[dict, str]:
    m = _FRONT.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def import_folder(conn: sqlite3.Connection, folder: Path, *, data_kind: str, default_kind: str = "kb",
                  origin: str = "folder") -> dict:
    """フォルダの .md / .txt を取り込む。先頭の --- の中で kind・info_class・project・updated_at を指定できる。"""
    if data_kind == "real":
        settings_store.require_real_data(conn, "ドライブの PMO ナレッジの取り込み")
    run_id = db.start_run(conn, "import_knowledge")
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    errors: list[str] = []
    if not folder.is_dir():
        db.finish_run(conn, run_id, "failed", counts, f"フォルダがありません: {folder}")
        return {"run_id": run_id, "status": "failed", "error": f"フォルダがありません: {folder}", **counts}
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in SUFFIXES)
    for p in files:
        try:
            meta, body = _parse_front(p.read_text(encoding="utf-8"))
            kind = meta.get("kind", default_kind)
            info = meta.get("info_class") or ("project_rule" if meta.get("project") else
                                              "personal_opinion" if kind == "note" else "general_knowhow")
            try:
                loc = p.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                loc = str(p.resolve())
            counts[upsert_doc(conn, origin=origin, location=loc, title=meta.get("title") or p.stem, kind=kind,
                              info_class=info, body=body, project=meta.get("project") or None,
                              updated_at=meta.get("updated_at"), data_kind=data_kind)] += 1
        except Exception as e:  # 1件の失敗で止めない
            errors.append(f"{p.name}: {e}")
    status = "success" if not errors else ("partial" if len(errors) < max(len(files), 1) else "failed")
    db.finish_run(conn, run_id, status, {**counts, "files": len(files), "errors": errors}, "; ".join(errors) or None)
    return {"run_id": run_id, "status": status, **counts, "files": len(files), "errors": errors}


_NS = {"content": "http://purl.org/rss/1.0/modules/content/", "wp": "http://wordpress.org/export/1.2/"}


def _html_to_text(s: str) -> str:
    s = re.sub(r"<\s*(br|/p|/h\d|/li)\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<h(\d)[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


def import_note_export(conn: sqlite3.Connection, xml_path: Path, *, data_kind: str) -> dict:
    """note の公式の書き出し（WordPress 形式の XML）を取り込む。公開・下書きの別を記録し、本文が無い記事は取り込まない。"""
    if data_kind == "real":
        settings_store.require_real_data(conn, "note の原稿の取り込み")
    run_id = db.start_run(conn, "import_note")
    counts = {"new": 0, "updated": 0, "unchanged": 0, "skipped_empty": 0}
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError) as e:
        db.finish_run(conn, run_id, "failed", counts, f"XML を読めません: {e}")
        return {"run_id": run_id, "status": "failed", "error": str(e), **counts}
    statuses: dict[str, int] = {}
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip() or "無題"
        body = _html_to_text(item.findtext("content:encoded", default="", namespaces=_NS))
        status = item.findtext("wp:status", default="不明", namespaces=_NS)
        statuses[status] = statuses.get(status, 0) + 1
        if not body:
            counts["skipped_empty"] += 1  # 取得できていない本文を取り込んだことにしない
            continue
        link = item.findtext("link") or f"{xml_path.name}#{title}"
        posted = (item.findtext("wp:post_date", default="", namespaces=_NS) or "")[:10] or None
        counts[upsert_doc(conn, origin="note_export", location=link, title=f"{title}（{status}）", kind="note",
                          info_class="personal_opinion", body=body, updated_at=posted, data_kind=data_kind)] += 1
    db.finish_run(conn, run_id, "success", {**counts, "statuses": statuses})
    return {"run_id": run_id, "status": "success", **counts, "statuses": statuses}


# ---------------- 検索 ----------------

def _flags(row, project: str | None) -> list[str]:
    flags = []
    if row["project"] and project and row["project"] != project:
        flags.append(f"「{row['project']}」固有のルール（このプロジェクトには当てはめない）")
    elif row["project"] and not project:
        flags.append(f"「{row['project']}」固有のルール")
    if row["updated_at"]:
        try:
            if (date.today() - date.fromisoformat(row["updated_at"][:10])).days > 365 * OLD_YEARS:
                flags.append("古い可能性（更新から2年超）")
        except ValueError:
            pass
    if row["data_kind"] == "real":
        flags.append("実データ")
    return flags


def search(conn: sqlite3.Connection, query: str, project: str | None = None, limit: int = 10,
           exclude_other_projects: bool = False) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    sql_doc = """SELECT c.chunk_id, c.heading, c.text, d.doc_id, d.title, d.kind, d.info_class, d.project,
                        d.location, d.updated_at, d.data_kind FROM knowledge_chunks c
                 JOIN knowledge_docs d ON d.doc_id = c.doc_id"""
    real_ok = settings_store.real_data_enabled(conn)
    rows: list = []
    terms = [t for t in re.split(r"\s+", q) if t]
    if db.fts_available(conn) and all(len(t) >= 3 for t in terms):
        match = " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)
        ids = [r[0] for r in conn.execute(
            "SELECT chunk_id FROM knowledge_fts WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit * 5))]
        if ids:
            by_id = {r["chunk_id"]: r for r in conn.execute(
                sql_doc + f" WHERE c.chunk_id IN ({','.join('?' * len(ids))})", ids)}
            rows = [by_id[i] for i in ids if i in by_id]
    else:  # 2文字以下の語は LIKE で探す
        where = " AND ".join("(c.text LIKE ? OR c.heading LIKE ? OR d.title LIKE ?)" for _ in terms)
        params = [p for t in terms for p in (f"%{t}%",) * 3]
        rows = conn.execute(sql_doc + " WHERE " + where + " LIMIT ?", (*params, limit * 5)).fetchall()
    out = []
    for r in rows:
        if r["data_kind"] == "real" and not real_ok:
            continue
        if exclude_other_projects and r["project"] and r["project"] != project:
            continue
        out.append({**dict(r), "flags": _flags(r, project)})
        if len(out) >= limit:
            break
    return out


def search_all(conn: sqlite3.Connection, query: str, project: str | None = None, limit: int = 10) -> dict:
    """議事録・確定した項目・ナレッジを横断して探す。"""
    like = f"%{query.strip()}%"
    pj_sql, pj_params = ("AND s.project=?", [project]) if project else ("", [])
    minutes = [dict(r) for r in conn.execute(
        f"""SELECT s.source_id, s.title, s.project, s.meeting_date, s.url, v.body_text FROM sources s
            JOIN source_versions v ON v.version_id = (SELECT MAX(version_id) FROM source_versions WHERE source_id=s.source_id)
            WHERE v.body_text LIKE ? {pj_sql} ORDER BY s.meeting_date DESC LIMIT ?""", (like, *pj_params, limit))]
    for m in minutes:
        body = m.pop("body_text")
        i = body.find(query.strip())
        m["snippet"] = body[max(0, i - 40): i + 80].replace("\n", " ") if i >= 0 else ""
    items = [dict(r) for r in conn.execute(
        f"""SELECT i.item_id, i.project, i.item_type, i.content, i.owner, i.due, i.status FROM items i
            JOIN sources s ON s.source_id = i.last_source_id WHERE i.content LIKE ? {pj_sql} LIMIT ?""",
        (like, *pj_params, limit))]
    return {"minutes": minutes, "items": items, "knowledge": search(conn, query, project, limit)}
