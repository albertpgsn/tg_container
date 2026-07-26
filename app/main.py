import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
from time import monotonic
from typing import Literal
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import inspect as sqlalchemy_inspect, text as sql_text
from sqlalchemy.orm import Session
from telethon import TelegramClient, events, functions, types, utils
from telethon.errors import PhoneCodeInvalidError, RPCError, SessionPasswordNeededError
from telethon.sessions import StringSession

from .config import get_settings
from .database import Base, SessionLocal, engine, get_db
from .models import AdminSession, AdminUser, AuditLog, LoginAttempt, TelegramAccount
from .security import CSRF_COOKIE, SESSION_COOKIE, audit, client_ip, hash_token, require_admin


password_hasher = PasswordHasher()
dummy_password_hash = password_hasher.hash("dummy-password-never-used")


@dataclass
class AccountRuntime:
    client: TelegramClient
    dialogs: list[dict] | None = None
    dialogs_dirty: bool = True
    dialogs_cached_at: float = 0.0
    histories: dict[str, tuple[float, list[dict]]] = field(default_factory=dict)
    peers: dict[str, object] = field(default_factory=dict)
    avatars: dict[str, tuple[float, bytes | None]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def invalidate(self) -> None:
        self.dialogs_dirty = True
        self.histories.clear()


account_runtimes: dict[str, AccountRuntime] = {}
runtime_guard = asyncio.Lock()
profile_sync_lock = asyncio.Lock()
PROFILE_REFRESH_AFTER = timedelta(days=1)


def ensure_account_profile_columns() -> None:
    columns = {column["name"] for column in sqlalchemy_inspect(engine).get_columns("telegram_accounts")}
    additions = {
        "telegram_peer_id": "VARCHAR(32)",
        "username": "VARCHAR(64)",
        "profile_checked_at": "TIMESTAMP",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(sql_text(f"ALTER TABLE telegram_accounts ADD COLUMN {name} {sql_type}"))


def profile_is_due(checked_at: datetime | None) -> bool:
    if checked_at is None:
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked_at >= PROFILE_REFRESH_AFTER


async def sync_due_account_profiles() -> None:
    async with profile_sync_lock:
        with SessionLocal() as db:
            account_ids = [
                account.id
                for account in db.query(TelegramAccount).filter_by(status="active").all()
                if profile_is_due(account.profile_checked_at)
            ]
        for account_id in account_ids:
            with SessionLocal() as db:
                account = db.get(TelegramAccount, account_id)
                if not account or account.status != "active" or not profile_is_due(account.profile_checked_at):
                    continue
                try:
                    runtime = await runtime_for(account)
                    me = await runtime.client.get_me()
                    account.telegram_user_id = str(me.id)
                    account.telegram_peer_id = str(utils.get_peer_id(me))
                    account.username = me.username
                    account.profile_checked_at = datetime.now(timezone.utc)
                    db.commit()
                except (RPCError, OSError, InvalidToken):
                    db.rollback()


async def profile_sync_loop() -> None:
    while True:
        await sync_due_account_profiles()
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_account_profile_columns()
    MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        settings = get_settings()
        username = settings.admin_bootstrap_username.strip().lower()
        existing = db.query(AdminUser).filter_by(username=username).first()
        if existing is None:
            db.add(AdminUser(username=username, password_hash=settings.resolved_admin_password_hash))
            db.commit()
    profile_task = asyncio.create_task(profile_sync_loop())
    yield
    profile_task.cancel()
    try:
        await profile_task
    except asyncio.CancelledError:
        pass
    for runtime in list(account_runtimes.values()):
        await runtime.client.disconnect()
    account_runtimes.clear()


app = FastAPI(
    title="Telegram CRM",
    version="0.2.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
PROJECT_DIR = Path(__file__).resolve().parent.parent
MEDIA_CACHE_DIR = Path(os.getenv("MEDIA_CACHE_DIR", str(PROJECT_DIR / "data" / "media-cache"))).resolve()
app.mount("/static", StaticFiles(directory=PROJECT_DIR / "static"), name="static")


def encrypt(value: str) -> str:
    return get_settings().cipher().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return get_settings().cipher().decrypt(value.encode()).decode()


def client_for(session_string: str) -> TelegramClient:
    settings = get_settings()
    return TelegramClient(
        StringSession(session_string),
        settings.telegram_api_id,
        settings.resolved_telegram_api_hash,
    )


async def runtime_for(account: TelegramAccount) -> AccountRuntime:
    async with runtime_guard:
        runtime = account_runtimes.get(account.id)
        if runtime is None:
            client = client_for(decrypt(account.session_ciphertext))
            await client.connect()
            runtime = AccountRuntime(client=client)
            account_runtimes[account.id] = runtime

            async def invalidate_cache(_event) -> None:
                runtime.invalidate()

            client.add_event_handler(invalidate_cache, events.NewMessage)
            client.add_event_handler(invalidate_cache, events.MessageEdited)
            client.add_event_handler(invalidate_cache, events.MessageDeleted)
        elif not runtime.client.is_connected():
            await runtime.client.connect()
        return runtime


class StartLoginRequest(BaseModel):
    phone: str = Field(description="International number, e.g. +79990000000")
    label: str | None = Field(default=None, max_length=120)


class CompleteLoginRequest(BaseModel):
    code: str = Field(min_length=3, max_length=16)


class CompletePasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ChatHistoryRequest(BaseModel):
    dialog_ref: str = Field(min_length=20, max_length=1024)
    limit: int = Field(default=100, ge=1, le=100)
    force: bool = False


class SendMessageRequest(BaseModel):
    dialog_ref: str = Field(min_length=20, max_length=1024)
    text: str = Field(min_length=1, max_length=4096)


class DialogRefRequest(BaseModel):
    dialog_ref: str = Field(min_length=20, max_length=1024)


class BotButtonRequest(DialogRefRequest):
    message_id: int = Field(ge=1)
    row: int = Field(ge=0, le=20)
    column: int = Field(ge=0, le=20)


class MarkReadRequest(DialogRefRequest):
    max_id: int = Field(ge=1)


class CreateDialogRequest(BaseModel):
    kind: Literal["private", "group", "channel"]
    target: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=128)
    about: str | None = Field(default=None, max_length=255)
    participants: list[str] = Field(default_factory=list, max_length=50)
    first_message: str | None = Field(default=None, max_length=4096)


class RevokeOtherSessionsRequest(BaseModel):
    confirmation: Literal["REVOKE"]


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=256)


def active_account(account_id: str, db: Session) -> TelegramAccount:
    account = db.get(TelegramAccount, account_id)
    if not account or account.status != "active":
        raise HTTPException(status_code=404, detail="Активный аккаунт не найден")
    return account


def encode_dialog_ref(entity) -> str:
    if isinstance(entity, types.User):
        if entity.is_self:
            payload = {"type": "self"}
        elif entity.access_hash is not None:
            payload = {"type": "user", "id": entity.id, "access_hash": entity.access_hash}
        else:
            raise ValueError("User access hash is unavailable")
    elif isinstance(entity, types.Channel):
        payload = {"type": "channel", "id": entity.id, "access_hash": entity.access_hash}
    elif isinstance(entity, types.Chat):
        payload = {"type": "chat", "id": entity.id}
    else:
        raise ValueError("Unsupported Telegram entity")
    return encrypt(json.dumps(payload, separators=(",", ":")))


def dialog_key(entity) -> str:
    entity_type = "user" if isinstance(entity, types.User) else "channel" if isinstance(entity, types.Channel) else "chat"
    entity_id = getattr(entity, "id", 0)
    secret = get_settings().resolved_session_encryption_key.encode()
    return hmac.new(secret, f"{entity_type}:{entity_id}".encode(), hashlib.sha256).hexdigest()[:24]


def decode_dialog_ref(value: str):
    try:
        payload = json.loads(decrypt(value))
        if payload["type"] == "self":
            return types.InputPeerSelf()
        if payload["type"] == "user":
            return types.InputPeerUser(payload["id"], payload["access_hash"])
        if payload["type"] == "channel":
            return types.InputPeerChannel(payload["id"], payload["access_hash"])
        if payload["type"] == "chat":
            return types.InputPeerChat(payload["id"])
    except (InvalidToken, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="Некорректная ссылка на диалог") from error
    raise HTTPException(status_code=400, detail="Неподдерживаемый тип диалога")


def serialize_bot_buttons(message) -> list[list[dict]]:
    result = []
    for row_index, row in enumerate(getattr(message, "buttons", None) or []):
        serialized_row = []
        for column_index, button in enumerate(row):
            item = {
                "text": str(getattr(button, "text", "Кнопка")),
                "row": row_index,
                "column": column_index,
                "type": "unsupported",
            }
            url = getattr(button, "url", None)
            if url:
                parsed = urlparse(url)
                if parsed.scheme in {"http", "https", "tg"}:
                    item.update({"type": "url", "url": url})
            elif getattr(button, "data", None) is not None:
                item["type"] = "callback"
            elif type(getattr(button, "button", None)) is types.KeyboardButton:
                item["type"] = "text"
            serialized_row.append(item)
        if serialized_row:
            result.append(serialized_row)
    return result


MAX_MEDIA_BYTES = 100 * 1024 * 1024
MAX_MEDIA_CACHE_BYTES = int(os.getenv("MEDIA_CACHE_MAX_BYTES", str(200 * 1024 * 1024)))


def serialize_media(message) -> dict | None:
    if not message.media:
        return None
    file = getattr(message, "file", None)
    size = getattr(file, "size", None)
    mime_type = getattr(file, "mime_type", None) or "application/octet-stream"
    file_name = getattr(file, "name", None)
    if getattr(message, "photo", None):
        kind = "photo"
        file_name = file_name or f"photo-{message.id}.jpg"
        mime_type = "image/jpeg"
    elif getattr(message, "gif", None):
        kind = "gif"
        file_name = file_name or f"animation-{message.id}.mp4"
    elif getattr(message, "video_note", None):
        kind = "video"
        file_name = file_name or f"video-note-{message.id}.mp4"
    elif getattr(message, "video", None):
        kind = "video"
        file_name = file_name or f"video-{message.id}.mp4"
    elif getattr(message, "voice", None):
        kind = "voice"
        file_name = file_name or f"voice-{message.id}.ogg"
    elif getattr(message, "audio", None):
        kind = "audio"
        file_name = file_name or f"audio-{message.id}"
    elif getattr(message, "sticker", None):
        kind = "sticker"
        file_name = file_name or f"sticker-{message.id}{getattr(file, 'ext', '') or ''}"
    elif getattr(message, "document", None):
        kind = "document"
        file_name = file_name or f"document-{message.id}{getattr(file, 'ext', '') or ''}"
    else:
        return None
    return {
        "kind": kind,
        "name": Path(file_name).name[:180],
        "mime_type": mime_type,
        "size": size,
        "duration": getattr(file, "duration", None),
        "width": getattr(file, "width", None),
        "height": getattr(file, "height", None),
        "available": size is None or size <= MAX_MEDIA_BYTES,
        "max_bytes": MAX_MEDIA_BYTES,
    }


def serialize_message(message) -> dict:
    sender_name = "Вы" if message.out else ""
    if message.sender:
        sender_name = utils.get_display_name(message.sender) or sender_name
    media = serialize_media(message)
    text = message.message or ("" if media else "Служебное сообщение")
    return {
        "id": message.id,
        "text": text,
        "date": message.date.isoformat() if message.date else None,
        "outgoing": bool(message.out),
        "sender_name": sender_name or "Участник",
        "has_media": bool(message.media),
        "media": media,
        "buttons": serialize_bot_buttons(message),
    }


def user_status_label(entity: types.User) -> str:
    if entity.bot:
        return "бот"
    user_status = entity.status
    if isinstance(user_status, types.UserStatusOnline):
        return "в сети"
    if isinstance(user_status, types.UserStatusRecently):
        return "был(а) недавно"
    if isinstance(user_status, types.UserStatusLastWeek):
        return "был(а) на этой неделе"
    if isinstance(user_status, types.UserStatusLastMonth):
        return "был(а) в этом месяце"
    if isinstance(user_status, types.UserStatusOffline) and user_status.was_online:
        return f"был(а) {user_status.was_online.isoformat()}"
    return ""


def initials_for(title: str) -> str:
    words = [word for word in title.strip().split() if word]
    return "".join(word[0].upper() for word in words[:2]) or "TG"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(PROJECT_DIR / "static" / "index.html")


@app.post("/auth/login")
def admin_login(
    payload: AdminLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    username = payload.username.strip().lower()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    failures = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "login_failed",
            AuditLog.created_at >= cutoff,
            (AuditLog.actor == username) | (AuditLog.ip_address == client_ip(request)),
        )
        .count()
    )
    if failures >= 8:
        raise HTTPException(status_code=429, detail="Слишком много попыток. Повторите через 15 минут")

    admin = db.query(AdminUser).filter_by(username=username, enabled=True).first()
    password_hash = admin.password_hash if admin else dummy_password_hash
    try:
        password_ok = password_hasher.verify(password_hash, payload.password)
    except (VerifyMismatchError, InvalidHashError):
        password_ok = False
    if not admin or not password_ok:
        audit(db, actor=username or "unknown", action="login_failed", request=request)
        db.commit()
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    if password_hasher.check_needs_rehash(admin.password_hash):
        admin.password_hash = password_hasher.hash(payload.password)

    now = datetime.now(timezone.utc)
    db.query(AdminSession).filter(AdminSession.expires_at <= now).delete(synchronize_session=False)
    session_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    ttl_seconds = get_settings().admin_session_hours * 3600
    db.add(
        AdminSession(
            admin_id=admin.id,
            token_hash=hash_token(session_token),
            csrf_hash=hash_token(csrf_token),
            expires_at=now + timedelta(seconds=ttl_seconds),
            last_seen_at=now,
        )
    )
    admin.last_login_at = now
    audit(db, actor=admin.username, action="login_success", request=request, admin_id=admin.id)
    db.commit()

    secure = get_settings().cookie_secure
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=ttl_seconds,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )
    return {"username": admin.username}


