# AI PMO Assistant

議事録（Notion）を読み取り専用で取り込み、Claude Code（Claude Pro の枠）で決定・TODO・課題・リスクを抽き出し、
人が確かめて採用したものを確定データにして、日々の業務整理・資料の下書き・横断分析・改善までを支援する個人用ツール。
Windows 11 の PC の中だけで動く。追加費用 0 円（Claude API・従量課金・有料サービスは使わない）。

## いちばん簡単な始め方（PC）

1. GitHub の画面でこのリポジトリの `scripts/pc_setup_and_check.bat` を開き、右上の「Download raw file」（下向き矢印）で保存する
2. 保存した `pc_setup_and_check.bat` をダブルクリックする（何度実行しても安全）

バッチがすること：最新コードの取得（初回は `Documents\ai-pmo-assistant` に clone）→ ANTHROPIC_API_KEY が無いことの確認（あれば止まる）→
Python の確認 → 準備（.venv と部品）→ テスト → `pmo check` → 試験用の議事録1件を `claude -p` で分析 →
（.env に Notion の鍵があり `NOTION_DATABASE_KIND=test` の時だけ）試験用データベースの読み取り → BitLocker の状態 → 結果のまとめ → 画面の起動。
進み具合は日本語で表示し、失敗したら「どこで止まったか」と「次の一手」を出す。記録は `logs\pc_check_日時.log`（鍵は書かない）。
BitLocker は管理者権限が要るため、`scripts\check_bitlocker.bat` を右クリック→「管理者として実行」で別に確かめられる。

うまくいった時の見え方：手順ごとに緑の「→ 成功」が並び、最後に「結果のまとめ」が出て、ブラウザに画面（左に10個の画面の名前）が開く。
Claude Code や Notion の設定が無い所は黄色の「→ 飛ばした」になる（失敗ではない）。

## 画面（spec 9章の10画面）

| 画面 | できること |
|---|---|
| ダッシュボード | 期限超過・今日／近い期限・放置ぎみ・担当未定・未決事項（Python が数える。AI の推測は入らない） |
| プロジェクト一覧 | プロジェクトごとの件数・最近の決定・直近の会議（進捗率は根拠が無いので出さない） |
| 議事録 | 一覧と詳細（原文と抽出を並べ、出典に飛べる）、Claude Code で分析／手で貼る |
| 抽出の確認（承認） | 候補の採用・却下。採用したものだけが確定データになり、変化は履歴に残る |
| 課題・リスク管理／TODO 管理 | 確定した項目の一覧と、Excel で開ける CSV（課題管理表・リスク管理表・TODO 一覧） |
| AI 分析結果 | 横断分析（再発・放置・食い違い・潜在リスク・次の行動・確認すべきこと）。根拠の項目番号が無い指摘は保存しない |
| 資料作成 | 週次・月次・状況報告、アジェンダ、会議準備、議事録の清書、WBS 更新案、各種メールの下書き。ナレッジを参照し、引用の番号と割合の数字を点検 |
| ナレッジ検索 | 議事録・項目・ナレッジ（資料作成知識・note）の横断検索。別プロジェクト固有のルールと古い資料に印 |
| 処理の履歴・設定 | 処理の履歴、品質（採用率・不正率・見落とし・改善案）、設定の変更（理由が必須で履歴に残る） |

## 毎日の使い方

1. `Documents\ai-pmo-assistant` で `.\.venv\Scripts\Activate.ps1` → `streamlit run app/main.py`（または bat をもう一度ダブルクリック）
2. 「議事録」→ 左の「Notion から取り込む」→ 会議日の古い順に「Claude Code で分析」→「抽出の確認」で採用・却下
3. 「ダッシュボード」で今日やることを見る。必要に応じて「資料作成」「AI 分析結果」
4. 自動実行（任意）：`powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1`（毎時の更新確認と毎朝の日次レポート。Claude は使わない。解除は `-Remove`）

## 実データの扱い（既定は止める）

設定「実データを扱う」が 0（既定）の間は、本物の議事録・ドライブの PMO ナレッジ・note の原稿を取り込まず、Claude にも渡さない。
勤務先の規程を確かめた後、「処理の履歴・設定」で理由を書いて 1 にする。試験用のデータ（samples）はいつでも使える。

## Notion につなぐ（オーナー本人の操作）

1. https://www.notion.so/profile/integrations で内部インテグレーションを作る。権限は「コンテンツを読み取る」だけ
2. 試験用の議事録データベースの「…」→「接続」でそのインテグレーションを追加する
3. `.env.example` を `.env` にコピーし、`NOTION_TOKEN`・`NOTION_DATABASE_ID`・`NOTION_DATABASE_KIND=test` を入れる（`.env` は Git に入らない）

## データの置き場所と触らないもの

- PC の `data\pmo.db`（SQLite：原文の版・抽出・確定データ・履歴・ナレッジ・下書き）、`data\reports`（日次レポート）、`logs`、`.env`。どれも Git に入らない
- 触らないもの：Notion の原文（読むだけ。書き込みは送る前に止める）、KB アプリと Supabase の kb の区画（読まない・書かない）

## コマンド

`python -m pmo --help` を参照（check / ingest-samples / ingest-notion / analyze / evaluate / import-knowledge / import-note / search / daily / document / analyze-project / auto / quality）。

## 構成

`src/pmo/`：notion_reader（読み取り専用）・ingest（差分と重複）・extract（抽出と点検）・compare（照合）・review（採用）・
knowledge（Phase 2）・practice（Phase 3）・automate（Phase 4）・quality（Phase 5）・settings_store（実データのスイッチと変更履歴）・
claude_runner（`claude -p`、API キーがあれば止める）。`app/`：画面。`scripts/`：PC 用のバッチ。`samples/`：試験用の架空のデータと、Claude の盲検の結果。
