"""Telegram Bot API client — the wire, and only the wire.

Long polling rather than a webhook: the backend runs on a laptop as often as
on the server, and a webhook needs a public address the laptop does not have.
``getUpdates`` with a timeout is one open request at a time, which is what a
bot in one group needs.

The token is read from settings at call time, like Pushy's, so a change on the
settings page is in force on the next poll without a restart.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger("telegram")

_API = "https://api.telegram.org"

# Telegram caps a message at 4096 characters; over that the API refuses it.
MESSAGE_MAX_CHARS = 4096
# ...and a photo caption at 1024.
CAPTION_MAX_CHARS = 1024


class TelegramError(RuntimeError):
    def __init__(self, status: int, description: str = "") -> None:
        super().__init__(f"telegram {status}: {description}")
        self.status = status
        self.description = description

    @property
    def conflict(self) -> bool:
        """Another process is polling with this token.

        Telegram allows one ``getUpdates`` consumer per bot; a second one gets
        409 until the first stops. Worth naming, because the symptom otherwise
        is a bot that stays silent while every log line says it is polling.
        """
        return self.status == 409


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def _url(self, method: str) -> str:
        return f"{_API}/bot{self.token}/{method}"

    async def _call(self, method: str, params: dict | None = None, *, http_timeout: float) -> Any:
        payload = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url(method), json=payload,
                    timeout=aiohttp.ClientTimeout(total=http_timeout),
                ) as resp:
                    body = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise TelegramError(0, f"network: {exc}") from exc

        if not isinstance(body, dict) or not body.get("ok"):
            if isinstance(body, dict):
                raise TelegramError(int(body.get("error_code", 0)), str(body.get("description", "")))
            raise TelegramError(0, str(body))
        return body.get("result")

    async def get_me(self) -> dict:
        """Who the bot is: id and username. Cached by the listener."""
        return await self._call("getMe", http_timeout=15)

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        """One long poll. Returns the raw updates, possibly none.

        Only ``message`` updates are asked for: edits, reactions and member
        changes are not part of what he reads. The HTTP timeout runs a little
        past Telegram's so a quiet poll ends on their side, not ours.
        """
        result = await self._call(
            "getUpdates",
            {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
            http_timeout=timeout + 10,
        )
        return list(result or [])

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> dict:
        """Post to the room. Returns the sent message as Telegram reports it."""
        if len(text) > MESSAGE_MAX_CHARS:
            logger.warning("[telegram] message of %d chars cut to %d", len(text), MESSAGE_MAX_CHARS)
            text = text[:MESSAGE_MAX_CHARS]
        return await self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "reply_to_message_id": reply_to_message_id},
            http_timeout=20,
        )


    async def send_photo(
        self,
        chat_id: str | int,
        path,
        *,
        caption: str = "",
        reply_to_message_id: int | None = None,
    ) -> dict:
        """Upload one picture from disk. Multipart, unlike every other call here."""
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        if caption:
            form.add_field("caption", caption[:CAPTION_MAX_CHARS])
        if reply_to_message_id:
            form.add_field("reply_to_message_id", str(reply_to_message_id))
        with open(path, "rb") as handle:
            form.add_field("photo", handle.read(), filename="image.png", content_type="image/png")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url("sendPhoto"), data=form,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    body = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise TelegramError(0, f"network: {exc}") from exc
        if not isinstance(body, dict) or not body.get("ok"):
            if isinstance(body, dict):
                raise TelegramError(int(body.get("error_code", 0)), str(body.get("description", "")))
            raise TelegramError(0, str(body))
        return body.get("result")


def get_client() -> TelegramClient | None:
    """A client from current settings, or ``None`` when no token is set."""
    from infrastructure.settings_store import load_settings

    token = (load_settings().get("telegram_bot_token") or "").strip()
    if not token:
        return None
    return TelegramClient(token)
