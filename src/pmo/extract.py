"""抽き出し：指示文を作り、AI の返事（JSON）を点検して候補として保存する。

点検（Python で行う。AI に任せない）：
- 引用が原文に無い項目は invalid にする（作り話の防止）。
- 担当・期限が引用の中に見当たらなければ「不明」に直す。
- 既存の項目との対応は、同じプロジェクト・同じ種類の中だけで探す。
- 同じ版をもう一度処理しても、同じ項目・似た項目は増やさない。
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from . import db
from .claude_runner import Runner, RunnerError
from .compare import find_match
from .config import UNKNOWN
from .textutil import contains, find_quote, fingerprint, parse_due, similarity

ITEM_TYPES = {
    "decision": "決定事項",
    "todo": "TODO",
    "issue": "課題",
    "risk": "リスク",
    "open_question": "未決事項",
}
CLASSIFICATIONS = {
    "fact": "事実",
    "decision": "決定",
    "opinion": "個人の意見",
    "ai_inference": "AIの推測",
}
REEXTRACT_SIMILARITY = 0.85  # 再処理の時、これ以上似た候補は同じものとして増やさない

PROMPT_TEMPLATE = """あなたは PMO の議事録分析の担当です。下の議事録から、決定事項・TODO・課題・リスク・未決事項を抽き出し、
指定の JSON だけを返してください（説明文やコードブロックの記号は不要）。道具は使わないでください。

# 守ること
1. すべての項目に、議事録の文をそのまま写した "quote"（原文の引用）を付ける。言い換えや要約は quote に入れない。
2. 担当者（owner）と期限（due）は、quote の中に書かれている時だけ入れる。書かれていなければ "不明" とする。推測で埋めない。
3. 期限は原文の書き方のまま入れる（例：「10/9」「来週中」）。日付への変換はしない。
4. status は原文に書かれた状態（未着手・対応中・完了・延期・未決 など）。書かれていなければ "不明"。
5. classification は fact（事実）/ decision（決定）/ opinion（個人の意見）/ ai_inference（あなたの推測）のどれか。
   原文に書かれていない推測を入れる時は ai_inference にし、quote には根拠の文を入れる。
6. item_type は decision / todo / issue / risk / open_question のどれか。
7. 同じプロジェクトの既存の項目と同じ事柄なら、"same_as_item_id" にその番号を入れる（違うなら null）。
   別のプロジェクトの項目とは結び付けない。
8. 議事録に無い事柄は作らない。分からないことは "不明"。

# 返す JSON の形
{{
  "meeting": {{"title": "...", "date": "YYYY-MM-DD または 不明", "participants": ["..."], "purpose": "... または 不明"}},
  "items": [
    {{"item_type": "todo", "content": "...", "owner": "... または 不明", "due": "... または 不明",
      "status": "...", "classification": "fact", "quote": "原文そのまま", "same_as_item_id": null}}
  ]
}}

# この議事録
SOURCE_ID: {source_id}
タイトル: {title}
プロジェクト: {project}
会議日: {meeting_date}

# 同じプロジェクトの既存の項目（参考。番号: 種類 / 内容 / 担当 / 期限 / 状態）
{existing}

