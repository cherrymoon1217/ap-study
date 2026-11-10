from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
_data_dir = Path(os.getenv("AP_TUTOR_DATA_DIR", "data"))
if not _data_dir.is_absolute():
    _data_dir = ROOT / _data_dir


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
    host: str = os.getenv("AP_TUTOR_HOST", "127.0.0.1")
    port: int = int(os.getenv("AP_TUTOR_PORT", "8000"))
    api_token: str = os.getenv("AP_TUTOR_API_TOKEN", "")
    data_dir: Path = _data_dir
    max_upload_mb: int = int(os.getenv("AP_TUTOR_MAX_UPLOAD_MB", "12"))

    @property
    def database_path(self) -> Path:
        return self.data_dir / "ap_tutor.sqlite3"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"


settings = Settings()
