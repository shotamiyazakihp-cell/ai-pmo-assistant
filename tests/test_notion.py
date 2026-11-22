"""Notion の読み取りを、偽の Notion（ネットにつながない）で確かめる。"""
import pytest

from pmo.ingest import ingest_notion
from pmo.notion_reader import NotionError, NotionReader, WriteNotAllowed

DB = "11111111111111111111111111111111"
P1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
P2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
P3 = "cccccccc-cccc-cccc-cccc-cccccccccccc"
PROJ = "dddddddd-dddd-dddd-dddd-dddddddddddd"
B_TOGGLE = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"


def rt(text):
    return [{"plain_text": text}]


def page(pid, title, edited, archived=False):
    return {
        "id": pid, "url": f"https://www.notion.so/{pid.replace('-', '')}", "last_edited_time": edited,
        "archived": archived, "in_trash": archived,
        "properties": {
            "タイトル": {"type": "title", "title": rt(title)},
            "録音日時": {"type": "date", "date": {"start": "2026-10-05T10:00:00+09:00"}},
            "関連プロジェクト": {"type": "relation", "relation": [{"id": PROJ}]},
        },
    }


def block(btype, text=None, has_children=False, bid=None, **extra):
    b = {"id": bid or f"blk-{btype}", "type": btype, "has_children": has_children}
    if text is not None:
        b[btype] = {"rich_text": rt(text), **extra}
    else:
        b[btype] = extra
    return b


class Resp:
    def __init__(self, status, data=None, headers=None):
        self.status_code = status
        self._data = data or {}
        self.headers = headers or {}
        self.text = str(data)

    def json(self):
        return self._data


class FakeNotion:
    """必要な API だけをまねる。呼ばれた (method, path) を記録する。"""

    def __init__(self, pages, bodies, fail_429_once=False, fail_list=False, fail_page=None):
        self.pages = pages
        self.bodies = bodies
        self.calls = []
        self.fail_429_once = fail_429_once
        self.fail_list = fail_list
        self.fail_page = fail_page

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        path = url.replace("https://api.notion.com/v1", "")
        self.calls.append((method, path))
        assert headers["Notion-Version"] == "2022-06-28"
        if self.fail_429_once:
            self.fail_429_once = False
            return Resp(429, {"message": "rate limited"}, {"Retry-After": "1"})
        if path == f"/databases/{DB}/query":
            if self.fail_list:
                return Resp(500, {"message": "boom"})
            # 1件ずつページネーション
            start = int((json or {}).get("start_cursor") or 0)
            res = self.pages[start:start + 1]
            more = start + 1 < len(self.pages)
            return Resp(200, {"results": res, "has_more": more, "next_cursor": str(start + 1) if more else None})
        if path == f"/pages/{PROJ}":
            return Resp(200, page(PROJ, "販売管理刷新", "2026-01-01T00:00:00.000Z"))
        if path.startswith("/blocks/"):
            bid = path.split("/")[2]
            if bid == self.fail_page:
                return Resp(404, {"message": "Could not find block"})
            blocks = self.bodies.get(bid, [])
            start = int((params or {}).get("start_cursor") or 0)
            res = blocks[start:start + 2]  # 2件ずつ
            more = start + 2 < len(blocks)
            return Resp(200, {"results": res, "has_more": more, "next_cursor": str(start + 2) if more else None})
        return Resp(404, {"message": f"unknown {path}"})


def make_reader(fake):
    return NotionReader("secret-test-token", session=fake, sleep=lambda s: None)


BODY_P1 = [
    block("heading_2", "議題1"),
    block("bulleted_list_item", "要件定義は10月末までに完了"),
    block("to_do", "佐藤さんが帳票一覧を作る", checked=False),
    block("toggle", "詳細", has_children=True, bid=B_TOGGLE),
    block("image"),          # 未対応：中身が読めない
    block("divider"),
]


def test_body_with_pagination_nested_and_unretrieved():
    fake = FakeNotion([], {P1: BODY_P1, B_TOGGLE: [block("paragraph", "入れ子の本文")]})
    body = make_reader(fake).get_body(P1)
    assert "## 議題1" in body.text
    assert "- [ ] 佐藤さんが帳票一覧を作る" in body.text
    assert "  入れ子の本文" in body.text
    assert "[取得できず: image]" in body.text
    assert body.unretrieved == [{"block_id": "blk-image", "type": "image"}]


def test_write_requests_are_blocked_before_sending():
    fake = FakeNotion([], {})
    r = make_reader(fake)
    with pytest.raises(WriteNotAllowed):
        r._request("PATCH", f"/pages/{P1}", json={"archived": True})
    with pytest.raises(WriteNotAllowed):
        r._request("POST", "/pages", json={})
    with pytest.raises(WriteNotAllowed):
        r._request("DELETE", f"/blocks/{P1}")
    assert fake.calls == []  # 何も送っていない


