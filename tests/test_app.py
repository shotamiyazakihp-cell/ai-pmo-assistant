"""画面が例外なく描けるかを Streamlit の試験機能で確かめる。"""
import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")


def test_app_pages_render(tmp_path, monkeypatch):
    monkeypatch.setenv("PMO_DB_PATH", str(tmp_path / "app.db"))
    from pmo.config import ROOT
    at = st_testing.AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=30)
    at.run()
    assert not at.exception
    at.sidebar.button[0].click().run()          # 試験用の議事録を取り込む
    assert not at.exception
    for name in ["ダッシュボード", "プロジェクト一覧", "議事録", "抽出の確認（承認）", "課題・リスク管理",
                 "TODO 管理", "AI 分析結果", "資料作成", "ナレッジ検索", "処理の履歴・設定"]:
        at.sidebar.radio[0].set_value(name).run()
        assert not at.exception, name