@app.get("/auth/me")
def admin_me(admin: AdminUser = Depends(require_admin)):
    return {"username": admin.username}


@app.post("/auth/logout")
def admin_logout(
    request: Request,
    response: Response,
    admin: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    session = request.state.admin_session
    audit(db, actor=admin.username, action="logout", request=request, admin_id=admin.id)
    db.delete(session)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"status": "ok"}


@app.post("/accounts/login/start", dependencies=[Depends(require_admin)])
async def start_login(payload: StartLoginRequest, db: Session = Depends(get_db)):
    existing = db.query(TelegramAccount).filter_by(phone=payload.phone).first()
    if existing and existing.status == "active":
        raise HTTPException(status_code=409, detail="This phone is already connected")

    client = client_for("")
    try:
        await client.connect()
        sent_code = await client.send_code_request(payload.phone)
        session_ciphertext = encrypt(StringSession.save(client.session))
    except RPCError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Telegram отклонил запрос: {error.__class__.__name__}",
        ) from error
    except OSError as error:
        raise HTTPException(
            status_code=502,
            detail="Не удалось подключиться к серверам Telegram. Проверьте интернет и повторите попытку.",
        ) from error
    finally:
        await client.disconnect()

    account = existing or TelegramAccount(phone=payload.phone, session_ciphertext=session_ciphertext)
    account.label = payload.label
    account.session_ciphertext = session_ciphertext
    account.status = "pending"
    db.add(account)
    db.flush()

    attempt = db.query(LoginAttempt).filter_by(account_id=account.id).first()
    if attempt is None:
        attempt = LoginAttempt(account_id=account.id, phone_code_hash=sent_code.phone_code_hash)
        db.add(attempt)
    else:
        attempt.phone_code_hash = sent_code.phone_code_hash
    db.commit()
    return {"account_id": account.id, "next_step": "submit the Telegram code to /accounts/{account_id}/login/complete"}


