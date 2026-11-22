"""設定の読み込み。鍵はソースコードに書かず、環境変数か .env（Git に入らない）から読む。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNKNOWN = "不明"


def load_dotenv(path: Path | None = None) -> None:
    """簡易 .env 読み込み（既にある環境変数は上書きしない）。"""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key == "ANTHROPIC_API_KEY":
            # .env に置かれていても読み込まない（API 課金を防ぐ）
            continue
        os.environ.setdefault(key, value)


@dataclass
class Settings:
    db_path: Path
    notion_token: str | None
    notion_database_id: str | None
    notion_project_property: str
    notion_date_property: str


def get_settings() -> Settings:
    load_dotenv()
    db = os.environ.get("PMO_DB_PATH", "data/pmo.db")
    db_path = Path(db) if Path(db).is_absolute() else ROOT / db
    return Settings(
        db_path=db_path,
        notion_token=os.environ.get("NOTION_TOKEN") or None,
        notion_database_id=os.environ.get("NOTION_DATABASE_ID") or None,
        notion_project_property=os.environ.get("NOTION_PROJECT_PROPERTY", "関連プロジェクト"),
        notion_date_property=os.environ.get("NOTION_DATE_PROPERTY", "録音日時"),
    )


def api_key_present() -> bool:
    """PC に ANTHROPIC_API_KEY があるか（あると Claude Code が API 課金で動く）。"""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
