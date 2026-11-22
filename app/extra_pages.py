"""Phase 2〜5 の画面（ダッシュボード・プロジェクト・課題/リスク・TODO・AI 分析・資料作成・ナレッジ検索・品質と設定）。"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from pmo import settings_store
from pmo.automate import render_daily
from pmo.claude_runner import ClaudeCliRunner
from pmo.config import ROOT, api_key_present, get_settings
from pmo.extract import ITEM_TYPES
from pmo.knowledge import INFO_CLASSES, KINDS, import_folder, import_note_export, search_all
from pmo.practice import (DOC_TYPES, FINDING_TYPES, analyze_project, build_analysis_prompt, build_doc_prompt,
                          daily, export_table, project_status, save_document, save_findings,
                          set_document_status, set_finding_status)
from pmo.quality import add_missed, metrics, suggestions

TYPE_LABEL = {"overdue": "期限超過", "due_today": "今日が期限", "due_soon": "期限が近い", "stalled": "放置ぎみ",
              "owner_unknown": "担当が未定", "due_unknown": "期限が未定", "open_questions": "未決事項",
              "open_issues": "未解決の課題", "open_risks": "対応中のリスク"}


def _projects(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT project FROM sources ORDER BY 1")]


def _items_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["種類"] = df["item_type"].map(ITEM_TYPES)
    cols = {"item_id": "番号", "project": "プロジェクト", "種類": "種類", "content": "内容", "owner": "担当",
            "due": "期限", "status": "状態", "last_meeting_date": "最後に出た会議"}
    extra = [c for c in ("days_over", "days_idle") if c in df.columns]
    out = df[list(cols) + extra].rename(columns={**cols, "days_over": "超過日数", "days_idle": "話題なし日数"})
    return out


def _today() -> date:
    return st.session_state.get("today_override") or date.today()


def page_dashboard(conn):
    st.header("ダッシュボード")
    today = st.date_input("基準日", value=_today(), key="dash_date")
    st.session_state["today_override"] = today
    d = daily(conn, today)
    cols = st.columns(4)
    for c, key in zip(cols, ["overdue", "due_soon", "stalled", "owner_unknown"]):
        c.metric(TYPE_LABEL[key], len(d[key]) + (len(d["due_today"]) if key == "due_soon" else 0))
    pend = conn.execute("SELECT COUNT(*) FROM extractions WHERE review_status='pending'").fetchone()[0]
    fpend = conn.execute("SELECT COUNT(*) FROM findings WHERE status='pending'").fetchone()[0]
    st.caption(f"確認待ち：抽出の候補 {pend} 件／AI の指摘 {fpend} 件。数字は確定データから Python が数えたもの（AI の推測は入らない）。")
    for key in ["overdue", "due_today", "due_soon", "open_questions", "owner_unknown", "stalled", "open_issues", "open_risks"]:
        rows = d[key]
        with st.expander(f"{TYPE_LABEL[key]}：{len(rows)} 件", expanded=key in ("overdue", "due_today") and bool(rows)):
            if rows:
                st.dataframe(_items_df(rows), hide_index=True, width="stretch")
            else:
                st.caption("なし")
    st.download_button("日次の確認を Markdown で保存", render_daily(conn, today),
                       file_name=f"daily_{today.isoformat()}.md")


def page_projects(conn):
    st.header("プロジェクト一覧")
    today = _today()
    for pj in _projects(conn):
        stt = project_status(conn, pj, today)
        with st.container(border=True):
            st.subheader(pj)
            c = st.columns(5)
            c[0].metric("未完了の TODO", stt["counts"]["todo"]["open"])
            c[1].metric("未解決の課題", stt["counts"]["issue"]["open"])
            c[2].metric("リスク", stt["counts"]["risk"]["open"])
            c[3].metric("期限超過", stt["overdue"])
            c[4].metric("未決事項", stt["counts"]["open_question"]["open"])
            st.caption(f"進捗率：{stt['progress_rate']}")
            if stt["decisions"]:
                st.markdown("**最近の決定**：" + "／".join(f"[#{x['item_id']}] {x['content']}" for x in stt["decisions"]))
            st.caption("直近の会議：" + "、".join(f"{m['meeting_date']} {m['title']}" for m in stt["recent_meetings"]))


def _typed_page(conn, types: tuple[str, ...], title: str):
    st.header(title)
    pj = st.selectbox("プロジェクト", ["すべて"] + _projects(conn), key=f"pj_{title}")
    show_closed = st.checkbox("完了・解決したものも表示する", key=f"cl_{title}")
    q = """SELECT i.*, s.meeting_date AS last_meeting_date FROM items i JOIN sources s ON s.source_id=i.last_source_id
           WHERE i.item_type IN (%s)""" % ",".join("?" * len(types))
    params = list(types)
    if pj != "すべて":
        q += " AND i.project=?"
        params.append(pj)
    from pmo.practice import CLOSED
    rows = [dict(r) for r in conn.execute(q + " ORDER BY i.due_date IS NULL, i.due_date, i.item_id", params)]
    if not show_closed:
        rows = [r for r in rows if r["status"] not in CLOSED]
    st.dataframe(_items_df(rows), hide_index=True, width="stretch")
    for t in types:
        if t in ("todo", "issue", "risk"):
            st.download_button(f"{ITEM_TYPES[t]}の表を CSV で保存（Excel で開ける）",
                               export_table(conn, t, None if pj == "すべて" else pj),
                               file_name=f"{t}_list.csv", key=f"dl_{title}_{t}")


def page_issues(conn):
    _typed_page(conn, ("issue", "risk", "open_question"), "課題・リスク管理")


def page_todos(conn):
    _typed_page(conn, ("todo",), "TODO 管理")


def _claude_or_paste(key: str, prompt: str, on_output):
    """Claude Code で実行するか、指示文を手で貼る。"""
    c1, c2 = st.columns(2)
    if c1.button("Claude Code で実行", type="primary", key=f"run_{key}", disabled=api_key_present()):
        with st.spinner("Claude Code で作成しています…"):
            try:
                on_output(ClaudeCliRunner().run(prompt))
            except Exception as e:  # 失敗は失敗として表示
                st.error(f"失敗：{e}")
    with c2.expander("手で貼る（予備）"):
        st.code(prompt, language=None)
        pasted = st.text_area("Claude の返事", key=f"paste_{key}", height=120)
        if st.button("貼った結果を保存", key=f"save_{key}"):
            try:
                on_output(pasted)
            except Exception as e:
                st.error(f"失敗：{e}")


def page_analysis(conn):
    st.header("AI 分析結果（横断分析・次の行動の提案）")
    st.caption("AI の推測・提案です。根拠の項目番号が実在しない指摘は保存しません。採用・却下は品質の評価に使います。")
    pjs = [r[0] for r in conn.execute("SELECT DISTINCT project FROM items ORDER BY 1")]
    if not pjs:
        st.info("確定した項目がまだありません。")
        return
    pj = st.selectbox("プロジェクト", pjs, key="an_pj")
    try:
        prompt, ids = build_analysis_prompt(conn, pj, _today())
        _claude_or_paste("analysis", prompt, lambda out: st.success(
            f"保存：{save_findings(conn, pj, out, ids, None)}"))
    except settings_store.RealDataDisabled as e:
        st.warning(str(e))
    for f in conn.execute("SELECT * FROM findings WHERE project=? ORDER BY status='pending' DESC, finding_id DESC",
                          (pj,)):
        with st.container(border=True):
            st.markdown(f"**{FINDING_TYPES.get(f['finding_type'])}**｜{f['text']}　`{f['status']}`")
            st.caption(f"根拠：{', '.join('#' + str(x) for x in json.loads(f['evidence']))}　分類："
                       f"{'AI の推測' if f['classification'] == 'ai_inference' else 'AI の提案'}")
            if f["status"] == "pending":
                c1, c2, c3 = st.columns([3, 1, 1])
                fb = c1.text_input("ひとこと（任意）", key=f"fb{f['finding_id']}", label_visibility="collapsed",
                                   placeholder="理由（任意）")
                if c2.button("妥当", key=f"ok{f['finding_id']}"):
                    set_finding_status(conn, f["finding_id"], "accepted", fb)
                    st.rerun()
                if c3.button("的外れ", key=f"ng{f['finding_id']}"):
                    set_finding_status(conn, f["finding_id"], "dismissed", fb)
                    st.rerun()


def page_documents(conn):
    st.header("資料作成（下書き）")
    pjs = _projects(conn)
    if not pjs:
        st.info("議事録がまだありません。")
        return
    c1, c2 = st.columns(2)
    doc_type = c1.selectbox("種類", list(DOC_TYPES), format_func=lambda k: DOC_TYPES[k][0])
    pj = c2.selectbox("プロジェクト", pjs, key="doc_pj")
    audience = st.text_input("対象者", value=DOC_TYPES[doc_type][1])
    extra = st.text_input("追加の指示（任意）")
    try:
        prompt, meta = build_doc_prompt(conn, doc_type, pj, _today(), audience, None, extra)
        st.caption("参照する知識：" + ("、".join(f"{k['k']} {k['title']}" for k in meta["knowledge"]) or "なし"))
        _claude_or_paste("doc", prompt, lambda out: st.success(
            f"下書き #{save_document(conn, doc_type, pj, out, meta, audience, None)} を保存しました"))
    except settings_store.RealDataDisabled as e:
        st.warning(str(e))
    st.subheader("これまでの下書き")
    for d in conn.execute("SELECT * FROM documents ORDER BY document_id DESC LIMIT 20"):
        with st.expander(f"#{d['document_id']} {d['title']}（{d['status']}・{d['created_at'][:16]}）"):
            for w in json.loads(d["warnings"]):
                st.warning(w)
            body = st.text_area("本文（直してから「承認」すると、直した版も記録します）", d["edited_body"] or d["body"],
                                height=300, key=f"body{d['document_id']}")
            b1, b2 = st.columns(2)
            if b1.button("承認", key=f"ap{d['document_id']}"):
                set_document_status(conn, d["document_id"], "approved", body if body != d["body"] else None)
                st.rerun()
            if b2.button("使わない", key=f"rj{d['document_id']}"):
                set_document_status(conn, d["document_id"], "rejected")
                st.rerun()
            st.download_button("Markdown で保存", body, file_name=f"document_{d['document_id']}.md",
                               key=f"dl{d['document_id']}")


def page_knowledge(conn):
    st.header("ナレッジ検索（議事録・項目・知識を横断）")
    s = get_settings()
    real = settings_store.real_data_enabled(conn)
    with st.expander("ナレッジの取り込み"):
        c1, c2 = st.columns(2)
        if c1.button("試験用の資料と note を取り込む"):
            st.write(import_folder(conn, ROOT / "samples" / "knowledge", data_kind="test"))
            st.write(import_note_export(conn, ROOT / "samples" / "note_export_sample.xml", data_kind="test"))
        if c2.button("実データ（KNOWLEDGE_DIR・NOTE_EXPORT_PATH）を取り込む", disabled=not real,
                     help=None if real else "実データは止めています（設定で有効にするまで使えません）"):
            if s.knowledge_dir:
                st.write(import_folder(conn, s.knowledge_dir, data_kind="real"))
            if s.note_export_path:
                st.write(import_note_export(conn, s.note_export_path, data_kind="real"))
    q = st.text_input("探す言葉", placeholder="例：データ移行、週次報告、担当")
    pj = st.selectbox("プロジェクト（固有ルールの判定に使う）", ["指定しない"] + _projects(conn))
    if not q:
        return
    r = search_all(conn, q, None if pj == "指定しない" else pj)
    st.subheader(f"ナレッジ：{len(r['knowledge'])} 件")
    for k in r["knowledge"]:
        with st.container(border=True):
            st.markdown(f"**{k['title']}**（{k['heading'] or ''}）　{KINDS.get(k['kind'])}・{INFO_CLASSES.get(k['info_class'])}")
            for f in k["flags"]:
                st.caption("⚠ " + f)
            st.write(k["text"])
            st.caption(f"出典：{k['location']}")
    st.subheader(f"議事録：{len(r['minutes'])} 件")
    for m in r["minutes"]:
        st.markdown(f"- {m['meeting_date']} **{m['title']}**（{m['project']}）：…{m['snippet']}…　`{m['url']}`")
    st.subheader(f"確定した項目：{len(r['items'])} 件")
    st.dataframe(pd.DataFrame(r["items"]), hide_index=True, width="stretch")


def page_quality_settings(conn):
    st.subheader("品質（Phase 5）")
    m = metrics(conn)
    c = st.columns(4)
    c[0].metric("採用率", "—" if m["adoption_rate"] is None else f"{m['adoption_rate']:.0%}")
    c[1].metric("点検で不正", "—" if m["invalid_rate"] is None else f"{m['invalid_rate']:.0%}")
    c[2].metric("見落とし", m["missed_items"])
    c[3].metric("AI の指摘の採用率", "—" if m["finding_accept_rate"] is None else f"{m['finding_accept_rate']:.0%}")
    if m["weekly"]:
        st.dataframe(pd.DataFrame(m["weekly"]), hide_index=True, width="stretch")
    st.markdown("**改善案（ルールによる提案。採用するかは自分で決める）**")
    for s_ in suggestions(conn, m):
        st.markdown(f"- {s_}")
    with st.expander("見落としを記録する（AI が抽き出さなかった事柄）"):
        srcs = {r["source_id"]: f"{r['meeting_date']} {r['title']}" for r in conn.execute(
            "SELECT source_id, meeting_date, title FROM sources ORDER BY meeting_date DESC")}
        if srcs:
            sid = st.selectbox("議事録", list(srcs), format_func=srcs.get)
            desc = st.text_input("見落とした内容")
            if st.button("記録する"):
                try:
                    add_missed(conn, sid, desc)
                    st.success("記録しました")
                except ValueError as e:
                    st.error(str(e))
    st.subheader("設定（変更には理由が要り、履歴に残ります）")
    with st.form("settings"):
        key = st.selectbox("項目", list(settings_store.DEFAULTS), format_func=settings_store.LABELS.get)
        st.caption(f"いまの値：{settings_store.get(conn, key)}")
        val = st.text_input("新しい値")
        reason = st.text_input("変更の理由（必須）")
        evaluation = st.text_input("どう評価するか（任意）")
        confirm = st.checkbox("実データを扱う設定は、勤務先の規程を確かめた上で変えることを確認した") \
            if key == "real_data_enabled" else True
        if st.form_submit_button("変更する"):
            if not confirm:
                st.error("確認のチェックを入れてください")
            else:
                try:
                    settings_store.change(conn, key, val, reason, evaluation or None)
                    st.success("変更しました")
                except (ValueError, KeyError) as e:
                    st.error(str(e))
    st.dataframe(pd.read_sql_query("SELECT created_at AS 日時, key AS 項目, old_value AS 前, new_value AS 後, "
                                   "reason AS 理由, evaluation AS 評価 FROM rule_changes ORDER BY change_id DESC", conn),
                 hide_index=True, width="stretch")
