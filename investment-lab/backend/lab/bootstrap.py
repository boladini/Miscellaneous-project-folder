"""Idempotent startup: ensure the owner account and its five portfolios exist."""
from __future__ import annotations

import logging

import bcrypt
from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .models import User
from .services.market_data import ensure_instruments
from .services.portfolios import init_portfolios

log = logging.getLogger("lab.bootstrap")


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_password(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), h.encode())
    except ValueError:
        return False


def bootstrap() -> str:
    cfg = get_settings()
    with session_scope() as s:
        u = s.scalars(select(User).where(User.email == cfg.admin_email.lower())).first()
        if u is None:
            u = User(email=cfg.admin_email.lower(), password_hash=hash_password(cfg.admin_password), timezone=cfg.display_timezone)
            s.add(u)
            s.flush()
            log.info("created owner account %s", cfg.admin_email)
        init_portfolios(s, u.id)
        ensure_instruments(s)
        return u.id