# 議事録の本文
<<<議事録ここから>>>
{body}
<<<議事録ここまで>>>
"""


def build_prompt(conn: sqlite3.Connection, version_id: int) -> str:
    v = _version(conn, version_id)
    rows = conn.execute(
        "SELECT item_id, item_type, content, owner, due, status FROM items WHERE project=? ORDER BY item_id",
        (v["project"],),
    ).fetchall()
    existing = "\n".join(
        f"{r['item_id']}: {r['item_type']} / {r['content']} / {r['owner']} / {r['due']} / {r['status']}" for r in rows
    ) or "（なし）"
    return PROMPT_TEMPLATE.format(
        source_id=v["source_id"], title=v["title"] or "", project=v["project"],
        meeting_date=v["meeting_date"] or UNKNOWN, existing=existing, body=v["body_text"],
    )


def _version(conn: sqlite3.Connection, version_id: int) -> sqlite3.Row:
    v = conn.execute(
        """SELECT v.*, s.project, s.title, s.meeting_date, s.url FROM source_versions v
           JOIN sources s ON s.source_id = v.source_id WHERE v.version_id=?""",
        (version_id,),
    ).fetchone()
    if v is None:
        raise ValueError(f"版 {version_id} がありません")
    return v


def latest_version_id(conn: sqlite3.Connection, source_id: str) -> int | None:
    r = conn.execute(
        "SELECT MAX(version_id) AS v FROM source_versions WHERE source_id=?", (source_id,)
    ).fetchone()
    return r["v"] if r else None


class OutputFormatError(ValueError):
    pass


def parse_output(text: str) -> dict:
    """AI の返事から JSON を取り出す。形が違えばエラー（成功扱いにしない）。"""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    else:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            raise OutputFormatError("返事に JSON が見つかりません")
        t = t[start:end + 1]
    try:
        data = json.loads(t)
    except json.JSONDecodeError as e:
        raise OutputFormatError(f"JSON として読めません: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise OutputFormatError("items の一覧がありません")
    return data


@dataclass
class CheckedItem:
    item_type: str
    content: str
    owner: str
    due: str
    due_date: str | None
    status: str
    classification: str
    quote: str
    quote_offset: int | None
    same_as_item_id: int | None
    notes: list[str] = field(default_factory=list)
    valid: bool = True


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def check_item(raw: dict, body: str, meeting_date: str | None) -> CheckedItem:
    notes: list[str] = []
    valid = True
    item_type = _s(raw.get("item_type"))
    if item_type not in ITEM_TYPES:
        notes.append(f"種類が不正: {item_type!r}")
        valid = False
    classification = _s(raw.get("classification"))
    if classification not in CLASSIFICATIONS:
        notes.append(f"分類が不正のため ai_inference にした: {classification!r}")
        classification = "ai_inference"
    content = _s(raw.get("content"))
    if not content:
        notes.append("内容が空")
        valid = False
    quote = _s(raw.get("quote"))
    offset = find_quote(body, quote)
    if offset is None:
        notes.append("引用が原文に見つからない")
        valid = False
    owner = _s(raw.get("owner")) or UNKNOWN
    if owner != UNKNOWN and not contains(quote, owner):
        notes.append(f"担当「{owner}」の根拠が引用に無いため「不明」にした")
        owner = UNKNOWN
    due = _s(raw.get("due")) or UNKNOWN
    if due != UNKNOWN and not contains(quote, due):
        notes.append(f"期限「{due}」の根拠が引用に無いため「不明」にした")
        due = UNKNOWN
    due_date = parse_due(due, meeting_date) if due != UNKNOWN else None
    status = _s(raw.get("status")) or UNKNOWN
    same_as = raw.get("same_as_item_id")
    try:
        same_as = int(same_as) if same_as not in (None, "", "null") else None
    except (TypeError, ValueError):
        notes.append(f"same_as_item_id が数字でない: {same_as!r}")
        same_as = None
    return CheckedItem(item_type, content, owner, due, due_date, status, classification,
                       quote, offset, same_as, notes, valid)


def save_extraction(conn: sqlite3.Connection, version_id: int, output_text: str,
                    run_id: int | None = None) -> dict:
    """AI の返事を点検して候補として保存する。再処理でも項目は増やさない。"""
    v = _version(conn, version_id)
    data = parse_output(output_text)
    body = v["body_text"]
    counts = {"saved": 0, "invalid": 0, "duplicate_skipped": 0}

    # 再処理：まだ確かめていない候補は入れ替える。採用・却下・不正の記録は残す
    conn.execute("DELETE FROM extractions WHERE version_id=? AND review_status='pending'", (version_id,))
    kept = conn.execute(
        "SELECT item_type, content, fingerprint FROM extractions WHERE version_id=?", (version_id,)
    ).fetchall()
    kept_list = [dict(r) for r in kept]

    for raw in data["items"]:
        if not isinstance(raw, dict):
            continue
        it = check_item(raw, body, v["meeting_date"])
        fp = fingerprint(it.item_type, it.content, it.quote)
        if any(k["fingerprint"] == fp for k in kept_list) or any(
            k["item_type"] == it.item_type and similarity(k["content"], it.content) >= REEXTRACT_SIMILARITY
            for k in kept_list
        ):
            counts["duplicate_skipped"] += 1
            continue
        matched_id, reason = (None, None)
        if it.valid:
            matched_id, reason = find_match(conn, v["project"], it.item_type, it.content, it.same_as_item_id)
            if it.same_as_item_id and matched_id != it.same_as_item_id:
                it.notes.append(f"AI が示した既存の項目 {it.same_as_item_id} は同じプロジェクト・種類に無いため使わない")
        conn.execute(
            """INSERT INTO extractions(version_id, run_id, item_type, content, owner, due, due_date, status,
                   classification, quote, quote_offset, fingerprint, notes, review_status,
                   matched_item_id, match_reason, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (version_id, run_id, it.item_type or "unknown", it.content or "(空)", it.owner, it.due, it.due_date,
             it.status, it.classification, it.quote, it.quote_offset, fp,
             json.dumps(it.notes, ensure_ascii=False), "pending" if it.valid else "invalid",
             matched_id, reason, db.now()),
        )
        kept_list.append({"item_type": it.item_type, "content": it.content, "fingerprint": fp})
        counts["invalid" if not it.valid else "saved"] += 1

    conn.execute(
        "UPDATE source_versions SET analyzed_at=?, meeting_json=?, raw_output=? WHERE version_id=?",
        (db.now(), json.dumps(data.get("meeting", {}), ensure_ascii=False), output_text, version_id),
    )
    conn.commit()
    return counts


def analyze(conn: sqlite3.Connection, version_id: int, runner: Runner) -> dict:
    """1つの版を Claude で分析する。失敗は failed として履歴に残す。"""
    run_id = db.start_run(conn, "analyze")
    detail: dict = {"version_id": version_id, "runner": runner.name}
    try:
        prompt = build_prompt(conn, version_id)
        output = runner.run(prompt)
        counts = save_extraction(conn, version_id, output, run_id)
        detail.update(counts)
        db.finish_run(conn, run_id, "success", detail)
        return {"run_id": run_id, "status": "success", **detail}
    except (RunnerError, OutputFormatError, ValueError) as e:
        detail["error_type"] = type(e).__name__
        db.finish_run(conn, run_id, "failed", detail, str(e))
        return {"run_id": run_id, "status": "failed", "error": str(e), **detail}


def analyze_pasted(conn: sqlite3.Connection, version_id: int, pasted: str) -> dict:
    """手で貼った返事を保存する（予備の方法）。"""
    run_id = db.start_run(conn, "analyze")
    detail: dict = {"version_id": version_id, "runner": "manual_paste"}
    try:
        counts = save_extraction(conn, version_id, pasted, run_id)
        detail.update(counts)
        db.finish_run(conn, run_id, "success", detail)
        return {"run_id": run_id, "status": "success", **detail}
    except (OutputFormatError, ValueError) as e:
        db.finish_run(conn, run_id, "failed", detail, str(e))
        return {"run_id": run_id, "status": "failed", "error": str(e), **detail}
