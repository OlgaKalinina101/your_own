"""Lightweight bearer-token authentication.

The server generates a random token on first run and stores it in
``data/auth_token.txt``.  Every request must include::

    Authorization: Bearer <token>

The token is displayed in the console on startup so the user can copy it
into the desktop/mobile client.
"""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_TOKEN_FILE = _DATA_DIR / "auth_token.txt"

_bearer_scheme = HTTPBearer(auto_error=False)


def _ensure_token() -> str:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _TOKEN_FILE.exists():
        token = _TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    _TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


AUTH_TOKEN: str = _ensure_token()


async def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency — returns the token if valid, raises 401 otherwise."""
    if creds is None or creds.credentials != AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing auth token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return creds.credentials