def test_rate_limit_retry():
    fake = FakeNotion([], {P1: [block("paragraph", "本文")]}, fail_429_once=True)
    assert make_reader(fake).get_body(P1).text == "本文"


def test_token_required():
    with pytest.raises(NotionError):
        NotionReader("")


def _ingest(conn, fake, force=False, data_kind="test"):
    return ingest_notion(conn, make_reader(fake), DB, "関連プロジェクト", "録音日時", force=force, data_kind=data_kind)


def test_real_notion_blocked_until_enabled(conn):
    from pmo import settings_store
    from pmo.extract import build_prompt, latest_version_id
    fake = FakeNotion([page(P1, "定例1", "t1")], {P1: [block("paragraph", "本物の本文")]})
    with pytest.raises(settings_store.RealDataDisabled):
        _ingest(conn, fake, data_kind="real")
    assert fake.calls == [] and conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    with pytest.raises(ValueError):
        settings_store.change(conn, "real_data_enabled", "1", "")      # 理由なしは不可
    settings_store.change(conn, "real_data_enabled", "1", "規程を確認した（テスト）")
    assert _ingest(conn, fake, data_kind="real")["new"] == 1
    vid = latest_version_id(conn, P1)
    assert "本物の本文" in build_prompt(conn, vid)
    settings_store.change(conn, "real_data_enabled", "0", "止める（テスト）")
    with pytest.raises(settings_store.RealDataDisabled):
        build_prompt(conn, vid)                                          # 止めたら Claude にも渡さない
    assert conn.execute("SELECT COUNT(*) FROM rule_changes").fetchone()[0] == 2


def test_ingest_notion_diff_and_duplicates(conn):
    pages = [page(P1, "定例1", "2026-10-05T09:00:00.000Z"), page(P2, "定例2", "2026-10-12T09:00:00.000Z")]
    bodies = {P1: [block("paragraph", "本文1")], P2: [block("paragraph", "本文2")]}
    fake = FakeNotion(pages, bodies)
    res = _ingest(conn, fake)
    assert res["status"] == "success" and res["new"] == 2 and res["listed"] == 2
    src = conn.execute("SELECT * FROM sources WHERE source_id=?", (P1,)).fetchone()
    assert src["project"] == "販売管理刷新"          # 関連プロジェクトの名前を読めた
    assert src["meeting_date"] == "2026-10-05"
    assert src["url"].startswith("https://www.notion.so/")

    # 2回目：更新日時が同じなら本文を取りに行かない
    fake.calls.clear()
    res = _ingest(conn, fake)
    assert res["skipped_not_edited"] == 2
    assert not any(p.startswith("/blocks/") for _, p in fake.calls)

    # 更新日時が変わったが本文は同じ → 版は増えない
    pages[0]["last_edited_time"] = "2026-10-06T09:00:00.000Z"
    res = _ingest(conn, fake)
    assert res["unchanged"] == 1
    # 本文が変わった → 新しい版
    pages[0]["last_edited_time"] = "2026-10-07T09:00:00.000Z"
    bodies[P1] = [block("paragraph", "本文1（修正）")]
    res = _ingest(conn, fake)
    assert res["new_version"] == 1
    assert conn.execute("SELECT COUNT(*) FROM source_versions WHERE source_id=?", (P1,)).fetchone()[0] == 2


def test_ingest_notion_trash_and_missing(conn):
    pages = [page(P1, "定例1", "t1"), page(P2, "定例2", "t2"), page(P3, "定例3", "t3")]
    bodies = {P1: [block("paragraph", "a")], P2: [block("paragraph", "b")], P3: [block("paragraph", "c")]}
    fake = FakeNotion(pages, bodies)
    _ingest(conn, fake)
    pages[1] = page(P2, "定例2", "t2b", archived=True)  # ゴミ箱へ
    del pages[2]                                       # 一覧から消えた
    res = _ingest(conn, fake)
    assert res["archived"] == 1 and res["missing_marked"] == 1
    flags = dict(conn.execute("SELECT source_id, archived FROM sources").fetchall())
    assert flags == {P1: 0, P2: 1, P3: 1}
    # 原文の版は消さずに残す
    assert conn.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 3


def test_ingest_notion_failures_are_not_success(conn):
    fake = FakeNotion([page(P1, "定例1", "t1")], {}, fail_list=True)
    res = _ingest(conn, fake)
    assert res["status"] == "failed"
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "failed"

    fake = FakeNotion([page(P1, "定例1", "t1"), page(P2, "定例2", "t2")],
                      {P2: [block("paragraph", "b")]}, fail_page=P1)
    res = _ingest(conn, fake)
    assert res["status"] == "partial" and res["new"] == 1
    assert P1 in res["errors"][0]
    # 失敗したページは取得済みにしない
    assert conn.execute("SELECT COUNT(*) FROM source_versions WHERE source_id=?", (P1,)).fetchone()[0] == 0
