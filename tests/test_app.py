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
    for name in ["抽出の確認", "処理の履歴", "議事録"]:
        at.sidebar.radio[0].set_value(name).run()
        assert not at.exception, name
