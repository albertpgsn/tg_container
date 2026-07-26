from getpass import getpass
from pathlib import Path
import secrets
from urllib.parse import quote

from argon2 import PasswordHasher
from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parent.parent
SECRETS_DIR = ROOT / "secrets"


def write_secret(name: str, value: str) -> None:
    path = SECRETS_DIR / name
    if path.exists():
        raise SystemExit(f"Refusing to overwrite existing secret: {path}")
    path.write_text(value, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def main() -> None:
    SECRETS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    required = {
        "postgres_password",
        "database_url",
        "telegram_api_hash",
        "session_encryption_key",
        "admin_password_hash",
    }
    existing = sorted(name for name in required if (SECRETS_DIR / name).exists())
    if existing:
        raise SystemExit(f"Refusing to overwrite existing secrets: {', '.join(existing)}")
    api_hash = getpass("Telegram API hash: ").strip()
    admin_password = getpass("New admin password (16+ characters): ")
    repeated = getpass("Repeat admin password: ")
    if len(admin_password) < 16 or admin_password != repeated:
        raise SystemExit("Passwords do not match or are shorter than 16 characters")
    if len(api_hash) < 20:
        raise SystemExit("Telegram API hash looks invalid")

    db_password = secrets.token_urlsafe(36)
    write_secret("postgres_password", db_password)
    write_secret(
        "database_url",
        f"postgresql+psycopg://telegram_crm:{quote(db_password, safe='')}@db:5432/telegram_crm",
    )
    write_secret("telegram_api_hash", api_hash)
    write_secret("session_encryption_key", Fernet.generate_key().decode())
    write_secret("admin_password_hash", PasswordHasher().hash(admin_password))
    print("Secrets created. Keep the secrets directory off backups unless backups are encrypted.")


if __name__ == "__main__":
    main()
