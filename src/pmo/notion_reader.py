"""Notion の読み取り専用クライアント。

読み取りの呼び出し（ページ・ブロックの取得、データベースの検索、検索）だけを許し、
それ以外の URL や書き込み（PATCH / DELETE、ページ作成）は送る前に止める。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import requests

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"  # 版を固定する

# 許す呼び出し（メソッド, パスの正規表現）。POST の2つはどちらも読み取り（検索）
_ALLOWED = [
    ("GET", re.compile(r"^/pages/[0-9a-fA-F-]{32,36}$")),
    ("GET", re.compile(r"^/blocks/[0-9a-fA-F-]{32,36}/children$")),
    ("POST", re.compile(r"^/databases/[0-9a-fA-F-]{32,36}/query$")),
    ("POST", re.compile(r"^/search$")),
]

# 本文に入れるブロックの種類（rich_text を持つもの）
_TEXT_BLOCKS = {
    "paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item",
    "numbered_list_item", "to_do", "toggle", "quote", "callout", "code",
}
_SKIP_BLOCKS = {"divider", "table_of_contents", "breadcrumb", "column_list", "column", "synced_block", "table"}


class NotionError(Exception):
    pass


class WriteNotAllowed(NotionError):
    pass


@dataclass
class PageBody:
    text: str
    unretrieved: list[dict] = field(default_factory=list)  # 取得できなかったブロック


class NotionReader:
    def __init__(self, token: str, session: requests.Session | None = None,
                 min_interval: float = 0.34, sleep: Callable[[float], None] = time.sleep,
                 max_retries: int = 3):
        if not token:
            raise NotionError("NOTION_TOKEN が設定されていません")
        self._token = token
        self._s = session or requests.Session()
        self._min_interval = min_interval  # 1秒3回まで
        self._sleep = sleep
        self._last = 0.0
        self._max_retries = max_retries

    # ---- 低レベル ----
    def _request(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> dict:
        if not any(m == method and rx.match(path) for m, rx in _ALLOWED):
            raise WriteNotAllowed(f"読み取り専用のため送信しません: {method} {path}")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            resp = self._s.request(method, API + path, headers=headers, params=params, json=json, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self._max_retries:
                    break
                retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                self._sleep(retry_after)
                continue
            if resp.status_code >= 400:
                try:
                    msg = resp.json().get("message", resp.text)
                except ValueError:
                    msg = resp.text
                raise NotionError(f"{resp.status_code} {method} {path}: {msg}")
            return resp.json()
        raise NotionError(f"{resp.status_code} {method} {path}: 再試行の上限に達しました")

    # ---- 一覧（ページネーション対応） ----
    def query_database(self, database_id: str) -> Iterator[dict]:
        """データベースの全ページを更新日時の新しい順に返す。"""
        body: dict[str, Any] = {
            "page_size": 100,
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
        }
        while True:
            data = self._request("POST", f"/databases/{database_id}/query", json=body)
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            body["start_cursor"] = data["next_cursor"]

    def search_pages(self) -> Iterator[dict]:
        """共有されたページを検索で返す（すべて返る保証はない点に注意）。"""
        body: dict[str, Any] = {
            "page_size": 100,
            "filter": {"property": "object", "value": "page"},
            "sort": {"timestamp": "last_edited_time", "direction": "descending"},
        }
        while True:
            data = self._request("POST", "/search", json=body)
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            body["start_cursor"] = data["next_cursor"]

    def get_page(self, page_id: str) -> dict:
        return self._request("GET", f"/pages/{page_id}")

    def _children(self, block_id: str) -> Iterator[dict]:
        params: dict[str, Any] = {"page_size": 100}
        while True:
            data = self._request("GET", f"/blocks/{block_id}/children", params=params)
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            params["start_cursor"] = data["next_cursor"]

    # ---- 本文 ----
    def get_body(self, page_id: str) -> PageBody:
        lines: list[str] = []
        unretrieved: list[dict] = []
        self._walk(page_id, 0, lines, unretrieved)
        return PageBody("\n".join(lines).strip(), unretrieved)

    def _walk(self, block_id: str, depth: int, lines: list[str], unretrieved: list[dict]) -> None:
        for block in self._children(block_id):
            btype = block.get("type", "")
            indent = "  " * depth
            if btype in _TEXT_BLOCKS:
                text = rich_text_plain(block.get(btype, {}).get("rich_text", []))
                if btype.startswith("heading_"):
                    text = "#" * int(btype[-1]) + " " + text
                elif btype in ("bulleted_list_item", "numbered_list_item"):
                    text = "- " + text
                elif btype == "to_do":
                    text = ("- [x] " if block["to_do"].get("checked") else "- [ ] ") + text
                lines.append(indent + text)
            elif btype == "table_row":
                cells = [rich_text_plain(c) for c in block["table_row"].get("cells", [])]
                lines.append(indent + "| " + " | ".join(cells) + " |")
            elif btype == "child_page":
                lines.append(indent + f"[子ページ] {block['child_page'].get('title', '')}")
            elif btype in _SKIP_BLOCKS:
                pass
            else:
                # 未対応・権限なしなどで中身が読めないブロックは「取得できず」と記録する
                unretrieved.append({"block_id": block.get("id"), "type": btype or "unknown"})
                lines.append(indent + f"[取得できず: {btype or 'unknown'}]")
            if block.get("has_children") and btype != "child_page":
                try:
                    self._walk(block["id"], depth + 1, lines, unretrieved)
                except NotionError as e:
                    unretrieved.append({"block_id": block.get("id"), "type": btype, "error": str(e)})
                    lines.append(indent + "  [取得できず: 子ブロック]")


def rich_text_plain(rich: list[dict]) -> str:
    return "".join(r.get("plain_text", "") for r in rich or [])


def page_title(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return rich_text_plain(prop.get("title", []))
    return ""


def page_property_text(page: dict, name: str, resolve_relation: Callable[[str], str] | None = None) -> str | None:
    """プロパティの値を文字にする。無い・空なら None。"""
    prop = page.get("properties", {}).get(name)
    if not prop:
        return None
    t = prop.get("type")
    v = prop.get(t)
    if t in ("rich_text", "title"):
        return rich_text_plain(v) or None
    if t == "select":
        return v.get("name") if v else None
    if t == "multi_select":
        return ", ".join(o["name"] for o in v) or None
    if t == "date":
        return (v or {}).get("start")
    if t == "relation":
        if not v:
            return None
        if resolve_relation:
            return ", ".join(resolve_relation(r["id"]) for r in v)
        return ", ".join(r["id"] for r in v)
    return None
