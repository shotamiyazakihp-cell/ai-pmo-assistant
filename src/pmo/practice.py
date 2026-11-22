"""Phase 3：実務支援。

数える・日付を判定するものは Python（daily, project_status, 表の書き出し）。
文章づくりと横断の分析は Claude（generate_document, analyze_project）。AI の出力は下書き・候補として保存し、
根拠の項目番号が実在するかを Python で点検する。進捗率などの数字は AI に作らせない。
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import date, timedelta

from . import db, settings_store
from .claude_runner import Runner, RunnerError
from .config import UNKNOWN
from .extract import ITEM_TYPES, OutputFormatError, parse_output
from .knowledge import search

CLOSED = {"完了", "解決", "解消", "対応済み", "クローズ", "済み", "中止"}
OPEN_TYPES = ("todo", "issue", "risk", "open_question")


def _items(conn: sqlite3.Connection, project: str | None = None) -> list[dict]:
    q = """SELECT i.*, s.meeting_date AS last_meeting_date, s.title AS last_title, s.url AS last_url
           FROM items i JOIN sources s ON s.source_id = i.last_source_id"""
    rows = conn.execute(q + (" WHERE i.project=?" if project else "") + " ORDER BY i.project, i.item_id",
                        (project,) if project else ()).fetchall()
    return [dict(r) for r in rows]


def is_open(item: dict) -> bool:
    return item["item_type"] in OPEN_TYPES and item["status"] not in CLOSED


def daily(conn: sqlite3.Connection, today: date, project: str | None = None) -> dict[str, list[dict]]:
    """今日の業務の整理（ルールで判定）。"""
    soon = today + timedelta(days=settings_store.get_int(conn, "due_soon_days"))
    stale = settings_store.get_int(conn, "stale_days")
    out: dict[str, list[dict]] = {k: [] for k in (
        "overdue", "due_today", "due_soon", "stalled", "owner_unknown", "due_unknown", "open_questions",
        "open_issues", "open_risks")}
    for it in _items(conn, project):
        if not is_open(it):
            continue
        d = date.fromisoformat(it["due_date"]) if it["due_date"] else None
        if it["item_type"] == "todo":
            if d and d < today:
                out["overdue"].append({**it, "days_over": (today - d).days})
            elif d and d == today:
                out["due_today"].append(it)
            elif d and d <= soon:
                out["due_soon"].append(it)
            if it["owner"] == UNKNOWN:
                out["owner_unknown"].append(it)
            if it["due"] == UNKNOWN:
                out["due_unknown"].append(it)
        if it["item_type"] == "open_question":
            out["open_questions"].append(it)
        if it["item_type"] == "issue":
            out["open_issues"].append(it)
        if it["item_type"] == "risk":
            out["open_risks"].append(it)
        if it["last_meeting_date"]:
            idle = (today - date.fromisoformat(it["last_meeting_date"])).days
            if idle > stale:
                out["stalled"].append({**it, "days_idle": idle})
    return out


def project_status(conn: sqlite3.Connection, project: str, today: date) -> dict:
    items = _items(conn, project)
    counts = {t: {"open": 0, "closed": 0} for t in ITEM_TYPES}
    for it in items:
        if it["item_type"] in counts:
            counts[it["item_type"]]["open" if is_open(it) else "closed"] += 1
    d = daily(conn, today, project)
    meetings = conn.execute("SELECT title, meeting_date, url FROM sources WHERE project=? AND archived=0 "
                            "ORDER BY meeting_date DESC LIMIT 5", (project,)).fetchall()
    return {
        "project": project,
        "counts": counts,
        "overdue": len(d["overdue"]),
        "due_soon": len(d["due_soon"]) + len(d["due_today"]),
        "stalled": len(d["stalled"]),
        "owner_unknown": len(d["owner_unknown"]),
        "decisions": [it for it in items if it["item_type"] == "decision"][-5:],
        "recent_meetings": [dict(m) for m in meetings],
        "progress_rate": "不明（根拠となる数字が議事録に無いため出さない）",
    }


def export_table(conn: sqlite3.Connection, item_type: str, project: str | None = None) -> str:
    """課題管理表・リスク管理表・TODO 一覧を CSV（Excel で開ける UTF-8 BOM 付き）で返す。"""
    cols = ["番号", "プロジェクト", "内容", "担当", "期限", "期限日", "状態", "分類", "最後に出た会議", "会議日", "出典"]
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(cols)
    for it in _items(conn, project):
        if it["item_type"] != item_type:
            continue
        w.writerow([it["item_id"], it["project"], it["content"], it["owner"], it["due"], it["due_date"] or "",
                    it["status"], it["classification"], it["last_title"], it["last_meeting_date"] or "", it["last_url"]])
    return buf.getvalue()


# ---------------- 資料の下書き（Claude） ----------------

DOC_TYPES = {
    "weekly_report": ("週次報告書", "上司・関係者", "週次報告 構成 進捗"),
    "monthly_report": ("月次報告書", "上司・顧客", "報告 構成 進捗"),
    "status_report": ("プロジェクト状況報告", "上司・顧客", "報告 結論 課題"),
    "meeting_agenda": ("会議アジェンダ", "会議の参加者", "アジェンダ 会議準備"),
    "meeting_prep": ("会議準備メモ（確認事項・質問・優先順位）", "自分", "会議準備 未決 宿題"),
    "minutes_draft": ("議事録の清書", "会議の参加者", "議事録 決定 宿題"),
    "wbs_update": ("WBS 更新案", "自分・メンバー", "WBS 作業 担当 期限"),
    "email_customer": ("顧客向けメール", "顧客", "顧客 確認 依頼"),
    "email_internal": ("社内向けメール", "社内の関係者", "報告 依頼"),
    "report_to_boss": ("上司向けの報告", "上司", "結論 判断 報告"),
    "request_to_member": ("メンバーへの依頼文", "メンバー", "依頼 担当 期限"),
}

DOC_PROMPT = """あなたは PMO の資料作成の担当です。下の「使ってよい事実」と「参考にする知識」だけを使って、
{label}の下書きを Markdown で書いてください。説明やコードブロックの記号は不要で、下書きの本文だけを返してください。

