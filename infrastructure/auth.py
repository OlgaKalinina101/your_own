"""Lightweight bearer-token authentication.

The server generates a random token on first run and stores it in
``data/auth_token.txt``.  Every request must include::

    Authorization: Bearer <token>

The token is displayed in the console on startup so the user can copy it
into the desktop/mobile client.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from infrastructure.paths import DATA_DIR
from infrastructure.state_file import atomic_write_text

logger = logging.getLogger("auth")

_DATA_DIR = DATA_DIR
_TOKEN_FILE = _DATA_DIR / "auth_token.txt"

_bearer_scheme = HTTPBearer(auto_error=False)


def _ensure_token() -> str:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _TOKEN_FILE.exists():
        token = _TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    atomic_write_text(_TOKEN_FILE, token)
    return token


AUTH_TOKEN: str = _ensure_token()


def current_token() -> str:
    """The token in force right now.

    A function rather than the module constant, because the constant is read at
    import time and rotation has to take effect without a restart.
    """
    return AUTH_TOKEN


def rotate_token() -> str:
    """Issue a new token and invalidate the old one immediately.

    There is one account and one token, so this token *is* the security
    boundary — and until now it was permanent. There was no way to revoke it
    after a leak short of deleting the file and restarting, which on a server
    means downtime to recover from an exposure.

    Every other client is signed out by this: that is the point.
    """
    global AUTH_TOKEN

    new_token = secrets.token_urlsafe(32)
    atomic_write_text(_TOKEN_FILE, new_token)
    AUTH_TOKEN = new_token
    logger.warning(
        "[auth] token rotated — every other client is now signed out and needs "
        "the new one from data/auth_token.txt"
    )
    return new_token


# Saying out loud when the token crosses a network in the clear.
#
# Running locally is the point of this app and must stay frictionless: on
# 127.0.0.1 plaintext is correct and this says nothing. Reaching it from
# elsewhere is a different situation — this token is the whole security
# boundary and it travels in a header — and TLS belongs to the deployment, not
# to this process. What this process can do is state the fact, once per address,
# so that it is a decision rather than an oversight.
#
# Checked here rather than in middleware on purpose: a Starlette HTTP
# middleware pumps every response through an intermediate task, which breaks
# disconnect handling on the SSE stream. Measured — it turned a client hang-up
# into "athrow(): asynchronous generator is already running".

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_warned_plaintext: set[str] = set()


def _note_if_plaintext(request: Request | None) -> None:
    if request is None:
        return
    client = request.client.host if request.client else ""
    if not client:
        return

    forwarded_for = request.headers.get("X-Forwarded-For")
    forwarded_host = request.headers.get("X-Forwarded-Host")
    if client in _LOOPBACK and not (forwarded_for or forwarded_host):
        return  # genuinely local

    # Behind a proxy the socket says 127.0.0.1 and the headers say who it really
    # was; the same headers say whether the proxy terminated TLS.
    if request.headers.get("X-Forwarded-Proto") == "https" or request.url.scheme == "https":
        return

    origin = (forwarded_for or "").split(",")[0].strip() or forwarded_host or client
    if origin in _warned_plaintext:
        return
    _warned_plaintext.add(origin)
    logger.warning(
        "[auth] a request arrived from %s over plain http — the bearer token "
        "crossed the network readable. Fine on a trusted LAN; put TLS in front "
        "of this before it is reachable from anywhere else.",
        origin,
    )


async def require_auth(
    request: Request = None,  # type: ignore[assignment]
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency — returns the token if valid, raises 401 otherwise."""
    _note_if_plaintext(request)
    if creds is None or not secrets.compare_digest(creds.credentials, current_token()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing auth token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return creds.credentials


# ── Media signatures ─────────────────────────────────────────────────────────
#
# An `<img src=…>` cannot carry an Authorization header. No browser sends one,
# and React Native's `<Image>` does not either — so the moment the three media
# routes got `Depends(require_auth)`, every generated image, upload and body
# asset started answering 401. The frontend hides that behind an `onError`
# handler, which is why it was silent.
#
# Putting the master token in the URL would fix it and cost more than it saves:
# it lands in the server's access log, in browser history and in the Referer of
# anything the page links to. On rented hosting those logs are not ours.
#
# So the URL carries a signature *derived* from the token instead. It expires,
# it cannot be replayed as a token, and — because the key is `current_token()`
# rather than a constant — `rotate_token()` invalidates every outstanding
# signature for free.
#
# One signature covers all media rather than one file: there is a single
# account, so whoever holds a valid signature is already entitled to every
# file. Per-file signing would buy nothing and cost a round trip per image.

MEDIA_SIG_TTL_SECONDS = 900


def issue_media_signature(ttl_seconds: int = MEDIA_SIG_TTL_SECONDS) -> tuple[str, int]:
    """Return ``(signature, ttl)`` for appending to media URLs as ``?sig=``."""
    expires_at = int(time.time()) + ttl_seconds
    digest = hmac.new(
        current_token().encode("utf-8"),
        str(expires_at).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{expires_at}.{digest}", ttl_seconds


def _media_signature_valid(signature: str | None) -> bool:
    if not signature:
        return False
    expires_raw, _, digest = signature.partition(".")
    if not digest:
        return False
    try:
        expires_at = int(expires_raw)
    except ValueError:
        return False
    if expires_at < int(time.time()):
        return False
    expected = hmac.new(
        current_token().encode("utf-8"),
        expires_raw.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return secrets.compare_digest(digest, expected)


async def require_auth_or_media_signature(
    request: Request = None,  # type: ignore[assignment]
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """Like :func:`require_auth`, but a valid ``?sig=`` is also accepted.

    Only the three media routes use this. Everything else stays header-only:
    a signature is proof that someone held the token recently, which is the
    right bar for fetching an image and the wrong bar for changing settings.
    """
    _note_if_plaintext(request)
    if creds is not None and secrets.compare_digest(creds.credentials, current_token()):
        return creds.credentials

    signature = request.query_params.get("sig") if request is not None else None
    if _media_signature_valid(signature):
        return "media-signature"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing auth token",
        headers={"WWW-Authenticate": "Bearer"},
    )