@app.post("/accounts/{account_id}/login/complete", dependencies=[Depends(require_admin)])
async def complete_login(account_id: str, payload: CompleteLoginRequest, db: Session = Depends(get_db)):
    account = db.get(TelegramAccount, account_id)
    attempt = db.query(LoginAttempt).filter_by(account_id=account_id).first()
    if not account or not attempt:
        raise HTTPException(status_code=404, detail="Pending login was not found")

    client = client_for(decrypt(account.session_ciphertext))
    await client.connect()
    try:
        try:
            user = await client.sign_in(account.phone, payload.code, phone_code_hash=attempt.phone_code_hash)
        except PhoneCodeInvalidError:
            raise HTTPException(status_code=400, detail="Invalid or expired Telegram code")
        except SessionPasswordNeededError:
            return {"account_id": account.id, "next_step": "password_required"}
        account.session_ciphertext = encrypt(StringSession.save(client.session))
        account.status = "active"
        account.telegram_user_id = str(user.id)
        account.telegram_peer_id = str(utils.get_peer_id(user))
        account.username = user.username
        account.profile_checked_at = datetime.now(timezone.utc)
        db.delete(attempt)
        db.commit()
        return {"account_id": account.id, "status": "active", "telegram_user_id": account.telegram_user_id}
    finally:
        await client.disconnect()


