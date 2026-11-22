# AI PMO Assistant（Phase 1 MVP）

Notion の議事録を読み取り専用で取り込み、Claude Code（Claude Pro の枠）で決定・TODO・課題・リスクを抽き出し、
人が確かめて採用したものだけを確定データにする、個人用のツールです。Windows 11 の PC の中だけで動きます。

- 追加費用 0 円（Claude API・従量課金・有料サービスは使わない）
- Notion へは書き込まない（読み取りの呼び出し以外は送る前に止める）
- 担当・期限は原文の引用に書かれている時だけ入れる。無ければ「不明」
- 失敗は失敗として処理の履歴に残す

## PC での準備（PowerShell）

### 0. 確認（読むだけ。何も入れない・変えない）

```powershell
(Get-CimInstance Win32_OperatingSystem).Caption
[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)
py -0p; python --version
git --version; claude --version
Get-ChildItem Env: | Where-Object Name -like '*ANTHROPIC*' | Select-Object Name
manage-bde -status C: | Select-String "保護状態|Protection Status"   # 管理者の PowerShell が要る。エラーなら飛ばしてよい
```

`ANTHROPIC_API_KEY` が表示されたら、先に消してください（残っていると Claude Code が API 料金で動きます。
このツールは見つけると Claude を呼ばずに止めます）。

### 1. 取得と設定

```powershell
cd $HOME\Documents
git clone https://github.com/shotamiyazakihp-cell/ai-pmo-assistant.git
cd ai-pmo-assistant
py -3 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass   # このウィンドウの間だけ
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -e .
python -m pytest -q        # すべて passed になること
python -m pmo check        # ANTHROPIC_API_KEY が false であること
```

### 2. 試験用の議事録で試す

```powershell
python -m pmo ingest-samples                     # 10件を取り込む
python -m pmo analyze --source sample-alpha-01   # 本物の Claude Code で1件だけ分析
streamlit run app/main.py                        # 画面（ブラウザが開く）
```

画面の「抽出の確認」で候補を見て、原文と合うものを採用・合わないものを却下します。
全件を分析したら `python -m pmo evaluate` で、正解データとの一致率と、作り話の担当・期限の件数を出せます。

Claude Code が動かない時は、画面の「手で貼る」を開き、指示文を Claude に貼って、返ってきた JSON を貼り戻します。

### 3. Notion につなぐ（オーナー本人の操作）

1. https://www.notion.so/profile/integrations で「内部インテグレーション」を作る。権限は **「コンテンツを読み取る」だけ**。
2. 議事録のデータベース（例：HiDock記録）の「…」→「接続」でそのインテグレーションを追加する。
3. `.env.example` を `.env` にコピーし、`NOTION_TOKEN` と `NOTION_DATABASE_ID` を入れる（`.env` は Git に入らない）。
4. 画面の「Notion から取り込む」か `python -m pmo ingest-notion`。

実際の勤務先の議事録は、勤務先の規程で許されるまで使いません（試験用のページだけで確かめる）。

## 毎日の使い方（Phase 1）

1. PowerShell で `cd $HOME\Documents\ai-pmo-assistant` → `.\.venv\Scripts\Activate.ps1` → `streamlit run app/main.py`
2. 左の「Notion から取り込む」（または試験用の議事録を取り込む）
3. 「議事録」で**会議日の古い順に**1件ずつ「Claude Code で分析」→「抽出の確認」で採用・却下
   （古い順に採用していくと、次の会議の分析で Claude が既存の項目を見て、状態や期限の変化を同じ項目に結び付けます）
4. 「確定した項目」で、項目ごとの変化の履歴を見る。「処理の履歴」で失敗が無いか見る

## データの置き場所

- 取り込んだ原文・抽出・確定した項目・履歴：PC の `data\pmo.db`（SQLite。Git には入らない）
- 鍵：PC の `.env`（Git には入らない）
- 触らないもの：Notion の原文（読むだけ）、KB アプリと Supabase の kb の区画（読まない・書かない）

## 構成

| 場所 | 中身 |
|---|---|
| `src/pmo/notion_reader.py` | Notion の読み取り専用クライアント（ページネーション、入れ子のブロック、429 の再試行、書き込みの禁止） |
| `src/pmo/ingest.py` | 取り込み。ページ ID＋本文の指紋で重複を防ぎ、本文が変わったら新しい版。ゴミ箱・消えたページに印 |
| `src/pmo/extract.py` | 指示文づくり、AI の返事の点検（引用が原文に無ければ不正、担当・期限の根拠が無ければ「不明」）、保存 |
| `src/pmo/compare.py` | 既存の項目との照合（同じプロジェクト・同じ種類の中だけ） |
| `src/pmo/review.py` | 採用・却下。採用したものだけが確定データに入り、状態・期限・担当の変化を履歴に残す |
| `src/pmo/claude_runner.py` | `claude -p` の呼び出し。ANTHROPIC_API_KEY があれば止める。道具は使わせない |
| `src/pmo/evaluate.py` | 完了の条件を数える |
| `app/main.py` | 画面3つ（議事録の一覧と詳細／抽出の確認／処理の履歴） |
| `samples/` | 試験用の議事録10件（架空の3プロジェクト・4週間分）と正解データ |
