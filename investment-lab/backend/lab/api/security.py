"""Session auth (signed HttpOnly cookie), CSRF double-submit, rate limiting, structured errors."""
from __future__ import annotations

import secrets
import threading
import time
import uuid
from collections import defaultdict

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User

SESSION_COOKIE = "lab_session"
CSRF_COOKIE = "lab_csrf"
SESSION_MAX_AGE = 14 * 24 * 3600


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(status_code=status, detail={"code": code, "message": message})


def _ser() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="lab-session")


def make_session_token(user_id: str) -> str:
    return _ser().dumps({"uid": user_id, "nonce": secrets.token_hex(8)})


def read_session_token(token: str) -> str | None:
    try:
        return _ser().loads(token, max_age=SESSION_MAX_AGE)["uid"]
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    tok = request.cookies.get(SESSION_COOKIE)
    uid = read_session_token(tok) if tok else None
    user = db.get(User, uid) if uid else None
    if user is None:
        raise ApiError(401, "unauthenticated", "login required")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("x-csrf-token", "")
        cookie = request.cookies.get(CSRF_COOKIE, "")
        if not sent or not cookie or not secrets.compare_digest(sent, cookie):
            raise ApiError(403, "csrf_failed", "missing or invalid CSRF token")
    return user


class RateLimiter:
    """In-process sliding window (per key). Durable limits for jobs are enforced by schedule keys."""

    def __init__(self):
        self.hits: dict[str, list[float]] = defaultdict(list)
        self.lock = threading.Lock()

    def check(self, key: str, limit: int, window: float) -> None:
        now = time.monotonic()
        with self.lock:
            h = [t for t in self.hits[key] if now - t < window]
            if len(h) >= limit:
                raise ApiError(429, "rate_limited", f"too many requests; retry in {int(window - (now - h[0])) + 1}s")
            h.append(now)
            self.hits[key] = h


limiter = RateLimiter()


def correlation_id(request: Request) -> str:
    cid = request.headers.get("x-correlation-id")
    if not cid or len(cid) > 64:
        cid = uuid.uuid4().hex
    request.state.correlation_id = cid
    return cid
