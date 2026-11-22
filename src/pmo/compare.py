"""過去との比較：候補が既存の項目と同じ事柄かを探す（同じプロジェクト・同じ種類の中だけ）。"""
from __future__ import annotations

import sqlite3

from .textutil import similarity

MATCH_THRESHOLD = 0.6


def find_match(conn: sqlite3.Connection, project: str, item_type: str, content: str,
               ai_same_as: int | None = None) -> tuple[int | None, str | None]:
    """戻り値：(既存の項目の番号, 理由)。見つからなければ (None, None)。"""
    if ai_same_as is not None:
        row = conn.execute(
            "SELECT item_id, content FROM items WHERE item_id=? AND project=? AND item_type=?",
            (ai_same_as, project, item_type),
        ).fetchone()
        if row:
            return row["item_id"], f"AI が同じと判定（文の近さ {similarity(row['content'], content):.2f}）"
    best_id, best_score = None, 0.0
    for row in conn.execute(
        "SELECT item_id, content FROM items WHERE project=? AND item_type=?", (project, item_type)
    ):
        score = similarity(row["content"], content)
        if score > best_score:
            best_id, best_score = row["item_id"], score
    if best_id is not None and best_score >= MATCH_THRESHOLD:
        return best_id, f"文が近い（{best_score:.2f}）"
    return None, None
