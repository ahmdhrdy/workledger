import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from .domain import utc
from .models import Session, User, now

hasher = PasswordHasher()
DUMMY = hasher.hash("unusable-workledger-placeholder")
COOKIE = "workledger_session"


def token_hash(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def verify_password(hashed: str, value: str):
    try:
        return hasher.verify(hashed, value)
    except (VerificationError, InvalidHashError):
        return False


def sign_in(db: DbSession, response, user: User, settings):
    raw = secrets.token_urlsafe(48)
    db.add(
        Session(
            token_hash=token_hash(raw),
            user_id=user.id,
            expires_at=now() + timedelta(hours=settings.session_hours),
        )
    )
    response.set_cookie(
        COOKIE,
        raw,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.session_hours * 3600,
        path="/",
    )


def principal(request: Request, db: DbSession):
    raw = request.cookies.get(COOKIE, "")
    session = db.get(Session, token_hash(raw)) if raw else None
    if not session or utc(session.expires_at) < now():
        raise HTTPException(401, "Please sign in")
    user = db.get(User, session.user_id)
    if not user or user.role == "disabled":
        raise HTTPException(401, "Session is no longer valid")
    return user


def require_role(user: User, *roles: str):
    if user.role not in roles:
        raise HTTPException(403, "Your role cannot perform this action")
