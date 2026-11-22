# 2026-11-22 の盲検の抽出（クラウド）

正解データ（samples/gold）を見せていない Claude（Claude Code のクラウドのセッション内の補助役。Claude Pro の範囲、API なし）に、
画面と同じ指示文（`pmo.extract.build_prompt`）だけを渡して作らせた返事。各会議は独立に処理し、既存の項目は「なし」の状態。

PC の `claude -p` で動かしたものではない。PC での確認は別に行う。
`tests/test_claude_run.py` がこの返事を点検と評価に流し直す。
