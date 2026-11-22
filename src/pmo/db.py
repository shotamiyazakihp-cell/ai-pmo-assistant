"""SQLite の表と接続。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
-- 取り込んだ議事録（Notion のページ、または試験用のファイル）
CREATE TABLE IF NOT EXISTS sources (
    source_id        TEXT PRIMARY KEY,           -- Notion のページ ID / 試験用 ID
    origin           TEXT NOT NULL,              -- 'notion' | 'file'
    url              TEXT NOT NULL,              -- 出典 URL（ファイルは相対パス）
    title            TEXT,
    project          TEXT NOT NULL DEFAULT '不明',
    meeting_date     TEXT,                       -- YYYY-MM-DD（分からなければ NULL）
    last_edited_time TEXT,
    archived         INTEGER NOT NULL DEFAULT 0, -- ゴミ箱・削除・見つからない
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL
);

-- 原文の版。本文の指紋（ハッシュ）が同じなら新しい版は作らない
CREATE TABLE IF NOT EXISTS source_versions (
    version_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id          TEXT NOT NULL REFERENCES sources(source_id),
    content_hash       TEXT NOT NULL,
    body_text          TEXT NOT NULL,
    last_edited_time   TEXT,
    fetched_at         TEXT NOT NULL,
    unretrieved_blocks TEXT NOT NULL DEFAULT '[]', -- 取得できなかったブロック（JSON）
    analyzed_at        TEXT,
    meeting_json       TEXT,                       -- AI が読んだ会議情報（参加者など）
    raw_output         TEXT,                       -- AI の出力そのもの（監査用）
    UNIQUE (source_id, content_hash)
);

-- AI が抽き出した候補。人が採用するまで確定データにはならない
CREATE TABLE IF NOT EXISTS extractions (
    extraction_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id       INTEGER NOT NULL REFERENCES source_versions(version_id),
    run_id           INTEGER REFERENCES runs(run_id),
    item_type        TEXT NOT NULL,   -- decision / todo / issue / risk / open_question
    content          TEXT NOT NULL,
    owner            TEXT NOT NULL DEFAULT '不明',
    due              TEXT NOT NULL DEFAULT '不明', -- 原文の書き方のまま
    due_date         TEXT,                         -- Python が読めた時だけ YYYY-MM-DD
    status           TEXT NOT NULL DEFAULT '不明',
    classification   TEXT NOT NULL,   -- fact / decision / opinion / ai_inference
    quote            TEXT NOT NULL,   -- 原文の引用
    quote_offset     INTEGER,         -- 原文の中の位置（文字）
    fingerprint      TEXT NOT NULL,
    notes            TEXT NOT NULL DEFAULT '[]',   -- 点検で直した点・捨てた理由（JSON）
    review_status    TEXT NOT NULL DEFAULT 'pending', -- pending / adopted / rejected / invalid
    matched_item_id  INTEGER REFERENCES items(item_id),
    match_reason     TEXT,
    created_at       TEXT NOT NULL,
    UNIQUE (version_id, fingerprint)
);

-- 確定した項目（承認画面で採用されたものだけ）
CREATE TABLE IF NOT EXISTS items (
    item_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project          TEXT NOT NULL,
    item_type        TEXT NOT NULL,
    content          TEXT NOT NULL,
    owner            TEXT NOT NULL DEFAULT '不明',
    due              TEXT NOT NULL DEFAULT '不明',
    due_date         TEXT,
    status           TEXT NOT NULL DEFAULT '不明',
    classification   TEXT NOT NULL,
    first_source_id  TEXT NOT NULL REFERENCES sources(source_id),
    last_source_id   TEXT NOT NULL REFERENCES sources(source_id),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- 項目の変化の履歴（状態・期限・担当の変化、再登場）
CREATE TABLE IF NOT EXISTS item_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL REFERENCES items(item_id),
    extraction_id INTEGER NOT NULL REFERENCES extractions(extraction_id),
    event_type    TEXT NOT NULL,  -- created / status_changed / due_changed / owner_changed / mentioned
    old_value     TEXT,
    new_value     TEXT,
    source_id     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (item_id, extraction_id, event_type)
);

-- 採用・却下の記録
CREATE TABLE IF NOT EXISTS reviews (
    review_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    extraction_id INTEGER NOT NULL REFERENCES extractions(extraction_id),
    decision      TEXT NOT NULL,   -- adopted / rejected
    item_id       INTEGER,
    note          TEXT,
    decided_at    TEXT NOT NULL
);

-- 処理の履歴（いつ・何を・成功か失敗か）
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,     -- ingest_files / ingest_notion / analyze / reconcile
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,     -- running / success / partial / failed
    detail      TEXT NOT NULL DEFAULT '{}',
    error       TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str, check_same_thread: bool = True) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def start_run(conn: sqlite3.Connection, kind: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs(kind, started_at, status) VALUES (?, ?, 'running')", (kind, now())
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, detail: dict, error: str | None = None) -> None:
    assert status in ("success", "partial", "failed")
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, detail=?, error=? WHERE run_id=?",
        (now(), status, json.dumps(detail, ensure_ascii=False), error, run_id),
    )
    conn.commit()
