"""The listener — what the room said, written down.

One tick is one long poll: fetch updates, turn the ones from *the* group into
rows, store them, advance the cursor. It does not answer anyone. Answering is a
decision, and decisions are made where the rest of his decisions are made.

Two things are kept on disk beside the rest of his state, in
``data/autonomy/{account}/telegram.json``:

* ``offset`` — the polling cursor. Telegram keeps an update until it is
  acknowledged by a higher offset, so without this a restart replays every
  message since the last poll. The unique constraint on the table would drop
  the duplicates, but the log would say the room was busy when it was not.
* ``chats`` — every room the bot has been spoken to in, by id and title. The
  settings page shows this list so the group can be picked rather than typed:
  a Telegram group id is a negative thirteen-digit number nobody knows by heart.

Until a chat is chosen in settings nothing is stored: the bot may sit in more
than one group, and only one of them is his.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from infrastructure.database.models.channel_message import ChannelMessage
from infrastructure.paths import AUTONOMY_DIR
from infrastructure.state_file import atomic_write_text, read_json

logger = logging.getLogger("telegram.listener")

_DATA_DIR = AUTONOMY_DIR
_lock = Lock()

POLL_TIMEOUT_SECONDS = 25

# What a message with no text is shown as. He should know something was
# posted even when it was a picture; the token says what kind.
_MEDIA_TOKENS = (
    ("photo", "[photo]"),
    ("sticker", "[sticker]"),
    ("voice", "[voice message]"),
    ("video_note", "[video message]"),
    ("video", "[video]"),
    ("animation", "[gif]"),
    ("audio", "[audio]"),
    ("document", "[file]"),
    ("location", "[location]"),
    ("poll", "[poll]"),
)


# ── State file ───────────────────────────────────────────────────────────────


def _state_path(account_id: str) -> Path:
    directory = _DATA_DIR / account_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "telegram.json"


def read_state(account_id: str) -> dict:
    state = read_json(_state_path(account_id), default={}, log=logger)
    return state if isinstance(state, dict) else {}


def write_state(account_id: str, state: dict) -> None:
    with _lock:
        atomic_write_text(_state_path(account_id), json.dumps(state, indent=2, ensure_ascii=False))


def _remember_chat(state: dict, chat: dict, seen_at: datetime) -> None:
    """Add or refresh one room in ``state["chats"]``."""
    chat_id = str(chat.get("id", ""))
    if not chat_id:
        return
    chats = state.setdefault("chats", {})
    entry = chats.get(chat_id, {})
    title = chat.get("title") or " ".join(
        part for part in (chat.get("first_name"), chat.get("last_name")) if part
    ) or chat.get("username") or chat_id
    entry.update({
        "title": title,
        "type": chat.get("type", ""),
        "last_seen": seen_at.isoformat(timespec="seconds"),
    })
    chats[chat_id] = entry


# ── Updates → rows ───────────────────────────────────────────────────────────


def sender_name_of(user: dict) -> str:
    """A person's name the way the room sees it: first and last, else handle."""
    full = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    return full or user.get("username") or str(user.get("id", ""))


def text_of(message: dict) -> str:
    """The message text, a caption, or a token for what kind of media it was."""
    text = message.get("text") or message.get("caption") or ""
    if text:
        return text
    for key, token in _MEDIA_TOKENS:
        if key in message:
            return token
    return ""


def row_from_message(
    message: dict,
    *,
    account_id: str,
    owner_user_id: str,
) -> ChannelMessage | None:
    """One Telegram message as a row, or ``None`` if there is nothing to keep.

    Service messages (someone joined, the title changed) have no sender or no
    text and are dropped. ``date`` is Unix seconds in UTC.
    """
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    text = text_of(message)
    if not sender or not chat or not text:
        return None

    sender_id = str(sender.get("id", ""))
    stamp = message.get("date")
    # "Her" may be given as the numeric id the picker stores, or as a handle
    # typed by hand — "@nishtyakina" is what a person knows about themselves.
    owner = str(owner_user_id or "").strip().lstrip("@").lower()
    is_owner = bool(owner) and (
        sender_id == owner or str(sender.get("username") or "").lower() == owner
    )
    created_at = (
        datetime.fromtimestamp(int(stamp), tz=timezone.utc)
        if stamp else datetime.now(timezone.utc)
    )
    reply = message.get("reply_to_message") or {}

    return ChannelMessage(
        id=uuid.uuid4(),
        account_id=account_id,
        channel="telegram",
        chat_id=str(chat.get("id")),
        message_id=int(message.get("message_id", 0)),
        sender_id=sender_id,
        sender_name=sender_name_of(sender),
        is_owner=is_owner,
        is_self=False,
        reply_to_message_id=int(reply["message_id"]) if reply.get("message_id") else None,
        text=text,
        created_at=created_at,
    )


