"""Settings, all from FA_* environment variables (BoxPilot sets them at install; .env works for development)."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FA_", env_file=".env", extra="ignore")

    # Where everything that must survive lives. BoxPilot mounts /data; development uses backend/data.
    data_dir: Path = Path(os.environ.get("FA_DATA_DIR") or Path(__file__).resolve().parents[1] / "data")
    # The drop folder for exports and paystub PDFs. BoxPilot mounts /imports.
    import_dir: Path | None = None

    host: str = "0.0.0.0"
    port: int = 8000

    # Sign-in. Empty password = sign-in off, which is only acceptable on a developer's machine.
    username: str = "owner"
    password: str = ""
    session_secret: str = ""
    # Encrypts connector credentials and account numbers inside the database.
    data_key: str = ""

    # The https address the app is published at; the OAuth issuer for connected clients (M3).
    public_url: str = ""

    # Model providers. Ollama on the host by default; the rest optional.
    ollama_url: str = "http://localhost:11434"
    anthropic_api_key: str = ""
    openai_base_url: str = ""
    openai_api_key: str = ""

    # Demo mode loads the synthetic household so the app can be seen without any real data.
    demo: bool = False
    # Run the scheduler (off in tests).
    jobs: bool = True
    # Allow the Vite dev server during development.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def tz(self) -> str:
        return os.environ.get("TZ") or "UTC"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "financialauthority.db"

    @property
    def import_root(self) -> Path:
        return (self.import_dir or self.data_dir / "imports").resolve()

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.import_root.mkdir(parents=True, exist_ok=True)
        (self.import_root / "paystubs").mkdir(parents=True, exist_ok=True)


settings = Settings()
