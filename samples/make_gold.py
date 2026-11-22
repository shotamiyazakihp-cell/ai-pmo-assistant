"""試験用の議事録の正解データ（samples/gold/*.json）を作る。引用が原文にあるかも確かめる。"""
import json
from pathlib import Path

HERE = Path(__file__).parent
U = "不明"


def it(t, content, quote, owner=U, due=U, status=U, cls="fact"):
    return {"item_type": t, "content": content, "owner": owner, "due": due, "status": status,
            "classification": cls, "quote": quote, "same_as_item_id": None}


GOLD = {
    "sample-alpha-01": [
        it("decision", "要件定義は10月末までに完了とする", "要件定義は10月末までに完了とすることで合意した。", cls="decision", status="合意"),
        it("issue", "現行システムの仕様書が古い", "現行システムの仕様書が古く、実際の動きと合っていない箇所が多い。"),
        it("todo", "現行帳票の一覧を作成する", "佐藤さんが現行帳票の一覧を10/9までに作成する。", owner="佐藤", due="10/9", status="未着手"),
        it("todo", "データ移行方針を整理する", "データ移行方針を誰かが整理する必要がある。担当は決まらなかった。", status="担当未定"),
        it("risk", "田中部長の兼務によりレビューが遅れる", "田中部長が11月から別案件と兼務になるため、レビューが遅れるおそれがある。"),
    ],
    "sample-alpha-02": [
        it("todo", "現行帳票の一覧を作成する", "現行帳票の一覧は佐藤さんが作成し、完了した。", owner="佐藤", status="完了"),
        it("todo", "データ移行方針を整理する", "データ移行方針の整理は、まだ担当が決まっていない。", status="担当未定"),
        it("decision", "新システムの帳票は30種類に絞る", "新システムで作る帳票は30種類に絞ることを決定した。", cls="decision", status="決定"),
        it("todo", "新システムの画面一覧をまとめる", "鈴木さんが新システムの画面一覧を来週中にまとめる。", owner="鈴木", due="来週中", status="未着手"),
        it("issue", "帳票を絞るなら利用部門への説明会が必要", "佐藤さんの意見：帳票を絞るなら、利用部門への説明会が必要だと思う。", cls="opinion"),
    ],
    "sample-alpha-03": [
        it("decision", "要件定義の完了を11月中旬に延期する", "要件定義の完了を10月末から11月中旬に延期することを決定した。", cls="decision", status="決定"),
        it("todo", "新システムの画面一覧をまとめる", "鈴木さんの画面一覧はまだ途中で、遅れている。", owner="鈴木", status="遅れ"),
        it("issue", "現行システムの仕様書が古い", "現行システムの仕様書が古い問題は解消していない。", status="未解決"),
        it("open_question", "帳票の出力形式をPDFにするかExcelにするか", "帳票の出力形式をPDFにするかExcelにするかは未決。", status="未決"),
    ],
    "sample-alpha-04": [
        it("issue", "田中部長の兼務で要件定義書のレビューが行われていない", "田中部長の兼務が始まり、要件定義書のレビューがまだ行われていない。", status="未対応"),
        it("todo", "データ移行方針を整理する", "山本さんがデータ移行方針を11/6までに整理する。", owner="山本", due="11/6", status="未着手"),
        it("todo", "新システムの画面一覧をまとめる", "鈴木さんの画面一覧は完了した。", owner="鈴木", status="完了"),
        it("decision", "帳票の出力形式はPDFとする", "帳票の出力形式はPDFとすることを決定した。", cls="decision", status="決定"),
    ],
    "sample-beta-01": [
        it("decision", "製品はクラウドサービス「ラクケイ」を採用する", "製品はクラウドサービス「ラクケイ」を採用することを決定した。", cls="decision", status="決定"),
        it("todo", "承認フローの現状を整理する", "高橋さんが承認フローの現状を10/21までに整理する。", owner="高橋", due="10/21", status="未着手"),
        it("todo", "月末の締め処理に間に合うように導入する（経理部の依頼）", "経理部からの依頼：月末の締め処理に間に合うように導入してほしい。"),
        it("open_question", "交通費の精算をラクケイで扱うかどうか", "交通費の精算をラクケイで扱うかどうかは未決。", status="未決"),
    ],
    "sample-beta-02": [
        it("todo", "承認フローの現状を整理する", "承認フローの整理は高橋さんが対応中。", owner="高橋", status="対応中"),
        it("issue", "本稼働の時期の認識が経理部（12月）と情報システム部（1月）で合っていない", "本稼働の時期について、経理部は12月と考えているが、情報システム部は1月と考えており、認識が合っていない。"),
        it("todo", "本稼働の時期を経理部長に確認する", "伊藤さんが本稼働の時期を経理部長に確認する。期限は10/16。", owner="伊藤", due="10/16", status="未着手"),
    ],
    "sample-beta-03": [
        it("decision", "本稼働は1月とする", "経理部長に確認した結果、本稼働は1月とすることを決定した。", cls="decision", status="決定"),
        it("todo", "承認フローの現状を整理する", "承認フローの整理は完了した。", status="完了"),
        it("open_question", "交通費の精算をラクケイで扱うかどうか", "交通費の精算をラクケイで扱うかどうかは、引き続き未決。", status="未決"),
        it("risk", "1月の本稼働は繁忙期に近く利用者への説明が不十分になる", "1月の本稼働は年度末の繁忙期に近く、利用者への説明が十分にできないおそれがある。"),
    ],
    "sample-gamma-01": [
        it("decision", "移行するページは過去1年以内に更新されたものだけとする", "移行するページは、過去1年以内に更新されたものだけとすることを決定した。", cls="decision", status="決定"),
        it("todo", "旧ポータルのページ棚卸しを行う", "中村さんが旧ポータルのページ棚卸しを10/23までに行う。", owner="中村", due="10/23", status="未着手"),
        it("risk", "旧ポータルのサポート終了（12月）で移行が遅れると障害時に対応できない", "旧ポータルのサポートが12月で終了するため、移行が遅れると障害時に対応できなくなるおそれがある。"),
    ],
    "sample-gamma-02": [
        it("issue", "添付ファイルの合計容量が新ポータルの上限を超えそう", "添付ファイルの合計容量が新ポータルの上限を超えそうだ。"),
        it("todo", "添付ファイルの容量を確認する", "小林さんが添付ファイルの容量を確認する。なるべく早く。", owner="小林", due="なるべく早く", status="未着手"),
        it("todo", "旧ポータルのページ棚卸しを行う", "ページ棚卸しは中村さんが対応中で、半分ほど終わった。", owner="中村", status="対応中"),
    ],
    "sample-gamma-03": [
        it("todo", "旧ポータルのページ棚卸しを行う", "ページ棚卸しは完了した。", status="完了"),
        it("issue", "添付ファイルの合計容量が新ポータルの上限を超えそう", "添付ファイルの容量が上限を超える問題は、まだ解決していない。", status="未解決"),
        it("decision", "新ポータルへの切替日は12/1とする", "新ポータルへの切替日は12/1とすることを決定した。", cls="decision", status="決定"),
    ],
}

if __name__ == "__main__":
    out = HERE / "gold"
    out.mkdir(exist_ok=True)
    bodies = {}
    for p in (HERE / "meetings").glob("*.md"):
        text = p.read_text(encoding="utf-8")
        sid = [l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("source_id:")][0]
        bodies[sid] = text
    total = 0
    for sid, items in GOLD.items():
        for i in items:
            assert i["quote"] in bodies[sid], (sid, i["quote"])
            for k in ("owner", "due"):
                assert i[k] == U or i[k] in i["quote"], (sid, k, i)
        (out / f"{sid}.json").write_text(
            json.dumps({"meeting": {"title": sid}, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
        total += len(items)
    print(f"{len(GOLD)} 件の議事録、{total} 項目の正解データを書き出した")
