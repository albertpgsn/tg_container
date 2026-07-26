from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import get_db
from .models import AdminSession, AdminUser, AuditLog


SESSION_COOKIE = "crm_session"
CSRF_COOKIE = "crm_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def hash_token(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-real-ip", "").strip()
    if forwarded:
        return forwarded[:64]
    return request.client.host[:64] if request.client else "unknown"


def audit(
    db: Session,
    *,
    actor: str,
    action: str,
    request: Request,
    admin_id: str | None = None,
    target: str | None = None,
) -> None:
    db.add(
        AuditLog(
            admin_id=admin_id,
            actor=actor[:120],
            action=action[:80],
            target=target[:160] if target else None,
            ip_address=client_ip(request),
        )
    )


def require_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход администратора")

    now = datetime.now(timezone.utc)
    session = (
        db.query(AdminSession)
        .filter(AdminSession.token_hash == hash_token(raw_token), AdminSession.expires_at > now)
        .first()
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия администратора истекла")

    admin = db.get(AdminUser, session.admin_id)
    if not admin or not admin.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ администратора отключён")

    if request.method not in SAFE_METHODS:
        csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
        csrf_header = request.headers.get("x-csrf-token", "")
        valid_csrf = (
            bool(csrf_cookie)
            and bool(csrf_header)
            and compare_digest(csrf_cookie, csrf_header)
            and compare_digest(hash_token(csrf_cookie), session.csrf_hash)
        )
        if not valid_csrf:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF-проверка не пройдена")

    session.last_seen_at = now
    db.commit()
    request.state.admin = admin
    request.state.admin_session = session
    return admin
