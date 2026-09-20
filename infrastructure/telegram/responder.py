"""Whether to say something into the room, and saying it.

Called by the listener after a poll has stored new messages from the group.
Three questions, in order:

1. **Is this for him?** Someone wrote his name or his handle, or replied to
   one of his messages — that is *addressed*. Or he spoke in the room a few
   minutes ago and people are still talking — that is *in conversation*, and
   the next lines may well be for him even without his name on them. Anything
   else is the room talking among itself: stored, not answered. He hears it
   at his next waking, not now.
2. **What does he say?** One model call, with who he is, the last stretch of
   the room, and what his memory turns up for the lines that pulled him in. He
   may answer with the single word ``SILENT``; that is a decision, not a
   failure.
3. **Send and remember.** The reply goes to the room and into the table as his
   own row, so the next poll's transcript has both sides.

His own initiative — writing to the room because something at a waking made
him want to — does not come through here. That is a reflection command.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from infrastructure.autonomy import context
from infrastructure.autonomy.helpers import detect_lang, get_ai_name, make_llm_client
from infrastructure.clock import format_local
from infrastructure.database.models.channel_message import ChannelMessage
from infrastructure.llm.prompt_loader import get_prompt

logger = logging.getLogger("telegram.responder")

_PROMPT = "infrastructure/telegram/prompts/group_reply.md"

# How long after his last line the room still counts as talking with him.
CONVERSATION_WINDOW_MINUTES = 10
# How much of the room he is shown when deciding.
ROOM_CONTEXT_MESSAGES = 30
# The word that means "I choose not to".
SILENT = "SILENT"
# Reasoning models bill thinking against max_tokens; same budget as his other
# single-reply calls.
REPLY_MAX_TOKENS = 16000

_WHY = {
    "ru": {
        "addressed": "к тебе обратились — по имени или ответом на твоё сообщение.",
        "conversation": "ты недавно говорил здесь, и разговор продолжается.",
    },
    "en": {
        "addressed": "someone addressed you — by name, or by replying to your message.",
        "conversation": "you spoke here a few minutes ago and the conversation is still going.",
    },
}


@dataclass
class Trigger:
    kind: str                       # "addressed" | "conversation"
    reply_to: int | None = None     # the message to answer under, if one stands out


# ── 1. Is this for him? ──────────────────────────────────────────────────────


def _mentions(text: str, *names: str) -> bool:
    lowered = text.lower()
    for name in names:
        name = (name or "").strip().lower()
        if not name:
            continue
        if name.startswith("@"):
            if name in lowered:
                return True
        elif re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lowered):
            return True
    return False


def decide(
    new_rows: list[ChannelMessage],
    recent: list[ChannelMessage],
    *,
    ai_name: str,
    bot_username: str,
    now: datetime,
) -> Trigger | None:
    """Which of the new lines, if any, make the room his to answer.

    *recent* is the stored stretch of the room including his own rows; a reply
    to one of those, a mention of his name or handle, or his own voice within
    the last few minutes are the three ways in.
    """
    own_ids = {row.message_id for row in recent if row.is_self}
    handle = f"@{bot_username}" if bot_username and not bot_username.startswith("@") else bot_username

    addressed: ChannelMessage | None = None
    for row in new_rows:
        if row.is_self:
            continue
        if row.reply_to_message_id in own_ids or _mentions(row.text, ai_name, handle):
            addressed = row   # the latest one wins: that is the line to answer under
    if addressed is not None:
        return Trigger(kind="addressed", reply_to=addressed.message_id)

    last_own = max((row.created_at for row in recent if row.is_self), default=None)
    if last_own is not None:
        if last_own.tzinfo is None:
            last_own = last_own.replace(tzinfo=timezone.utc)
        if now - last_own <= timedelta(minutes=CONVERSATION_WINDOW_MINUTES):
            if any(not row.is_self for row in new_rows):
                return Trigger(kind="conversation")
    return None


# ── 2. What does he say? ─────────────────────────────────────────────────────


def render_room(rows: list[ChannelMessage], *, ai_name: str, lang: str) -> str:
    """The stretch of the room as a transcript, with her and him marked.

    The marks are the whole point: in a list of first names she is one name
    among five, and the one thing he must not do here is fail to know her.
    """
    her = "она" if lang == "ru" else "her"
    you = "ты" if lang == "ru" else "you"
    lines: list[str] = []
    for row in rows:
        stamp = format_local(row.created_at, "%H:%M") if row.created_at else "--:--"
        if row.is_self:
            who = f"{ai_name} ({you})"
        elif row.is_owner:
            who = f"{row.sender_name} ({her})"
        else:
            who = row.sender_name or row.sender_id
        prefix = f"[{stamp}] #{row.message_id} "
        if row.reply_to_message_id:
            prefix += f"↩#{row.reply_to_message_id} "
        lines.append(f"{prefix}{who}: {row.text}")
    return "\n".join(lines)


def _her_name(recent: list[ChannelMessage], lang: str) -> str:
    for row in reversed(recent):
        if row.is_owner and row.sender_name:
            return row.sender_name
    return "(ещё не писала здесь)" if lang == "ru" else "(has not written here yet)"


async def _recall(account_id: str, text: str, lang: str) -> tuple[str, list[str]]:
    """What long-term memory says about the lines that pulled him in.

    Returns the block and the fact ids, so usage can be stamped after a reply
    actually goes out. Failing here is a thinner answer, not a lost one.
    """
    if not text.strip():
        return "", []
    try:
        from infrastructure.memory.chroma_pipeline import get_chroma_pipeline
        from infrastructure.memory.retrieval import humanize_timestamp
        from infrastructure.settings_store import load_settings

        cutoff = int(load_settings().get("memory_cutoff_days", 2))
        pipeline = get_chroma_pipeline()
        facts = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: pipeline.query_similar_multi(
                account_id=account_id, message=text, top_k=5, days_cutoff=cutoff,
            ),
        )
    except Exception as exc:
        logger.warning("[telegram.responder] memory unavailable, answering without it: %s", exc)
        return "", []

    lines = []
    for fact in facts or []:
        meta = fact.get("metadata") or {}
        lines.append(f"— ({humanize_timestamp(meta.get('created_at'), lang)}) {fact.get('text', '').strip()}")
    return "\n".join(lines), [f["id"] for f in facts or [] if f.get("id")]


def _mark_used(fact_ids: list[str]) -> None:
    if not fact_ids:
        return
    try:
        from infrastructure.memory.chroma_pipeline import get_chroma_pipeline

        pipeline = get_chroma_pipeline()
        for fact_id in fact_ids:
            pipeline.update_usage(fact_id)
    except Exception as exc:
        logger.warning("[telegram.responder] could not stamp memory usage: %s", exc)


async def compose(
    *,
    account_id: str,
    api_key: str,
    recent: list[ChannelMessage],
    new_rows: list[ChannelMessage],
    trigger: Trigger,
    bot_username: str,
) -> tuple[str | None, list[str]]:
    """Ask him what he wants to say. ``None`` means he chose silence.

    Also returns the fact ids that were shown, for the caller to stamp once
    the message has actually been sent.
    """
    ai_name = get_ai_name()
    room_text = "\n".join(row.text for row in recent[-ROOM_CONTEXT_MESSAGES:])
    lang = detect_lang(room_text)

    state = context.build(
        context.Consumer.TELEGRAM,
        context.Request(account_id=account_id, lang=lang),
    )
    pull = "\n".join(row.text for row in new_rows if not row.is_self)[-1500:]
    memories, fact_ids = await _recall(account_id, pull, lang)

    system = get_prompt(_PROMPT, lang=lang, section="system", ai_name=ai_name)
    user = get_prompt(
        _PROMPT, lang=lang, section="user",
        ai_name=ai_name,
        memories=memories or ("(ничего не всплыло)" if lang == "ru" else "(nothing surfaced)"),
        room=render_room(recent[-ROOM_CONTEXT_MESSAGES:], ai_name=ai_name, lang=lang),
        her_name=_her_name(recent, lang),
        bot_username=bot_username or "?",
        why=_WHY.get(lang, _WHY["en"])[trigger.kind],
        **state,
    )

    client = make_llm_client(api_key)
    text, finish_reason = await client.complete(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=REPLY_MAX_TOKENS,
        temperature=0.7,
        return_meta=True,
    )
    text = (text or "").strip()
    if finish_reason == "length":
        # A clipped reply is not a reply. Better one missed line in a group
        # chat than half a sentence posted under his name.
        logger.warning("[telegram.responder:%s] reply hit max_tokens — not sent", account_id)
        return None, fact_ids
    if not text or text.upper().strip(".!") == SILENT:
        logger.info("[telegram.responder:%s] chose silence (%s)", account_id, trigger.kind)
        return None, fact_ids
    return text, fact_ids


# ── 3. Send and remember ─────────────────────────────────────────────────────


def own_row(sent: dict, *, account_id: str, chat_id: str, bot_id: int | str, ai_name: str) -> ChannelMessage:
    """His message as the room will have it, from what Telegram sent back."""
    stamp = sent.get("date")
    return ChannelMessage(
        id=uuid.uuid4(),
        account_id=account_id,
        channel="telegram",
        chat_id=str(chat_id),
        message_id=int(sent.get("message_id", 0)),
        sender_id=str(bot_id),
        sender_name=ai_name,
        is_owner=False,
        is_self=True,
        reply_to_message_id=(sent.get("reply_to_message") or {}).get("message_id"),
        text=sent.get("text", ""),
        created_at=datetime.fromtimestamp(int(stamp), tz=timezone.utc) if stamp else datetime.now(timezone.utc),
    )


async def consider(account_id: str, new_rows: list[ChannelMessage]) -> str | None:
    """The whole thing: decide, compose, send, remember. Returns what he said."""
    from infrastructure.database.engine import get_db_session
    from infrastructure.database.repositories.channel_repo import ChannelRepository
    from infrastructure.settings_store import load_settings
    from infrastructure.telegram import listener
    from infrastructure.telegram.client import get_client

    if not new_rows:
        return None
    settings = load_settings()
    api_key = settings.get("openrouter_api_key", "")
    chat_id = str(settings.get("telegram_chat_id") or "")
    if not api_key or not chat_id:
        return None

    state = listener.read_state(account_id)
    bot = state.get("bot") or {}
    bot_username = bot.get("username", "")
    ai_name = get_ai_name()

    async with get_db_session() as db:
        recent = await ChannelRepository(db).get_recent(account_id, chat_id, limit=ROOM_CONTEXT_MESSAGES)

    trigger = decide(
        new_rows, recent, ai_name=ai_name, bot_username=bot_username,
        now=datetime.now(timezone.utc),
    )
    if trigger is None:
        return None
    logger.info("[telegram.responder:%s] the room is his to answer: %s", account_id, trigger.kind)

    text, fact_ids = await compose(
        account_id=account_id, api_key=api_key, recent=recent, new_rows=new_rows,
        trigger=trigger, bot_username=bot_username,
    )
    if text is None:
        return None

    client = get_client()
    if client is None:
        return None
    sent = await client.send_message(chat_id, text, reply_to_message_id=trigger.reply_to)
    logger.info("[telegram.responder:%s] said: %s", account_id, text[:100])

    row = own_row(sent, account_id=account_id, chat_id=chat_id, bot_id=bot.get("id", ""), ai_name=ai_name)
    try:
        await asyncio.get_running_loop().run_in_executor(None, listener.fill_embeddings, [row])
        async with get_db_session() as db:
            await ChannelRepository(db).save_many([row])
    except Exception as exc:
        # The room has the message; only our copy is missing. Say so — the
        # next transcript would otherwise show a reply to nothing.
        logger.error("[telegram.responder:%s] sent but could not store own row: %s", account_id, exc)

    await asyncio.get_running_loop().run_in_executor(None, _mark_used, fact_ids)
    return text
