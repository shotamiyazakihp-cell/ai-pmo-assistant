import json
import sys

import pytest

from pmo.claude_runner import ApiKeyDetected, ClaudeCliRunner, RunnerError


def fake_cli(tmp_path, stdout: str, code: int = 0):
    """claude の代わりに動く小さな Python スクリプト（受け取った指示文の長さも書き出す）。"""
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "import sys, pathlib\n"
        "data = sys.stdin.read()\n"
        f"pathlib.Path(r'{tmp_path}').joinpath('argv.txt').write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.exit({code})\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_refuses_when_api_key_present(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dummy")
    cmd = fake_cli(tmp_path, "{}")
    with pytest.raises(ApiKeyDetected):
        ClaudeCliRunner(command=cmd).run("x")
    assert not (tmp_path / "argv.txt").exists()   # 呼んでいない


def test_reads_result_field(tmp_path):
    cmd = fake_cli(tmp_path, json.dumps({"type": "result", "is_error": False, "result": '{"items": []}'}))
    assert ClaudeCliRunner(command=cmd).run("指示") == '{"items": []}'
    argv = (tmp_path / "argv.txt").read_text(encoding="utf-8")
    assert argv.startswith("-p --output-format json --disallowedTools")


def test_errors_are_raised(tmp_path):
    with pytest.raises(RunnerError):
        ClaudeCliRunner(command=fake_cli(tmp_path, json.dumps({"is_error": True, "result": "limit"}))).run("x")
    with pytest.raises(RunnerError):
        ClaudeCliRunner(command=fake_cli(tmp_path, "", code=1)).run("x")
    with pytest.raises(RunnerError):
        ClaudeCliRunner(command=[str(tmp_path / "no_such_cmd")]).run("x")


def test_missing_claude(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RunnerError):
        ClaudeCliRunner().run("x")


def test_dotenv_never_loads_api_key(tmp_path, monkeypatch):
    from pmo.config import load_dotenv
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-should-not-load\nPMO_TEST_X=1\n", encoding="utf-8")
    monkeypatch.delenv("PMO_TEST_X", raising=False)
    load_dotenv(env)
    import os
    assert "ANTHROPIC_API_KEY" not in os.environ and os.environ["PMO_TEST_X"] == "1"
