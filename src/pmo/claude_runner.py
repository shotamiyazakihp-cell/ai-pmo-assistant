"""Claude の呼び方。

- ClaudeCliRunner：PC の Claude Code を `claude -p` で1回だけ呼ぶ（Claude Pro のログインで動く）。
  ANTHROPIC_API_KEY が環境にあると API 課金で動くため、その場合は呼ばずに止める。
- 手で貼る予備：画面で指示文を表示し、Claude の返事を貼り戻す（runner を使わない）。
- テストでは偽の runner を使う（tests/ を参照）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Protocol

from .config import api_key_present


class RunnerError(Exception):
    pass


class ApiKeyDetected(RunnerError):
    pass


class Runner(Protocol):
    name: str

    def run(self, prompt: str) -> str: ...


# 抽き出しには道具（ファイル操作・コマンド・ネット）を使わせない
DISALLOWED_TOOLS = "Bash Edit Write MultiEdit NotebookEdit WebFetch WebSearch Read Glob Grep Task"


class ClaudeCliRunner:
    name = "claude_cli"

    def __init__(self, command: list[str] | None = None, timeout: int = 600, extra_args: list[str] | None = None):
        self.command = command  # 例：テストでは [python, 偽のスクリプト]。None なら PATH の claude
        self.timeout = timeout
        self.extra_args = extra_args if extra_args is not None else ["--disallowedTools", DISALLOWED_TOOLS]

    def run(self, prompt: str) -> str:
        if api_key_present():
            raise ApiKeyDetected(
                "ANTHROPIC_API_KEY が設定されています。このまま Claude Code を呼ぶと API 従量課金になるため、"
                "呼び出しを止めました。環境変数から削除してからやり直してください。"
            )
        if self.command:
            cmd = list(self.command)
        else:
            exe = shutil.which("claude")
            if not exe:
                raise RunnerError("claude コマンドが見つかりません。Claude Code を入れるか、手で貼る方法を使ってください。")
            cmd = [exe]
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        # 空のフォルダで動かし、プロジェクトのファイルを読ませない
        with tempfile.TemporaryDirectory() as workdir:
            try:
                proc = subprocess.run(
                    [*cmd, "-p", "--output-format", "json", *self.extra_args],
                    input=prompt, capture_output=True, text=True, encoding="utf-8",
                    timeout=self.timeout, cwd=workdir, env=env,
                )
            except FileNotFoundError as e:
                raise RunnerError(f"Claude Code を起動できません: {e}") from e
            except subprocess.TimeoutExpired as e:
                raise RunnerError(f"Claude Code が {self.timeout} 秒で終わりませんでした") from e
        if proc.returncode != 0:
            raise RunnerError(f"Claude Code が失敗しました（終了コード {proc.returncode}）: {proc.stderr.strip()[:500]}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout  # JSON でなければ本文をそのまま返す（後の点検で判定）
        if isinstance(data, dict):
            if data.get("is_error"):
                raise RunnerError(f"Claude Code がエラーを返しました: {str(data.get('result'))[:500]}")
            if "result" in data:
                return str(data["result"])
        return proc.stdout
