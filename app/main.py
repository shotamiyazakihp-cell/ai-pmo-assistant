"""AI PMO Assistant（Phase 1）の画面。起動：streamlit run app/main.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pmo import db  # noqa: E402
from pmo.claude_runner import ClaudeCliRunner  # noqa: E402
from pmo.config import ROOT, api_key_present, get_settings  # noqa: E402
from pmo.extract import CLASSIFICATIONS, ITEM_TYPES, analyze, analyze_pasted, build_prompt  # noqa: E402
from pmo.ingest import ingest_files, ingest_notion  # noqa: E402
from pmo.notion_reader import NotionReader  # noqa: E402
from pmo.review import NEW_ITEM, ReviewError, adopt, reject  # noqa: E402

st.set_page_config(page_title="AI PMO Assistant", page_icon="🧭", layout="wide")
settings = get_settings()


@st.cache_resource
def get_conn():
    # Streamlit は操作ごとに別のスレッドで動くことがあるため、スレッドをまたいで使える接続にする
    return db.connect(settings.db_path, check_same_thread=False)


conn = get_conn()
STATUS_JA = {"pending": "未確認", "adopted": "採用", "rejected": "却下", "invalid": "点検で不正"}
RUN_JA = {"running": "実行中", "success": "成功", "partial": "一部失敗", "failed": "失敗"}


def link(url: str) -> str:
    if url.startswith("http"):
        return f"[出典を開く]({url})"
    return f"出典：`{url}`"


# ---------------- 横の欄 ----------------
with st.sidebar:
    st.title("🧭 AI PMO Assistant")
    st.caption("Phase 1：取り込む → 抽き出す → 確かめて採用する")
    page = st.radio("画面", ["議事録", "抽出の確認", "処理の履歴"], label_visibility="collapsed")
    if api_key_present():
        st.error("ANTHROPIC_API_KEY が設定されています。API 課金を防ぐため、Claude Code の呼び出しは止めます。")
    st.divider()
    st.subheader("取り込み")
    if st.button("試験用の議事録10件を取り込む", use_container_width=True):
        res = ingest_files(conn, list((ROOT / "samples" / "meetings").glob("*.md")))
        st.toast(f"{RUN_JA[res['status']]}：新規 {res['new']}・変更 {res['new_version']}・変化なし {res['unchanged']}")
    notion_ready = bool(settings.notion_token and settings.notion_database_id)
    if st.button("Notion から取り込む（読み取りのみ）", use_container_width=True, disabled=not notion_ready,
                 help=None if notion_ready else ".env に NOTION_TOKEN と NOTION_DATABASE_ID を設定すると使えます"):
        with st.spinner("Notion を読んでいます…"):
            res = ingest_notion(conn, NotionReader(settings.notion_token), settings.notion_database_id,
                                settings.notion_project_property, settings.notion_date_property)
        msg = (f"{RUN_JA[res['status']]}：一覧 {res['listed']}・新規 {res['new']}・変更 {res['new_version']}・"
               f"更新なし {res['skipped_not_edited']}・取得できないブロック {res['unretrieved_blocks']}")
        (st.success if res["status"] == "success" else st.error)(msg)


# ---------------- 画面1：議事録 ----------------
def page_sources():
    st.header("議事録の一覧と詳細")
    df = pd.read_sql_query(
        """SELECT s.source_id, s.project AS プロジェクト, s.meeting_date AS 会議日, s.title AS タイトル,
                  (SELECT COUNT(*) FROM source_versions v WHERE v.source_id=s.source_id) AS 版,
                  (SELECT MAX(analyzed_at) FROM source_versions v WHERE v.source_id=s.source_id) AS 分析日時,
                  (SELECT COUNT(*) FROM extractions e JOIN source_versions v ON v.version_id=e.version_id
                     WHERE v.source_id=s.source_id AND e.review_status='pending') AS 未確認,
                  CASE s.archived WHEN 1 THEN 'ゴミ箱・見つからない' ELSE '' END AS 状態
           FROM sources s ORDER BY s.meeting_date DESC, s.title""", conn)
    if df.empty:
        st.info("まだ議事録がありません。左の「取り込み」から取り込んでください。")
        return
    df["分析日時"] = df["分析日時"].fillna("未分析")
    projects = ["すべて"] + sorted(df["プロジェクト"].unique())
    pj = st.selectbox("プロジェクト", projects)
    view = df if pj == "すべて" else df[df["プロジェクト"] == pj]
    st.dataframe(view.drop(columns=["source_id"]), hide_index=True, use_container_width=True)

    labels = {r.source_id: f"{r.会議日 or '日付不明'}｜{r.タイトル}" for r in view.itertuples()}
    sid = st.selectbox("詳細を見る議事録", list(labels), format_func=labels.get)
    src = conn.execute("SELECT * FROM sources WHERE source_id=?", (sid,)).fetchone()
    versions = conn.execute("SELECT * FROM source_versions WHERE source_id=? ORDER BY version_id DESC",
                            (sid,)).fetchall()
    st.markdown(f"**{src['title']}**　プロジェクト：{src['project']}　会議日：{src['meeting_date'] or '不明'}　"
                f"{link(src['url'])}")
    vlabels = {v["version_id"]: f"版 {v['version_id']}（取得 {v['fetched_at']}）" for v in versions}
    vid = st.selectbox("版", list(vlabels), format_func=vlabels.get) if len(versions) > 1 else versions[0]["version_id"]
    v = next(x for x in versions if x["version_id"] == vid)
    unretrieved = json.loads(v["unretrieved_blocks"])
    if unretrieved:
        st.warning(f"取得できなかったブロックが {len(unretrieved)} 件あります（本文の「取得できず」）。")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("原文")
        st.text_area("原文", v["body_text"], height=520, disabled=True, label_visibility="collapsed")
    with c2:
        st.subheader("抽き出し")
        b1, b2 = st.columns(2)
        if b1.button("Claude Code で分析", type="primary", use_container_width=True, disabled=api_key_present()):
            with st.spinner("Claude Code で分析しています（数分かかることがあります）…"):
                res = analyze(conn, vid, ClaudeCliRunner())
            if res["status"] == "success":
                st.success(f"候補 {res['saved']} 件・点検で不正 {res['invalid']} 件・重複で省略 {res['duplicate_skipped']} 件")
            else:
                st.error(f"失敗：{res['error']}　下の「手で貼る」も使えます。")
        if v["analyzed_at"]:
            b2.caption(f"分析済み：{v['analyzed_at']}")
        with st.expander("手で貼る（Claude Code が動かない時の予備）"):
            st.caption("① 下の指示文をコピーして Claude に貼る　② 返ってきた JSON を下の欄に貼って保存")
            st.code(build_prompt(conn, vid), language=None)
            pasted = st.text_area("Claude の返事（JSON）", height=150)
            if st.button("貼った結果を保存"):
                res = analyze_pasted(conn, vid, pasted)
                (st.success if res["status"] == "success" else st.error)(
                    f"{RUN_JA[res['status']]}：{res.get('error') or str(res.get('saved', 0)) + ' 件を保存'}")
        if v["meeting_json"]:
            m = json.loads(v["meeting_json"])
            st.caption(f"参加者：{'、'.join(m.get('participants') or []) or '不明'}　目的：{m.get('purpose') or '不明'}")
        for e in conn.execute("SELECT * FROM extractions WHERE version_id=? ORDER BY extraction_id", (vid,)):
            render_extraction(e, compact=True)


def render_extraction(e, compact=False):
    head = (f"**{ITEM_TYPES.get(e['item_type'], e['item_type'])}**｜{e['content']}　"
            f"`{STATUS_JA[e['review_status']]}`")
    with st.container(border=True):
        st.markdown(head)
        st.caption(f"担当：{e['owner']}　期限：{e['due']}{'（' + e['due_date'] + '）' if e['due_date'] else ''}　"
                   f"状態：{e['status']}　分類：{CLASSIFICATIONS.get(e['classification'], e['classification'])}")
        st.markdown(f"> {e['quote']}")
        notes = json.loads(e["notes"])
        if notes:
            st.caption("点検：" + "／".join(notes))
        if e["matched_item_id"] and not compact:
            st.caption(f"既存の項目 #{e['matched_item_id']} と同じ可能性（{e['match_reason']}）")


# ---------------- 画面2：抽出の確認 ----------------
def page_review():
    st.header("抽出の確認（採用・却下）")
    tab1, tab2 = st.tabs(["未確認の候補", "確定した項目"])
    with tab1:
        projects = [r[0] for r in conn.execute("SELECT DISTINCT project FROM sources ORDER BY 1")]
        pj = st.selectbox("プロジェクト", ["すべて"] + projects, key="rv_pj")
        show_invalid = st.checkbox("点検で不正とされた候補も表示する")
        statuses = ("pending", "invalid") if show_invalid else ("pending",)
        q = f"""SELECT e.*, s.project, s.title, s.url, s.meeting_date FROM extractions e
                JOIN source_versions v ON v.version_id=e.version_id JOIN sources s ON s.source_id=v.source_id
                WHERE e.review_status IN ({','.join('?' * len(statuses))})"""
        params = list(statuses)
        if pj != "すべて":
            q += " AND s.project=?"
            params.append(pj)
        rows = conn.execute(q + " ORDER BY s.meeting_date, e.extraction_id", params).fetchall()
        st.caption(f"{len(rows)} 件")
        last_source = None
        for e in rows:
            if e["version_id"] != last_source:
                last_source = e["version_id"]
                st.markdown(f"##### {e['meeting_date'] or '日付不明'}｜{e['title']}（{e['project']}）　{link(e['url'])}")
            render_extraction(e)
            if e["review_status"] != "pending":
                continue
            items = conn.execute("SELECT item_id, content FROM items WHERE project=? AND item_type=? ORDER BY item_id",
                                 (e["project"], e["item_type"])).fetchall()
            options = ["auto", NEW_ITEM] + [str(i["item_id"]) for i in items]
            names = {"auto": "自動（似た項目があれば結び付ける）", NEW_ITEM: "新しい項目にする",
                     **{str(i["item_id"]): f"#{i['item_id']} {i['content']}" for i in items}}
            c1, c2, c3 = st.columns([3, 1, 1])
            choice = c1.selectbox("結び付け", options, format_func=names.get, key=f"lk{e['extraction_id']}",
                                  label_visibility="collapsed")
            if c2.button("採用", key=f"ad{e['extraction_id']}", type="primary", use_container_width=True):
                try:
                    item_id = adopt(conn, e["extraction_id"], None if choice == "auto" else choice)
                    st.toast(f"項目 #{item_id} に採用しました")
                    st.rerun()
                except ReviewError as err:
                    st.error(str(err))
            if c3.button("却下", key=f"rj{e['extraction_id']}", use_container_width=True):
                reject(conn, e["extraction_id"])
                st.rerun()
    with tab2:
        df = pd.read_sql_query(
            """SELECT i.item_id AS 番号, i.project AS プロジェクト, i.item_type AS 種類, i.content AS 内容,
                      i.owner AS 担当, i.due AS 期限, i.due_date AS 期限日, i.status AS 状態,
                      s.title AS 最後に出た議事録, s.url AS 出典
               FROM items i JOIN sources s ON s.source_id=i.last_source_id ORDER BY i.project, i.item_type""", conn)
        df["種類"] = df["種類"].map(ITEM_TYPES)
        st.dataframe(df, hide_index=True, use_container_width=True)
        if not df.empty:
            iid = st.selectbox("変化の履歴を見る項目", df["番号"], format_func=lambda x: f"#{x}")
            ev = pd.read_sql_query(
                """SELECT ev.created_at AS 記録日時, ev.event_type AS 変化, ev.old_value AS 前, ev.new_value AS 後,
                          s.title AS 議事録 FROM item_events ev JOIN sources s ON s.source_id=ev.source_id
                   WHERE ev.item_id=? ORDER BY s.meeting_date, ev.event_id""", conn, params=(int(iid),))
            st.dataframe(ev, hide_index=True, use_container_width=True)


# ---------------- 画面3：処理の履歴 ----------------
def page_runs():
    st.header("処理の履歴")
    df = pd.read_sql_query(
        "SELECT run_id AS 番号, kind AS 処理, started_at AS 開始, finished_at AS 終了, status AS 結果, "
        "detail AS 内容, error AS エラー FROM runs ORDER BY run_id DESC LIMIT 500", conn)
    df["結果"] = df["結果"].map(RUN_JA)
    st.dataframe(df, hide_index=True, use_container_width=True)


{"議事録": page_sources, "抽出の確認": page_review, "処理の履歴": page_runs}[page]()
