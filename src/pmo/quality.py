"""Phase 5：分析品質の評価と改善。

- 採用・却下・点検で不正の割合、AI の指摘の採用率、資料の手直しの量を数える。
- 見落とし（AI が抽き出さなかった事柄）を人が記録する。
- 改善案は Python のルールで出す（提案として表示するだけ。設定の変更は人が理由を書いて行い、rule_changes に残る）。
過去の採用・却下を無条件に正解として学習はしない。
"""
from __future__ import annotations

import sqlite3
from difflib import SequenceMatcher

from . import db


def add_missed(conn: sqlite3.Connection, source_id: str, description: str) -> int:
    if not description.strip():
        raise ValueError("見落とした内容を書いてください")
    if not conn.execute("SELECT 1 FROM sources WHERE source_id=?", (source_id,)).fetchone():
        raise ValueError(f"議事録がありません: {source_id}")
    cur = conn.execute("INSERT INTO missed_items(source_id, description, created_at) VALUES (?,?,?)",
                       (source_id, description.strip(), db.now()))
    conn.commit()
    return cur.lastrowid


def _rate(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def metrics(conn: sqlite3.Connection) -> dict:
    ex = dict(conn.execute("SELECT review_status, COUNT(*) FROM extractions GROUP BY 1").fetchall())
    reviewed = ex.get("adopted", 0) + ex.get("rejected", 0)
    total = sum(ex.values())
    fd = dict(conn.execute("SELECT status, COUNT(*) FROM findings GROUP BY 1").fetchall())
    docs = conn.execute("SELECT status, body, edited_body FROM documents").fetchall()
    edited = [d for d in docs if d["edited_body"]]
    edit_ratio = None
    if edited:
        edit_ratio = round(sum(1 - SequenceMatcher(None, d["body"], d["edited_body"]).ratio() for d in edited)
                           / len(edited), 3)
    weekly = [dict(r) for r in conn.execute(
        """SELECT strftime('%Y-W%W', created_at) AS 週, COUNT(*) AS 候補,
                  SUM(review_status='adopted') AS 採用, SUM(review_status='rejected') AS 却下,
                  SUM(review_status='invalid') AS 不正 FROM extractions GROUP BY 1 ORDER BY 1""")]
    return {
        "extractions": {"total": total, **ex},
        "adoption_rate": _rate(ex.get("adopted", 0), reviewed),
        "invalid_rate": _rate(ex.get("invalid", 0), total),
        "missed_items": conn.execute("SELECT COUNT(*) FROM missed_items").fetchone()[0],
        "findings": fd,
        "finding_accept_rate": _rate(fd.get("accepted", 0), fd.get("accepted", 0) + fd.get("dismissed", 0)),
        "documents": {"total": len(docs), "approved": sum(d["status"] == "approved" for d in docs),
                      "rejected": sum(d["status"] == "rejected" for d in docs), "edited": len(edited)},
        "avg_edit_ratio": edit_ratio,
        "weekly": weekly,
        "failed_runs": conn.execute("SELECT COUNT(*) FROM runs WHERE status IN ('failed','partial')").fetchone()[0],
    }


def suggestions(conn: sqlite3.Connection, m: dict | None = None) -> list[str]:
    """改善案（ルールによる提案。採用するかは人が決める）。"""
    m = m or metrics(conn)
    out = []
    if m["invalid_rate"] is not None and m["invalid_rate"] > 0.1:
        out.append(f"点検で不正になる候補が {m['invalid_rate']:.0%} ある。指示文の「引用はそのまま写す」を強める案を検討する。")
    if m["adoption_rate"] is not None and m["adoption_rate"] < 0.7:
        out.append(f"採用率が {m['adoption_rate']:.0%}。却下の理由を見て、抽き出す範囲を絞る案を検討する。")
    if m["missed_items"]:
        out.append(f"見落としが {m['missed_items']} 件記録されている。種類の偏りを見て、指示文に例を足す案を検討する。")
    if m["avg_edit_ratio"] is not None and m["avg_edit_ratio"] > 0.3:
        out.append(f"資料の下書きの手直しが平均 {m['avg_edit_ratio']:.0%}。直した版をナレッジ（過去の成果物）に入れる案を検討する。")
    if m["finding_accept_rate"] is not None and m["finding_accept_rate"] < 0.5:
        out.append("AI の指摘の採用率が5割未満。横断分析の件数を減らし、根拠の強いものに絞る案を検討する。")
    if m["failed_runs"]:
        out.append(f"失敗・一部失敗の処理が {m['failed_runs']} 件ある。「処理の履歴」で原因を確かめる。")
    rej = conn.execute("""SELECT r.note, COUNT(*) c FROM reviews r WHERE r.decision='rejected' AND r.note IS NOT NULL
                          AND r.note<>'' GROUP BY r.note ORDER BY c DESC LIMIT 3""").fetchall()
    for r in rej:
        if r["c"] >= 3:
            out.append(f"却下の理由「{r['note']}」が {r['c']} 回ある。判定ルールの見直し候補。")
    return out or ["今のところ改善が必要な兆しは見つからない（データが少ない場合は判断できない）。"]