# 守ること
1. 事実は「使ってよい事実」にあるものだけを使う。項目に触れる時は [#番号] を付ける。
2. 進捗率・割合・件数などの数字は、「使ってよい事実」に書かれたものだけを使う。無い数字は作らない。
3. 担当・期限が「不明」のものは「不明（要確認）」と書く。推測で埋めない。
4. 「参考にする知識」は書き方・構成の参考にし、使った所に [K番号] を付ける。別のプロジェクト固有のルールは使わない。
5. 対象者（{audience}）と目的（{purpose}）に合わせて、構成と言葉づかいを調整する。
6. 最後に「確認が必要なこと」を箇条書きにする（無ければ「なし」）。

# 資料
種類：{label}
プロジェクト：{project}
対象者：{audience}
目的：{purpose}
作成日：{today}
追加の指示：{extra}

# 使ってよい事実（Python が議事録の確定データから集計したもの）
{facts}

# 参考にする知識
{knowledge}
"""


def _facts_text(conn: sqlite3.Connection, project: str, today: date) -> tuple[str, list[int]]:
    st = project_status(conn, project, today)
    d = daily(conn, today, project)
    lines = [f"- 集計日：{today.isoformat()}",
             f"- 期限超過の TODO：{st['overdue']} 件／期限が近い TODO：{st['due_soon']} 件／"
             f"放置ぎみ（{settings_store.get_int(conn, 'stale_days')}日超）の項目：{st['stalled']} 件／担当不明の TODO：{st['owner_unknown']} 件",
             "- 進捗率：不明（議事録に根拠が無い）",
             "- 直近の会議：" + "、".join(f"{m['meeting_date']} {m['title']}" for m in st["recent_meetings"])]
    ids = []
    for it in _items(conn, project):
        flag = ""
        if any(x["item_id"] == it["item_id"] for x in d["overdue"]):
            flag = "（期限超過）"
        lines.append(f"- [#{it['item_id']}] {ITEM_TYPES.get(it['item_type'])}：{it['content']}／担当：{it['owner']}／"
                     f"期限：{it['due']}／状態：{it['status']}{flag}／最後に出た会議：{it['last_meeting_date']}")
        ids.append(it["item_id"])
    return "\n".join(lines), ids


def build_doc_prompt(conn: sqlite3.Connection, doc_type: str, project: str, today: date,
                     audience: str | None = None, purpose: str | None = None, extra: str = "") -> tuple[str, dict]:
    if doc_type not in DOC_TYPES:
        raise ValueError(f"資料の種類が不正: {doc_type}")
    label, default_aud, keywords = DOC_TYPES[doc_type]
    facts, ids = _facts_text(conn, project, today)
    refs: list[dict] = []
    seen: set[int] = set()
    # このプロジェクト固有のルールは必ず渡す（別プロジェクトのものは渡さない）
    for r in conn.execute(
            """SELECT c.chunk_id, c.heading, c.text, d.doc_id, d.title, d.kind, d.info_class, d.project, d.location,
                      d.updated_at, d.data_kind FROM knowledge_chunks c JOIN knowledge_docs d ON d.doc_id=c.doc_id
               WHERE d.project=? AND (d.data_kind='test' OR ?) LIMIT 3""",
            (project, settings_store.real_data_enabled(conn))):
        seen.add(r["chunk_id"])
        refs.append({**dict(r), "flags": [f"「{project}」固有のルール"]})
    for kw in keywords.split():
        for h in search(conn, kw, project=project, limit=3, exclude_other_projects=True):
            if h["chunk_id"] not in seen:
                seen.add(h["chunk_id"])
                refs.append(h)
    refs = refs[:5]
    ktext = "\n".join(f"[K{i + 1}] {r['title']}（{r['heading'] or ''}）{'・'.join(r['flags'])}\n{r['text']}"
                      for i, r in enumerate(refs)) or "（該当なし）"
    prompt = DOC_PROMPT.format(label=label, project=project, audience=audience or default_aud,
                               purpose=purpose or label, today=today.isoformat(), extra=extra or "なし",
                               facts=facts, knowledge=ktext)
    meta = {"item_ids": ids, "knowledge": [{"k": f"K{i + 1}", "title": r["title"], "location": r["location"],
                                            "chunk_id": r["chunk_id"]} for i, r in enumerate(refs)]}
    return prompt, meta


def check_document(body: str, meta: dict) -> list[str]:
    warnings = []
    cited = {int(x) for x in re.findall(r"\[#(\d+)\]", body)}
    unknown = sorted(cited - set(meta["item_ids"]))
    if unknown:
        warnings.append(f"存在しない項目番号を引用している：{unknown}")
    kcited = set(re.findall(r"\[K(\d+)\]", body))
    kunknown = sorted(k for k in kcited if int(k) > len(meta["knowledge"]))
    if kunknown:
        warnings.append(f"存在しない知識の番号を引用している：{kunknown}")
    if re.search(r"\d+(\.\d+)?\s*[%％]", body):
        warnings.append("割合（%）が書かれている。根拠の数字か確かめる")
    if not body.strip():
        warnings.append("本文が空")
    return warnings


def save_document(conn: sqlite3.Connection, doc_type: str, project: str, body: str, meta: dict,
                  audience: str | None, run_id: int | None) -> int:
    body = re.sub(r"^```(?:markdown|md)?\s*\n|\n```\s*$", "", body.strip())
    warnings = check_document(body, meta)
    cur = conn.execute(
        """INSERT INTO documents(run_id, doc_type, project, title, audience, body, refs, warnings, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (run_id, doc_type, project, f"{DOC_TYPES[doc_type][0]}（{project}）", audience, body,
         json.dumps(meta, ensure_ascii=False), json.dumps(warnings, ensure_ascii=False), db.now()))
    conn.commit()
    return cur.lastrowid


def generate_document(conn: sqlite3.Connection, doc_type: str, project: str, runner: Runner, today: date,
                      audience: str | None = None, purpose: str | None = None, extra: str = "") -> dict:
    run_id = db.start_run(conn, "generate_document")
    detail = {"doc_type": doc_type, "project": project, "runner": runner.name}
    try:
        _require_project_allowed(conn, project)
        prompt, meta = build_doc_prompt(conn, doc_type, project, today, audience, purpose, extra)
        body = runner.run(prompt)
        doc_id = save_document(conn, doc_type, project, body, meta, audience, run_id)
        detail["document_id"] = doc_id
        db.finish_run(conn, run_id, "success", detail)
        return {"status": "success", "run_id": run_id, "document_id": doc_id}
    except (RunnerError, ValueError, settings_store.RealDataDisabled) as e:
        db.finish_run(conn, run_id, "failed", detail, str(e))
        return {"status": "failed", "run_id": run_id, "error": str(e)}


def _require_project_allowed(conn: sqlite3.Connection, project: str) -> None:
    real = conn.execute("SELECT 1 FROM sources WHERE project=? AND data_kind='real' LIMIT 1", (project,)).fetchone()
    if real:
        settings_store.require_real_data(conn, "本物の議事録から作った情報を Claude に渡すこと")


# ---------------- 横断分析（Claude） ----------------

FINDING_TYPES = {
    "recurring": "同じ問題の再発・継続",
    "stalled": "放置・対応の遅れ",
    "inconsistency": "認識の食い違い・決定の変更",
    "risk": "潜在リスク・後続への影響",
    "next_action": "次に取るべき行動の提案",
    "question": "会議・顧客に確認すべきこと",
}

ANALYSIS_PROMPT = """あなたは PMO です。下のプロジェクトの確定した項目と、その変化の履歴を横断して読み、
単独の議事録だけでは分からない問題と、次に取るべき行動を見つけてください。指定の JSON だけを返してください。

# 守ること
1. 根拠にした項目の番号を evidence_item_ids に必ず入れる（下の一覧にある番号だけ）。根拠の無い指摘はしない。
2. finding_type は recurring / stalled / inconsistency / risk / next_action / question のどれか。
3. classification は、事実から言える推測なら ai_inference、提案なら ai_proposal。
4. 数字（日数・件数）は下の一覧に書かれたものだけを使う。進捗率は作らない。担当・期限は推測で決めない。
5. 多くても8件まで。重要なものから並べる。

# 返す JSON の形
{{"findings": [{{"finding_type": "stalled", "text": "...", "evidence_item_ids": [1, 2], "classification": "ai_inference"}}]}}

# プロジェクト：{project}（集計日 {today}）
{facts}

# 項目の変化の履歴（古い順）
{history}
"""


def build_analysis_prompt(conn: sqlite3.Connection, project: str, today: date) -> tuple[str, list[int]]:
    facts, ids = _facts_text(conn, project, today)
    hist = conn.execute(
        """SELECT ev.item_id, ev.event_type, ev.old_value, ev.new_value, s.meeting_date, s.title FROM item_events ev
           JOIN items i ON i.item_id = ev.item_id JOIN sources s ON s.source_id = ev.source_id
           WHERE i.project=? ORDER BY s.meeting_date, ev.event_id""", (project,)).fetchall()
    history = "\n".join(f"- {h['meeting_date']} {h['title']}：[#{h['item_id']}] {h['event_type']}"
                        + (f"（{h['old_value']} → {h['new_value']}）" if h["event_type"] != "mentioned" and h["event_type"] != "created" else "")
                        for h in hist) or "（なし）"
    return ANALYSIS_PROMPT.format(project=project, today=today.isoformat(), facts=facts, history=history), ids


def save_findings(conn: sqlite3.Connection, project: str, output: str, valid_ids: list[int], run_id: int | None) -> dict:
    t = output.strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise OutputFormatError("返事に JSON が見つかりません")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise OutputFormatError(f"JSON として読めません: {e}") from e
    if not isinstance(data.get("findings"), list):
        raise OutputFormatError("findings の一覧がありません")
    counts = {"saved": 0, "dropped": 0}
    for f in data["findings"]:
        ev = [int(x) for x in (f.get("evidence_item_ids") or []) if str(x).isdigit()]
        ok = f.get("finding_type") in FINDING_TYPES and str(f.get("text", "")).strip() and ev \
            and all(x in valid_ids for x in ev)
        if not ok:
            counts["dropped"] += 1  # 根拠の無い・別プロジェクトの番号を使った指摘は保存しない
            continue
        cls = f.get("classification") if f.get("classification") in ("ai_inference", "ai_proposal") else "ai_inference"
        conn.execute("""INSERT INTO findings(run_id, project, finding_type, text, evidence, classification, created_at)
                        VALUES (?,?,?,?,?,?,?)""",
                     (run_id, project, f["finding_type"], f["text"].strip(), json.dumps(ev), cls, db.now()))
        counts["saved"] += 1
    conn.commit()
    return counts


def analyze_project(conn: sqlite3.Connection, project: str, runner: Runner, today: date) -> dict:
    run_id = db.start_run(conn, "analyze_project")
    detail: dict = {"project": project, "runner": runner.name}
    try:
        _require_project_allowed(conn, project)
        prompt, ids = build_analysis_prompt(conn, project, today)
        detail.update(save_findings(conn, project, runner.run(prompt), ids, run_id))
        db.finish_run(conn, run_id, "success", detail)
        return {"status": "success", "run_id": run_id, **detail}
    except (RunnerError, OutputFormatError, ValueError, settings_store.RealDataDisabled) as e:
        db.finish_run(conn, run_id, "failed", detail, str(e))
        return {"status": "failed", "run_id": run_id, "error": str(e)}


def set_finding_status(conn: sqlite3.Connection, finding_id: int, status: str, feedback: str | None = None) -> None:
    if status not in ("accepted", "dismissed"):
        raise ValueError(status)
    conn.execute("UPDATE findings SET status=?, feedback=? WHERE finding_id=?", (status, feedback, finding_id))
    conn.commit()


def set_document_status(conn: sqlite3.Connection, document_id: int, status: str, edited_body: str | None = None) -> None:
    if status not in ("draft", "approved", "rejected"):
        raise ValueError(status)
    conn.execute("UPDATE documents SET status=?, edited_body=COALESCE(?, edited_body) WHERE document_id=?",
                 (status, edited_body, document_id))
    conn.commit()