@app.post("/accounts/{account_id}/login/password", dependencies=[Depends(require_admin)])
async def complete_password(account_id: str, payload: CompletePasswordRequest, db: Session = Depends(get_db)):
    account = db.get(TelegramAccount, account_id)
    if not account or account.status != "pending":
        raise HTTPException(status_code=404, detail="Pending account was not found")

    client = client_for(decrypt(account.session_ciphertext))
    await client.connect()
    try:
        user = await client.sign_in(password=payload.password)
        account.session_ciphertext = encrypt(StringSession.save(client.session))
        account.status = "active"
        account.telegram_user_id = str(user.id)
        account.telegram_peer_id = str(utils.get_peer_id(user))
        account.username = user.username
        account.profile_checked_at = datetime.now(timezone.utc)
        attempt = db.query(LoginAttempt).filter_by(account_id=account.id).first()
        if attempt:
            db.delete(attempt)
        db.commit()
        return {"account_id": account.id, "status": "active", "telegram_user_id": account.telegram_user_id}
    finally:
        await client.disconnect()


@app.get("/accounts", dependencies=[Depends(require_admin)])
async def list_accounts(db: Session = Depends(get_db)):
    await sync_due_account_profiles()
    db.expire_all()
    accounts = db.query(TelegramAccount).order_by(TelegramAccount.created_at.desc()).all()
    return [
        {
            "id": a.id,
            "phone": a.phone,
            "label": a.label,
            "status": a.status,
            "telegram_user_id": a.telegram_user_id,
            "telegram_peer_id": a.telegram_peer_id,
            "username": a.username,
            "profile_checked_at": a.profile_checked_at.isoformat() if a.profile_checked_at else None,
            "avatar_url": f"/accounts/{a.id}/avatar" if a.status == "active" else None,
        }
        for a in accounts
    ]


@app.get("/accounts/{account_id}/me", dependencies=[Depends(require_admin)])
async def account_me(account_id: str, db: Session = Depends(get_db)):
    account = active_account(account_id, db)
    client = client_for(decrypt(account.session_ciphertext))
    await client.connect()
    try:
        me = await client.get_me()
        return {"id": me.id, "username": me.username, "first_name": me.first_name, "phone": me.phone}
    finally:
        await client.disconnect()


