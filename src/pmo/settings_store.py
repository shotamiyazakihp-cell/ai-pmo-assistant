"""画面から変える設定と、その変更の記録（理由・評価を必ず残す）。

real_data_enabled は既定で「0」（止める）。勤務先の規程を確かめた後に、オーナーが画面で有効にする。
0 の間は、実データ（本物の議事録・ドライブの PMO ナレッジ・note の原稿）を取り込まず、Claude にも渡さない。
"""
from __future__ import annotations

import sqlite3

from . import db

DEFAULTS = {
    "real_data_enabled": "0",   # 実データを扱うか（0=止める）
    "stale_days": "14",         # この日数より長く話題に出ない未完了の項目を「放置」とみなす
    "due_soon_days": "3",       # 期限が近いとみなす日数
    "match_threshold": "0.6",   # 既存の項目と結び付ける文の近さ
}
LABELS = {
    "real_data_enabled": "実データを扱う（1=扱う／0=止める）",
    "stale_days": "放置とみなす日数",
    "due_soon_days": "期限が近いとみなす日数",
    "match_threshold": "既存の項目と結び付ける文の近さ（0〜1）",
}


class RealDataDisabled(PermissionError):
    pass


def get(conn: sqlite3.Connection, key: str) -> str:
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else DEFAULTS[key]


def get_int(conn: sqlite3.Connection, key: str) -> int:
    return int(get(conn, key))


def get_float(conn: sqlite3.Connection, key: str) -> float:
    return float(get(conn, key))


def real_data_enabled(conn: sqlite3.Connection) -> bool:
    return get(conn, "real_data_enabled") == "1"


def require_real_data(conn: sqlite3.Connection, what: str) -> None:
    if not real_data_enabled(conn):
        raise RealDataDisabled(
            f"{what}は実データのため止めています。勤務先の規程を確かめた後、"
            "「処理の履歴・設定」の画面で「実データを扱う」を 1 にしてください。")


def change(conn: sqlite3.Connection, key: str, new_value: str, reason: str, evaluation: str | None = None) -> None:
    """設定を変える。理由が無い変更は受け付けない。変更は rule_changes に残す。"""
    if key not in DEFAULTS:
        raise KeyError(key)
    if not reason or not reason.strip():
        raise ValueError("変更の理由を書いてください")
    new_value = str(new_value).strip()
    if key == "real_data_enabled" and new_value not in ("0", "1"):
        raise ValueError("0 か 1 を入れてください")
    if key in ("stale_days", "due_soon_days") and (not new_value.isdigit() or int(new_value) < 1):
        raise ValueError("1 以上の整数を入れてください")
    if key == "match_threshold" and not (0.3 <= float(new_value) <= 0.95):
        raise ValueError("0.3〜0.95 の間で入れてください")
    old = get(conn, key)
    if old == new_value:
        return
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, new_value))
    conn.execute("INSERT INTO rule_changes(key, old_value, new_value, reason, evaluation, created_at) VALUES (?,?,?,?,?,?)",
                 (key, old, new_value, reason.strip(), evaluation, db.now()))
    conn.commit()
