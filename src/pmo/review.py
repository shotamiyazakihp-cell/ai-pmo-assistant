"""確認：候補を採用・却下する。採用したものだけが確定データ（items）に入る。"""
from __future__ import annotations

import sqlite3

from . import db
from .compare import find_match
from .config import UNKNOWN

NEW_ITEM = "new"


class ReviewError(ValueError):
    pass


def _extraction(conn: sqlite3.Connection, extraction_id: int) -> sqlite3.Row:
    e = conn.execute(
        """SELECT e.*, v.source_id, s.project FROM extractions e
           JOIN source_versions v ON v.version_id = e.version_id
           JOIN sources s ON s.source_id = v.source_id WHERE e.extraction_id=?""",
        (extraction_id,),
    ).fetchone()
    if e is None:
        raise ReviewError(f"候補 {extraction_id} がありません")
    return e


def adopt(conn: sqlite3.Connection, extraction_id: int, link_to: int | str | None = None,
          note: str | None = None) -> int:
    """候補を採用する。link_to：既存の項目の番号 / 'new'（新しい項目にする）/ None（自動の対応に従う）。
    戻り値は確定した項目の番号。同じ候補を2回採用しても項目は増えない。"""
    e = _extraction(conn, extraction_id)
    if e["review_status"] == "invalid":
        raise ReviewError("点検で不正とされた候補は採用できません（引用が原文に無いなど）")
    if e["review_status"] == "adopted":
        r = conn.execute("SELECT item_id FROM reviews WHERE extraction_id=? AND decision='adopted' "
                         "ORDER BY review_id DESC LIMIT 1", (extraction_id,)).fetchone()
        return r["item_id"]
    if link_to is None:
        # 分析の後に確定した項目もあるので、採用の時にもう一度照合する
        target = e["matched_item_id"] or find_match(conn, e["project"], e["item_type"], e["content"])[0]
    else:
        target = None if link_to == NEW_ITEM else int(link_to)
    ts = db.now()
    if target is not None:
        item = conn.execute("SELECT * FROM items WHERE item_id=?", (target,)).fetchone()
        if item is None:
            raise ReviewError(f"項目 {target} がありません")
        if item["project"] != e["project"]:
            raise ReviewError("別のプロジェクトの項目とは結び付けられません")
        events = [("mentioned", None, e["content"])]
        updates: dict[str, object] = {"last_source_id": e["source_id"], "updated_at": ts}
        # 原文に書かれた新しい値だけで上書きする（「不明」で上書きしない）
        if e["status"] != UNKNOWN and e["status"] != item["status"]:
            events.append(("status_changed", item["status"], e["status"]))
            updates["status"] = e["status"]
        if e["due"] != UNKNOWN and e["due"] != item["due"]:
            events.append(("due_changed", item["due"], e["due"]))
            updates["due"], updates["due_date"] = e["due"], e["due_date"]
        if e["owner"] != UNKNOWN and e["owner"] != item["owner"]:
            events.append(("owner_changed", item["owner"], e["owner"]))
            updates["owner"] = e["owner"]
        sets = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE items SET {sets} WHERE item_id=?", (*updates.values(), target))
        item_id = target
    else:
        cur = conn.execute(
            """INSERT INTO items(project, item_type, content, owner, due, due_date, status, classification,
                   first_source_id, last_source_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (e["project"], e["item_type"], e["content"], e["owner"], e["due"], e["due_date"], e["status"],
             e["classification"], e["source_id"], e["source_id"], ts, ts),
        )
        item_id = cur.lastrowid
        events = [("created", None, e["content"])]
    for etype, old, new in events:
        conn.execute(
            """INSERT OR IGNORE INTO item_events(item_id, extraction_id, event_type, old_value, new_value,
                   source_id, created_at) VALUES (?,?,?,?,?,?,?)""",
            (item_id, extraction_id, etype, old, new, e["source_id"], ts),
        )
    conn.execute("UPDATE extractions SET review_status='adopted', matched_item_id=? WHERE extraction_id=?",
                 (item_id, extraction_id))
    conn.execute("INSERT INTO reviews(extraction_id, decision, item_id, note, decided_at) VALUES (?,?,?,?,?)",
                 (extraction_id, "adopted", item_id, note, ts))
    conn.commit()
    return item_id


def reject(conn: sqlite3.Connection, extraction_id: int, note: str | None = None) -> None:
    e = _extraction(conn, extraction_id)
    if e["review_status"] == "adopted":
        raise ReviewError("採用済みの候補は却下できません")
    conn.execute("UPDATE extractions SET review_status='rejected' WHERE extraction_id=?", (extraction_id,))
    conn.execute("INSERT INTO reviews(extraction_id, decision, note, decided_at) VALUES (?,?,?,?)",
                 (extraction_id, "rejected", note, db.now()))
    conn.commit()
