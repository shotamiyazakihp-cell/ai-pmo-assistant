"""完了の条件を数える：試験用の議事録の正解データ（samples/gold）と、保存された抽出を比べる。

ここで出す一致率は「機械的な目安」。最終の判定（9割以上が原文と合うか）はオーナーが画面で確かめる。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .config import ROOT, UNKNOWN
from .textutil import similarity

GOLD_DIR = ROOT / "samples" / "gold"
MATCH = 0.6


class GoldRunner:
    """試験専用：Claude を呼ばず、正解データをそのまま返す（画面と流れの確認用）。"""
    name = "gold_sample"

    def __init__(self, gold_dir: Path = GOLD_DIR):
        self.gold_dir = gold_dir

    def run(self, prompt: str) -> str:
        m = re.search(r"^SOURCE_ID: (.+)$", prompt, re.M)
        if not m:
            raise ValueError("指示文に SOURCE_ID がありません")
        path = self.gold_dir / f"{m.group(1).strip()}.json"
        if not path.exists():
            from .claude_runner import RunnerError
            raise RunnerError(f"正解データがありません: {path.name}（試験用の議事録だけで使えます）")
        return path.read_text(encoding="utf-8")


def load_gold(gold_dir: Path = GOLD_DIR) -> dict[str, list[dict]]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8"))["items"] for p in sorted(gold_dir.glob("*.json"))}


def evaluate(conn: sqlite3.Connection, gold_dir: Path = GOLD_DIR) -> dict:
    gold = load_gold(gold_dir)
    report: dict = {"sources_expected": len(gold), "per_source": {}}
    srcs = {r["source_id"]: r for r in conn.execute("SELECT * FROM sources")}
    report["sources_missing"] = sorted(set(gold) - set(srcs))
    report["sources_total"] = len(srcs)
    dup = conn.execute(
        "SELECT source_id, content_hash, COUNT(*) c FROM source_versions GROUP BY 1,2 HAVING c>1"
    ).fetchall()
    report["duplicate_versions"] = len(dup)

    gold_total = matched_total = extra_total = fabricated = invalid_total = 0
    for sid, items in gold.items():
        v = conn.execute("SELECT MAX(version_id) v FROM source_versions WHERE source_id=?", (sid,)).fetchone()["v"]
        ex = [dict(r) for r in conn.execute(
            "SELECT * FROM extractions WHERE version_id=?", (v,))] if v else []
        invalid_total += sum(1 for e in ex if e["review_status"] == "invalid")
        usable = [e for e in ex if e["review_status"] != "invalid"]
        used: set[int] = set()
        matched = 0
        for g in items:
            best, score = None, 0.0
            for e in usable:
                if e["extraction_id"] in used or e["item_type"] != g["item_type"]:
                    continue
                s = max(similarity(e["content"], g["content"]), similarity(e["quote"], g["quote"]))
                if s > score:
                    best, score = e, s
            if best is not None and score >= MATCH:
                used.add(best["extraction_id"])
                matched += 1
                for k in ("owner", "due"):
                    # 正解が「不明」なのに値が入っていれば作り話として数える
                    if g[k] == UNKNOWN and best[k] != UNKNOWN:
                        fabricated += 1
        extra = len(usable) - len(used)
        report["per_source"][sid] = {"gold": len(items), "matched": matched, "extra": extra}
        gold_total += len(items)
        matched_total += matched
        extra_total += extra
    report.update({
        "gold_items": gold_total,
        "matched_items": matched_total,
        "match_rate": round(matched_total / gold_total, 3) if gold_total else 0.0,
        "extra_items": extra_total,
        "invalid_items": invalid_total,
        "fabricated_owner_or_due": fabricated,
    })
    return report