def rows_from_updates(
    updates: list[dict],
    *,
    account_id: str,
    chat_id: str,
    owner_user_id: str,
    state: dict,
) -> tuple[list[ChannelMessage], int | None]:
    """Rows from *chat_id* only, and the offset to acknowledge them all with.

    Every room seen is remembered in *state* whether or not it is the chosen
    one — that is how the settings page learns the groups exist. Rows are kept
    only for the chosen room.
    """
    rows: list[ChannelMessage] = []
    last_update_id: int | None = None
    now = datetime.now(timezone.utc)

    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            last_update_id = update_id if last_update_id is None else max(last_update_id, update_id)

        message = update.get("message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat") or {}
        _remember_chat(state, chat, now)
        if not chat_id or str(chat.get("id")) != str(chat_id):
            continue
        row = row_from_message(message, account_id=account_id, owner_user_id=owner_user_id)
        if row is not None:
            rows.append(row)

    next_offset = last_update_id + 1 if last_update_id is not None else None
    return rows, next_offset


def fill_embeddings(rows: list[ChannelMessage]) -> None:
    """Embed the texts so the room is searchable later. Synchronous CPU work."""
    if not rows:
        return
    from infrastructure.memory.embedder import embed_texts

    vectors = embed_texts([row.text for row in rows])
    for row, vector in zip(rows, vectors):
        row.embedding = vector


# ── One tick ─────────────────────────────────────────────────────────────────


async def ensure_bot_identity(account_id: str, client, state: dict) -> dict | None:
    """Ask Telegram who the bot is, once, and keep the answer in the state."""
    bot = state.get("bot")
    if bot and bot.get("id"):
        return bot
    me = await client.get_me()
    bot = {"id": me.get("id"), "username": me.get("username", ""), "name": me.get("first_name", "")}
    state["bot"] = bot
    write_state(account_id, state)
    logger.info("[telegram:%s] bot is @%s (id %s)", account_id, bot["username"], bot["id"])
    return bot


async def tick(account_id: str) -> bool:
    """One long poll. Returns ``False`` when there is no token to poll with.

    Everything that can fail here raises to the worker, which logs it and
    waits a tick: a token that is wrong, a second poller on the same token,
    the network. Nothing is retried inside — a long poll is its own retry.
    """
    from infrastructure.settings_store import load_settings
    from infrastructure.telegram.client import get_client

    client = get_client()
    if client is None:
        return False

    settings = load_settings()
    chat_id = str(settings.get("telegram_chat_id") or "").strip()
    owner_user_id = str(settings.get("telegram_owner_user_id") or "").strip()

    state = read_state(account_id)
    await ensure_bot_identity(account_id, client, state)

    updates = await client.get_updates(state.get("offset"), timeout=POLL_TIMEOUT_SECONDS)
    if not updates:
        return True

    rows, next_offset = rows_from_updates(
        updates, account_id=account_id, chat_id=chat_id,
        owner_user_id=owner_user_id, state=state,
    )

    written = 0
    if rows:
        from infrastructure.database.engine import get_db_session
        from infrastructure.database.repositories.channel_repo import ChannelRepository

        await asyncio.get_running_loop().run_in_executor(None, fill_embeddings, rows)
        async with get_db_session() as db:
            written = await ChannelRepository(db).save_many(rows)

    # The cursor moves only after the rows are safe: a crash between the two
    # costs a replay, and the unique constraint makes the replay harmless.
    if next_offset is not None:
        state["offset"] = next_offset
    write_state(account_id, state)

    logger.info(
        "[telegram:%s] poll: %d update(s), %d from the room, %d new",
        account_id, len(updates), len(rows), written,
    )

    # Only now, with the rows stored and the cursor moved: a reply that fails
    # must not cost the poll, and a poll must not replay because a reply took
    # too long.
    if written:
        from infrastructure.telegram import responder

        try:
            await responder.consider(account_id, rows)
        except Exception as exc:
            logger.warning("[telegram:%s] reply failed, the room was still recorded: %s", account_id, exc)
    return True