async def avatar_response(runtime: AccountRuntime, cache_key: str, entity) -> Response:
    cached = runtime.avatars.get(cache_key)
    if cached and monotonic() - cached[0] < 21600:
        image = cached[1]
    else:
        image = await runtime.client.download_profile_photo(entity, file=bytes, download_big=False)
        runtime.avatars[cache_key] = (monotonic(), image)
    if not image:
        raise HTTPException(status_code=404, detail="У профиля нет фотографии")
    return Response(content=image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=21600"})


@app.get("/accounts/{account_id}/avatar", dependencies=[Depends(require_admin)])
async def account_avatar(account_id: str, db: Session = Depends(get_db)):
    account = active_account(account_id, db)
    runtime = await runtime_for(account)
    try:
        async with runtime.lock:
            me = await runtime.client.get_me()
            return await avatar_response(runtime, "self", me)
    except RPCError as error:
        raise HTTPException(status_code=404, detail="Аватар аккаунта недоступен") from error


@app.get("/accounts/{account_id}/avatars/{peer_key}", dependencies=[Depends(require_admin)])
async def dialog_avatar(account_id: str, peer_key: str, db: Session = Depends(get_db)):
    account = active_account(account_id, db)
    runtime = await runtime_for(account)
    entity = runtime.peers.get(peer_key)
    if entity is None:
        raise HTTPException(status_code=404, detail="Диалог не найден в локальном кеше")
    try:
        async with runtime.lock:
            return await avatar_response(runtime, peer_key, entity)
    except RPCError as error:
        raise HTTPException(status_code=404, detail="Аватар диалога недоступен") from error


def safe_media_suffix(media: dict) -> str:
    suffix = Path(media["name"]).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        return suffix
    guessed = mimetypes.guess_extension(media["mime_type"], strict=False) or ".bin"
    return guessed if re.fullmatch(r"\.[a-z0-9]{1,10}", guessed) else ".bin"


def trim_media_cache(required_bytes: int) -> None:
    files = [path for path in MEDIA_CACHE_DIR.rglob("*") if path.is_file() and ".part-" not in path.name]
    total = sum(path.stat().st_size for path in files)
    for path in sorted(files, key=lambda item: item.stat().st_mtime):
        if total + required_bytes <= MAX_MEDIA_CACHE_BYTES:
            break
        try:
            size = path.stat().st_size
            path.unlink()
            total -= size
        except OSError:
            continue


@app.get("/accounts/{account_id}/media/{peer_key}/{message_id}", dependencies=[Depends(require_admin)])
async def message_media(
    account_id: str,
    peer_key: str,
    message_id: int,
    download: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    account = active_account(account_id, db)
    runtime = await runtime_for(account)
    entity = runtime.peers.get(peer_key)
    if entity is None:
        raise HTTPException(status_code=404, detail="Диалог не найден в локальном кеше")
    try:
        async with runtime.lock:
            message = await runtime.client.get_messages(entity, ids=message_id)
            media = serialize_media(message) if message else None
            if not media:
                raise HTTPException(status_code=404, detail="Медиафайл не найден")
            if not media["available"]:
                raise HTTPException(status_code=413, detail="Файл больше допустимого лимита 100 МБ")

            cache_dir = (MEDIA_CACHE_DIR / account.id / peer_key).resolve()
            if MEDIA_CACHE_DIR not in cache_dir.parents:
                raise HTTPException(status_code=400, detail="Некорректный путь медиакеша")
            cache_dir.mkdir(parents=True, exist_ok=True)
            target = cache_dir / f"{message_id}{safe_media_suffix(media)}"
            if not target.exists():
                trim_media_cache(int(media["size"] or 0))
                temporary = cache_dir / f"{target.name}.part-{secrets.token_hex(6)}"
                downloaded = await runtime.client.download_media(message, file=str(temporary))
                if not downloaded:
                    raise HTTPException(status_code=404, detail="Telegram не вернул содержимое файла")
                downloaded_path = Path(downloaded).resolve()
                if downloaded_path.parent != cache_dir:
                    downloaded_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=400, detail="Некорректный путь загруженного файла")
                if downloaded_path.stat().st_size > MAX_MEDIA_BYTES:
                    downloaded_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Файл больше допустимого лимита 100 МБ")
                downloaded_path.replace(target)

            headers = {"Cache-Control": "private, max-age=3600"}
            return FileResponse(
                target,
                media_type=media["mime_type"],
                filename=media["name"] if download else None,
                headers=headers,
            )
    except HTTPException:
        raise
    except RPCError as error:
        raise HTTPException(status_code=400, detail=f"Telegram не отдал медиафайл: {error.__class__.__name__}") from error
    except OSError as error:
        raise HTTPException(status_code=507, detail="Не удалось сохранить медиафайл в локальный кеш") from error


def created_dialog_payload(account: TelegramAccount, runtime: AccountRuntime, entity, preview: str = "") -> dict:
    peer_key = dialog_key(entity)
    runtime.peers[peer_key] = entity
    title = utils.get_display_name(entity) or getattr(entity, "title", None) or "Без названия"
    kind = "user"
    if isinstance(entity, types.Channel):
        kind = "channel" if entity.broadcast else "group"
    elif isinstance(entity, types.Chat):
        kind = "group"
    return {
        "key": peer_key,
        "ref": encode_dialog_ref(entity),
        "avatar_url": f"/accounts/{account.id}/avatars/{peer_key}",
        "title": title,
        "kind": kind,
        "unread_count": 0,
        "preview": preview[:180],
        "last_message_at": datetime.now(timezone.utc).isoformat() if preview else None,
    }


@app.post("/accounts/{account_id}/dialogs/create", dependencies=[Depends(require_admin)])
async def create_dialog(
    account_id: str,
    payload: CreateDialogRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    account = active_account(account_id, db)
    runtime = await runtime_for(account)
    warnings = []
    try:
        async with runtime.lock:
            if payload.kind == "private":
                target = (payload.target or "").strip()
                if not target:
                    raise HTTPException(status_code=400, detail="Укажите @username или телефон пользователя")
                try:
                    entity = await runtime.client.get_entity(target)
                except (ValueError, RPCError) as error:
                    raise HTTPException(status_code=404, detail="Telegram-пользователь не найден") from error
                if not isinstance(entity, types.User):
                    raise HTTPException(status_code=400, detail="Для личного чата нужно указать пользователя")
                first_message = (payload.first_message or "").strip()
                if first_message:
                    await runtime.client.send_message(entity, first_message, link_preview=False)
                result = created_dialog_payload(account, runtime, entity, first_message)
            else:
                title = (payload.title or "").strip()
                if not title:
                    raise HTTPException(status_code=400, detail="Введите название")
                raw_participants = []
                seen = set()
                for value in payload.participants:
                    participant = value.strip()
                    marker = participant.lower()
                    if participant and marker not in seen:
                        raw_participants.append(participant)
                        seen.add(marker)
                if payload.kind == "group" and not raw_participants:
                    raise HTTPException(status_code=400, detail="Добавьте хотя бы одного участника группы")

                participants = []
                for participant in raw_participants:
                    try:
                        entity = await runtime.client.get_entity(participant)
                    except (ValueError, RPCError) as error:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Не найден участник: {participant}",
                        ) from error
                    if not isinstance(entity, types.User):
                        raise HTTPException(status_code=400, detail=f"Не является пользователем: {participant}")
                    participants.append(entity)

                created = await runtime.client(
                    functions.channels.CreateChannelRequest(
                        title=title,
                        about=(payload.about or "").strip(),
                        broadcast=payload.kind == "channel",
                        megagroup=payload.kind == "group",
                    )
                )
                if not created.chats:
                    raise HTTPException(status_code=502, detail="Telegram не вернул созданный чат")
                entity = created.chats[0]
                if participants:
                    try:
                        await runtime.client(functions.channels.InviteToChannelRequest(entity, participants))
                    except RPCError as invite_error:
                        warnings.append(
                            f"Чат создан, но Telegram не добавил часть участников: {invite_error.__class__.__name__}"
                        )
                result = created_dialog_payload(account, runtime, entity)
            runtime.invalidate()

        admin = request.state.admin
        audit(
            db,
            actor=admin.username,
            action=f"telegram_{payload.kind}_dialog_created",
            request=request,
            admin_id=admin.id,
            target=account.id,
        )
        db.commit()
        return {"dialog": result, "warnings": warnings}
    except HTTPException:
        raise
    except RPCError as error:
        raise HTTPException(status_code=400, detail=f"Telegram отклонил создание: {error.__class__.__name__}") from error


@app.get("/accounts/{account_id}/dialogs", dependencies=[Depends(require_admin)])
async def account_dialogs(
    account_id: str,
    limit: int = 50,
    force: bool = False,
    db: Session = Depends(get_db),
):
    account = active_account(account_id, db)
    safe_limit = min(max(limit, 1), 50)
    runtime = await runtime_for(account)
    try:
        async with runtime.lock:
            if force:
                runtime.invalidate()
            cache_fresh = monotonic() - runtime.dialogs_cached_at < 30
            if runtime.dialogs is not None and not runtime.dialogs_dirty and cache_fresh:
                return runtime.dialogs[:safe_limit]
            dialogs = []
            async for dialog in runtime.client.iter_dialogs(limit=50):
                try:
                    dialog_ref = encode_dialog_ref(dialog.entity)
                except ValueError:
                    continue
                peer_key = dialog_key(dialog.entity)
                runtime.peers[peer_key] = dialog.entity
                last_message = dialog.message
                preview = ""
                last_message_at = None
                if last_message:
                    preview = last_message.message or ("Вложение" if last_message.media else "Служебное сообщение")
                    last_message_at = last_message.date.isoformat() if last_message.date else None
                kind = "user" if dialog.is_user else "channel" if dialog.is_channel else "group"
                dialogs.append(
                    {
                        "key": peer_key,
                        "ref": dialog_ref,
                        "avatar_url": f"/accounts/{account.id}/avatars/{peer_key}",
                        "title": dialog.name or "Без названия",
                        "kind": kind,
                        "unread_count": dialog.unread_count,
                        "preview": preview[:180],
                        "last_message_at": last_message_at,
                    }
                )
            runtime.dialogs = dialogs
            runtime.dialogs_dirty = False
            runtime.dialogs_cached_at = monotonic()
            return dialogs[:safe_limit]
    except RPCError as error:
        raise HTTPException(status_code=400, detail=f"Telegram отклонил запрос: {error.__class__.__name__}") from error


@app.post("/accounts/{account_id}/chat-history", dependencies=[Depends(require_admin)])
async def chat_history(account_id: str, payload: ChatHistoryRequest, db: Session = Depends(get_db)):
    account = active_account(account_id, db)
    peer = decode_dialog_ref(payload.dialog_ref)
    runtime = await runtime_for(account)
    try:
        async with runtime.lock:
            cached = runtime.histories.get(payload.dialog_ref)
            if cached and not payload.force and monotonic() - cached[0] < 30:
                return cached[1][-payload.limit :]
            messages = []
            async for message in runtime.client.iter_messages(peer, limit=payload.limit):
                messages.append(serialize_message(message))
            messages.reverse()
            runtime.histories[payload.dialog_ref] = (monotonic(), messages)
            return messages
    except RPCError as error:
        raise HTTPException(status_code=400, detail=f"Telegram отклонил запрос: {error.__class__.__name__}") from error


@app.post("/accounts/{account_id}/chat-profile", dependencies=[Depends(require_admin)])
async def chat_profile(account_id: str, payload: DialogRefRequest, db: Session = Depends(get_db)):
    account = active_account(account_id, db)
    peer = decode_dialog_ref(payload.dialog_ref)
    runtime = await runtime_for(account)
    try:
        async with runtime.lock:
            entity = await runtime.client.get_entity(peer)
            peer_key = dialog_key(entity)
            runtime.peers[peer_key] = entity
            title = utils.get_display_name(entity) or getattr(entity, "title", None) or "Без названия"
            profile = {
                "title": title,
                "initials": initials_for(title),
                "avatar_url": f"/accounts/{account.id}/avatars/{peer_key}",
                "username": getattr(entity, "username", None),
                "phone": getattr(entity, "phone", None),
                "about": None,
                "participants_count": None,
                "kind": "user",
                "is_bot": False,
                "status": "",
            }
            if isinstance(entity, types.User):
                full = await runtime.client(functions.users.GetFullUserRequest(entity))
                profile.update(
                    {
                        "about": getattr(full.full_user, "about", None),
                        "is_bot": bool(entity.bot),
                        "status": user_status_label(entity),
                    }
                )
            elif isinstance(entity, types.Channel):
                full = await runtime.client(functions.channels.GetFullChannelRequest(entity))
                profile.update(
                    {
                        "about": getattr(full.full_chat, "about", None),
                        "participants_count": getattr(full.full_chat, "participants_count", None),
                        "kind": "channel" if entity.broadcast else "group",
                        "status": "канал" if entity.broadcast else "группа",
                    }
                )
            elif isinstance(entity, types.Chat):
                full = await runtime.client(functions.messages.GetFullChatRequest(entity.id))
                participants = getattr(getattr(full.full_chat, "participants", None), "participants", None)
                profile.update(
                    {
                        "about": getattr(full.full_chat, "about", None),
                        "participants_count": len(participants) if participants is not None else None,
                        "kind": "group",
                        "status": "группа",
                    }
                )
            return profile
    except RPCError as error:
        raise HTTPException(status_code=400, detail=f"Telegram отклонил запрос профиля: {error.__class__.__name__}") from error


@app.post("/accounts/{account_id}/mark-read", dependencies=[Depends(require_admin)])
async def mark_dialog_read(account_id: str, payload: MarkReadRequest, db: Session = Depends(get_db)):
    account = active_account(account_id, db)
    peer = decode_dialog_ref(payload.dialog_ref)
    runtime = await runtime_for(account)
    try:
        async with runtime.lock:
            await runtime.client.send_read_acknowledge(
                peer,
                max_id=payload.max_id,
                clear_mentions=True,
                clear_reactions=True,
            )
            if runtime.dialogs:
                for dialog in runtime.dialogs:
                    if dialog["ref"] == payload.dialog_ref:
                        dialog["unread_count"] = 0
                        break
            runtime.dialogs_dirty = True
        return {"status": "ok", "max_id": payload.max_id}
    except RPCError as error:
        raise HTTPException(status_code=400, detail=f"Telegram отклонил отметку о прочтении: {error.__class__.__name__}") from error


@app.get("/accounts/{account_id}/search", dependencies=[Depends(require_admin)])
async def global_message_search(
    account_id: str,
    q: str = Query(min_length=2, max_length=128),
    limit: int = Query(default=30, ge=1, le=50),
    db: Session = Depends(get_db),
):
    account = active_account(account_id, db)
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Введите минимум 2 символа")
    runtime = await runtime_for(account)
    results = []
    try:
        async with runtime.lock:
            async for message in runtime.client.iter_messages(None, search=query, limit=limit):
                entity = message.chat or await message.get_chat()
                if entity is None:
                    continue
                try:
                    ref = encode_dialog_ref(entity)
                except ValueError:
                    continue
                peer_key = dialog_key(entity)
                runtime.peers[peer_key] = entity
                sender_name = "Вы" if message.out else ""
                if message.sender:
                    sender_name = utils.get_display_name(message.sender) or sender_name
                title = utils.get_display_name(entity) or getattr(entity, "title", None) or "Без названия"
                results.append(
                    {
                        "message_id": message.id,
                        "dialog_ref": ref,
                        "dialog_key": peer_key,
                        "avatar_url": f"/accounts/{account.id}/avatars/{peer_key}",
                        "dialog_title": title,
                        "sender_name": sender_name or "Участник",
                        "text": message.message or ("Вложение" if message.media else "Служебное сообщение"),
                        "date": message.date.isoformat() if message.date else None,
                    }
                )
        return results
    except RPCError as error:
        raise HTTPException(status_code=400, detail=f"Telegram отклонил поиск: {error.__class__.__name__}") from error


@app.post("/accounts/{account_id}/bot-button", dependencies=[Depends(require_admin)])
async def click_bot_button(
    account_id: str,
    payload: BotButtonRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    account = active_account(account_id, db)
    peer = decode_dialog_ref(payload.dialog_ref)
    runtime = await runtime_for(account)
    try:
        async with runtime.lock:
            message = await runtime.client.get_messages(peer, ids=payload.message_id)
            rows = getattr(message, "buttons", None) or []
            if payload.row >= len(rows) or payload.column >= len(rows[payload.row]):
                raise HTTPException(status_code=404, detail="Кнопка больше не доступна")
            button = rows[payload.row][payload.column]
            is_callback = getattr(button, "data", None) is not None
            is_text_button = type(getattr(button, "button", None)) is types.KeyboardButton
            if not is_callback and not is_text_button:
                raise HTTPException(status_code=400, detail="Эта кнопка требует неподдерживаемое действие")
            answer = await message.click(payload.row, payload.column)
            runtime.invalidate()
        admin = request.state.admin
        audit(
            db,
            actor=admin.username,
            action="telegram_bot_button_clicked" if is_callback else "telegram_bot_text_button_sent",
            request=request,
            admin_id=admin.id,
            target=account.id,
        )
        db.commit()
        answer_url = getattr(answer, "url", None)
        if answer_url and urlparse(answer_url).scheme not in {"http", "https", "tg"}:
            answer_url = None
        return {
            "status": "ok",
            "message": getattr(answer, "message", None) if is_callback else "Сообщение-кнопка отправлено",
            "alert": bool(getattr(answer, "alert", False)),
            "url": answer_url,
        }
    except HTTPException:
        raise
    except RPCError as error:
        raise HTTPException(status_code=400, detail=f"Telegram отклонил нажатие кнопки: {error.__class__.__name__}") from error


@app.post("/accounts/{account_id}/send-message", dependencies=[Depends(require_admin)])
async def send_chat_message(
    account_id: str,
    payload: SendMessageRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")
    account = active_account(account_id, db)
    peer = decode_dialog_ref(payload.dialog_ref)
    runtime = await runtime_for(account)
    try:
        message = await runtime.client.send_message(peer, text, link_preview=False)
        runtime.invalidate()
        admin = request.state.admin
        audit(
            db,
            actor=admin.username,
            action="telegram_message_sent",
            request=request,
            admin_id=admin.id,
            target=account.id,
        )
        db.commit()
        return {
            "id": message.id,
            "text": message.message or text,
            "date": message.date.isoformat() if message.date else None,
            "outgoing": True,
            "sender_name": "Вы",
            "has_media": False,
            "buttons": [],
        }
    except RPCError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Telegram отклонил отправку: {error.__class__.__name__}",
        ) from error


@app.post("/accounts/{account_id}/sessions/revoke-others", dependencies=[Depends(require_admin)])
async def revoke_other_sessions(
    account_id: str,
    _payload: RevokeOtherSessionsRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    account = active_account(account_id, db)
    runtime = await runtime_for(account)
    try:
        authorizations = await runtime.client(functions.account.GetAuthorizationsRequest())
        revoked_count = sum(1 for authorization in authorizations.authorizations if not authorization.current)
        await runtime.client(functions.auth.ResetAuthorizationsRequest())
        admin = request.state.admin
        audit(
            db,
            actor=admin.username,
            action="telegram_other_sessions_revoked",
            request=request,
            admin_id=admin.id,
            target=account.id,
        )
        db.commit()
        return {"status": "ok", "revoked_count": revoked_count}
    except RPCError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Telegram отклонил отзыв сессий: {error.__class__.__name__}",
        ) from error
