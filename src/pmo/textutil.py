"""文字の正規化・指紋・日付の読み取り（ルールで処理するもの）。"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher

_PUNCT = re.compile(r"[\s、。・,.!?！？「」『』()（）\[\]【】:：;；\"'`~〜\-ー―]+")


def normalize(text: str) -> str:
    """比較用：全角半角をそろえ、空白と記号を取る。"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    return _PUNCT.sub("", t)


def normalize_ws(text: str) -> str:
    """引用の照合用：全角半角をそろえ、空白だけ詰める（記号は残す）。"""
    t = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", t).strip()


def content_hash(text: str) -> str:
    """本文の指紋。行末の空白や改行コードの違いでは変わらない。"""
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").split("\n")]
    canonical = "\n".join(lines).strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(normalize(p) for p in parts).encode("utf-8")).hexdigest()[:32]


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def find_quote(body: str, quote: str) -> int | None:
    """引用が原文にあれば位置（正規化後の文字位置）を返す。無ければ None。"""
    q = normalize_ws(quote)
    if not q:
        return None
    pos = normalize_ws(body).find(q)
    return pos if pos >= 0 else None


def contains(haystack: str, needle: str) -> bool:
    return bool(needle) and normalize(needle) in normalize(haystack)


_DATE_PATTERNS = [
    re.compile(r"(?P<y>\d{4})[-/年](?P<m>\d{1,2})[-/月](?P<d>\d{1,2})日?"),
    re.compile(r"(?P<m>\d{1,2})月(?P<d>\d{1,2})日"),
    re.compile(r"(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?![\d/])"),
]


def parse_due(due: str, meeting_date: str | None) -> str | None:
    """期限の文字から日付を読む。読めない時（「来週中」など）は None。年は会議日から補う。"""
    if not due:
        return None
    t = unicodedata.normalize("NFKC", due)
    base = None
    if meeting_date:
        try:
            base = date.fromisoformat(meeting_date)
        except ValueError:
            base = None
    for pat in _DATE_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        gd = m.groupdict()
        month, day = int(gd["m"]), int(gd["d"])
        if gd.get("y"):
            year = int(gd["y"])
        elif base:
            year = base.year
            # 会議が12月で期限が1月なら翌年
            if month < base.month and base.month - month > 6:
                year += 1
        else:
            return None
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None
