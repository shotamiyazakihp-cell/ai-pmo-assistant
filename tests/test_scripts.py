"""PC 用のバッチの形を確かめる（Windows での実行そのものは PC で確かめる）。"""
import re

from pmo.config import ROOT

SCRIPTS = ROOT / "scripts"


def test_bat_and_ps1_format():
    for name in ("pc_setup_and_check.bat", "check_bitlocker.bat"):
        raw = (SCRIPTS / name).read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")      # CRLF だけ
        text = raw.decode("utf-8")
        assert text.startswith("@echo off\r\nchcp 65001")
        # ( ) のブロックの中の echo に半角のかっこを使わない（cmd が誤解するため）
        for line in text.splitlines():
            if line.strip().startswith("echo"):
                assert "(" not in line[5:] and ")" not in line[5:], line
    for name in ("pc_setup_and_check.ps1", "register_tasks.ps1"):
        raw = (SCRIPTS / name).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")                                 # Windows PowerShell 5.1 用に BOM 付き


def test_scripts_have_no_secrets_and_guard_api_key():
    ps = (SCRIPTS / "pc_setup_and_check.ps1").read_text(encoding="utf-8-sig")
    assert "ANTHROPIC_API_KEY" in ps and "Stop-All" in ps
    assert "Start-Transcript" in ps
    allfiles = "".join(p.read_text(encoding="utf-8-sig") for p in SCRIPTS.iterdir())
    assert not re.search(r"(secret_|ntn_|sk-ant-)[A-Za-z0-9]{8,}", allfiles)
    assert "pc_check.db" in ps                                                 # 試験は本番の DB を汚さない
