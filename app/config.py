from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_api_id: int
    telegram_api_hash: str | None = None
    telegram_api_hash_file: str | None = None
    session_encryption_key: str | None = None
    session_encryption_key_file: str | None = None
    database_url: str = "sqlite:///./data/mvp.db"
    database_url_file: str | None = None
    admin_bootstrap_username: str = "admin"
    admin_bootstrap_password_hash: str | None = None
    admin_bootstrap_password_hash_file: str | None = None
    cookie_secure: bool = False
    admin_session_hours: int = 12

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @staticmethod
    def _secret(value: str | None, file_path: str | None, name: str) -> str:
        if file_path:
            secret = Path(file_path).read_text(encoding="utf-8").strip()
        else:
            secret = (value or "").strip()
        if not secret:
            raise ValueError(f"Missing required secret: {name}")
        return secret

    @property
    def resolved_telegram_api_hash(self) -> str:
        return self._secret(self.telegram_api_hash, self.telegram_api_hash_file, "telegram_api_hash")

    @property
    def resolved_session_encryption_key(self) -> str:
        return self._secret(
            self.session_encryption_key,
            self.session_encryption_key_file,
            "session_encryption_key",
        )

    @property
    def resolved_database_url(self) -> str:
        return self._secret(self.database_url, self.database_url_file, "database_url")

    @property
    def resolved_admin_password_hash(self) -> str:
        return self._secret(
            self.admin_bootstrap_password_hash,
            self.admin_bootstrap_password_hash_file,
            "admin_bootstrap_password_hash",
        )

    def cipher(self) -> Fernet:
        return Fernet(self.resolved_session_encryption_key.encode())


@lru_cache
def get_settings() -> Settings:
    return Settings()
