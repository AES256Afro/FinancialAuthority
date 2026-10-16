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
    # Where the app keeps its own restore points. Beside the data by default; BoxPilot mounts its own folder,
    # so its nightly archive of the data does not carry every copy inside it.
    restore_dir: Path | None = None
    # A folder off the data disk for sealed copies (a USB drive, a synced folder); BoxPilot mounts one at /offbox.
    offbox_dir: str = ""
    # How the app was installed (boxpilot, compose, docker), and the name of the Proton Mail Bridge's container
    # on the server, so the Bridge's sign-in steps show the commands for this install.
    install: str = ""
    # Proxies whose forwarding headers are believed for the caller's address (comma-separated addresses or
    # networks, for example the tunnel container's). Empty: the direct peer is the caller.
    trusted_proxies: str = ""
    bridge_container: str = "financialauthority-bridge"

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

    def signing_secret(self) -> str:
        """FA_SESSION_SECRET, or, when it is not set, a random one made once and kept in the data folder (only
        this app's user can read it). Never a value anyone else could know."""
        if self.session_secret:
            return self.session_secret
        path = self.data_dir / ".session_secret"
        try:
            if path.exists():
                kept = path.read_text(encoding="utf-8").strip()
                if len(kept) >= 32:
                    return kept
            import secrets as _secrets

            made = _secrets.token_urlsafe(48)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(made, encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return made
        except OSError:
            import secrets as _secrets

            # The data folder cannot be written: a secret for this run only (sessions end at a restart).
            cached = getattr(self, "_run_secret", None) or _secrets.token_urlsafe(48)
            object.__setattr__(self, "_run_secret", cached)
            return cached

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.import_root.mkdir(parents=True, exist_ok=True)
        (self.import_root / "paystubs").mkdir(parents=True, exist_ok=True)


settings = Settings()
