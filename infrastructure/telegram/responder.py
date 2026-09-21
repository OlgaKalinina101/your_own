"""Whether to say something into the room, and saying it.

Called by the listener after a poll has stored new messages from the group.
Three questions, in order:

1. **Is this for him?** Someone wrote his name or his handle, or replied to
   one of his messages — that is *addressed*. Or he spoke in the room a few
   minutes ago and people are still talking — that is *in conversation*, and
   the next lines may well be for him even without his name on them. Anything
   else is the room talking among itself: stored, not answered. He reads it
   whole at his next waking.
2. **What does he say?** A short loop rather than one call, because in the
   room he can do five things besides talk:

   * ``[WRITE_NOTE: …]`` — write something down. It lands on his desk marked
     with the group's name, so it reaches long-term memory through the rotator
     like any other note, without crowding the two of them off the desk. This
     exists because he was saying "noted" to people with nothing to note with.
   * ``[FETCH_URL: …]`` — open a link someone posted. The page comes back to
     him and he answers again, knowing what is on it.
   * ``[WEB_SEARCH: …]`` — the private chat's web-search skill: its own
     description, its own wording for what came back, the same research agent.
   * ``[GENERATE_IMAGE: model | prompt]`` — the same skill as in the private
     chat; the picture is posted to the room.
   * ``[REPLY_TO: #id]`` — answer under a particular line rather than the one
     that pulled him in.

   He may still answer ``SILENT``; that is a decision, not a failure — and a
   note taken alongside it is still taken.
3. **Send and remember.** The reply goes to the room and into the table as his
   own row, so the next transcript has both sides.

His own initiative — writing to the room because something at a waking made
him want to — does not come through here. That is a reflection command.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
# A link opened, then an answer: two rounds is the normal case, three the limit.
MAX_ROUNDS = 3
MAX_FETCHES_PER_ROUND = 2
MAX_SEARCHES_PER_ROUND = 2

_NOTE_RE = re.compile(r"\[WRITE[_ ]NOTE:\s*(?P<text>.+?)\]", re.IGNORECASE | re.DOTALL)
_FETCH_RE = re.compile(r"\[FETCH[_ ]URL:\s*(?P<url>\S+?)\s*\]", re.IGNORECASE)
_SEARCH_RE = re.compile(r"\[WEB[_ ]SEARCH:\s*(?P<query>.+?)\]", re.IGNORECASE | re.DOTALL)
_IMAGE_RE = re.compile(r"\[GENERATE[_ ]IMAGE:\s*(.*?)\]", re.IGNORECASE | re.DOTALL)
_REPLY_TO_RE = re.compile(r"\[REPLY[_ ]TO:\s*#?(?P<id>\d+)\s*\]", re.IGNORECASE)
_ANY_CMD_RE = re.compile(
    r"\[(?:WRITE[_ ]NOTE|FETCH[_ ]URL|WEB[_ ]SEARCH|GENERATE[_ ]IMAGE|REPLY[_ ]TO):[^\]]*\]",
    re.IGNORECASE | re.DOTALL,
)
# A reply cut mid-command: the opener is there, the bracket is not.
_UNCLOSED_RE = re.compile(
    r"\[(?:WRITE[_ ]NOTE|FETCH[_ ]URL|WEB[_ ]SEARCH|GENERATE[_ ]IMAGE|REPLY[_ ]TO):[^\]]*$",
    re.IGNORECASE | re.DOTALL,
)
_MEDIA_TOKEN_RE = re.compile(r"^\[[a-z ]+\]$")

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

_FETCH_TASK = {
    "ru": "Открой страницу {url} и перескажи, что на ней: о чём она, главное содержание, "
          "автор и дата, если есть. Если страница не открывается или закрыта — так и скажи.",
    "en": "Open the page {url} and tell what is on it: what it is about, the main content, "
          "author and date if any. If the page does not open or is gated, say so.",
}
_LOOKUP_BACK = {
    "ru": "{results}\n\nТеперь напиши то, что хочешь сказать в чат. "
          "Предыдущий твой текст в чат не ушёл — пиши ответ целиком.",
    "en": "{results}\n\nNow write what you want to say in the chat. "
          "Your previous text was not posted — write the reply whole.",
}
_LINKS_HEAD = {"ru": "Вот что по ссылкам:", "en": "Here is what the links hold:"}


@dataclass
class Trigger:
    kind: str                       # "addressed" | "conversation"
    reply_to: int | None = None     # the message to answer under, if one stands out


@dataclass
class Reply:
    """What he decided: words, a picture, where to put them — any may be empty."""

    text: str = ""
    reply_to: int | None = None
    image_path: Path | None = None
    notes: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)

    @property
    def speaks(self) -> bool:
        return bool(self.text) or self.image_path is not None


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


# ── The room as text ─────────────────────────────────────────────────────────


def _who(row: ChannelMessage, ai_name: str, lang: str) -> str:
    if row.is_self:
        return f"{ai_name} ({'ты' if lang == 'ru' else 'you'})"
    if row.is_owner:
        return f"{row.sender_name} ({'она' if lang == 'ru' else 'her'})"
    return row.sender_name or row.sender_id


def render_room(
    rows: list[ChannelMessage],
    *,
    ai_name: str,
    lang: str,
    with_dates: bool = False,
) -> str:
    """The stretch of the room as a transcript, with her and him marked.

    The marks are the whole point: in a list of first names she is one name
    among five, and the one thing he must not do here is fail to know her.

    A run of bare media from one person — eight photos sent as an album arrive
    as eight messages — is folded into one line. ``with_dates`` adds a day
    header whenever the date changes, for transcripts that span a night.
    """
    lines: list[str] = []
    day = None
    i = 0
    while i < len(rows):
        row = rows[i]
        if with_dates and row.created_at is not None:
            this_day = format_local(row.created_at, "%Y-%m-%d")
            if this_day != day:
                day = this_day
                lines.append(f"— {day} —")

        run = 1
        if _MEDIA_TOKEN_RE.match(row.text or "") and not row.reply_to_message_id:
            while (
                i + run < len(rows)
                and rows[i + run].sender_id == row.sender_id
                and rows[i + run].text == row.text
                and not rows[i + run].reply_to_message_id
            ):
                run += 1

        stamp = format_local(row.created_at, "%H:%M") if row.created_at else "--:--"
        prefix = f"[{stamp}] #{row.message_id} "
        if row.reply_to_message_id:
            prefix += f"↩#{row.reply_to_message_id} "
        text = f"{row.text} ×{run}" if run > 1 else row.text
        lines.append(f"{prefix}{_who(row, ai_name, lang)}: {text}")
        i += run
    return "\n".join(lines)


def render_transcript(
    rows: list[ChannelMessage],
    *,
    ai_name: str,
    lang: str,
    max_chars: int,
) -> tuple[str, int]:
    """The newest part of *rows* that fits in *max_chars*, and how many did not.

    Newest wins when something has to give: what was said last is what he is
    most likely to be asked about, and the rest is one ``[SEARCH_CHAT]`` away.
    """
    kept = list(rows)
    text = render_room(kept, ai_name=ai_name, lang=lang, with_dates=True)
    while len(text) > max_chars and len(kept) > 1:
        # Drop from the old end in chunks; re-rendering is cheap next to a model call.
        kept = kept[max(1, len(kept) // 10):]
        text = render_room(kept, ai_name=ai_name, lang=lang, with_dates=True)
    return text, len(rows) - len(kept)


def _her_name(recent: list[ChannelMessage], lang: str) -> str:
    for row in reversed(recent):
        if row.is_owner and row.sender_name:
            return row.sender_name
    return "(ещё не писала здесь)" if lang == "ru" else "(has not written here yet)"


# ── Memory ───────────────────────────────────────────────────────────────────


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


# ── What he can do besides talk ──────────────────────────────────────────────


def _take_notes(account_id: str, response: str, lang: str, already: list[str]) -> None:
    """File every ``[WRITE_NOTE]`` in *response* that has not been filed yet."""
    from infrastructure.autonomy import workbench
    from infrastructure.telegram import listener

    title = listener.room_title(account_id)
    for match in _NOTE_RE.finditer(response):
        note = match.group("text").strip()
        if not note or note in already:
            continue
        try:
            workbench.append_group_note(account_id, note, lang, room_title=title)
            already.append(note)
            logger.info("[telegram.responder:%s] noted: %s", account_id, note[:100])
        except Exception as exc:
            # He will have said "noted" to a person. If it did not happen, it
            # has to be loud here — he has no next step in the room to hear it.
            logger.error("[telegram.responder:%s] NOTE NOT SAVED (%s): %s", account_id, exc, note[:200])


async def _fetch(urls: list[str], *, api_key: str, account_id: str, lang: str) -> str:
    """Open each link through the research agent's web source."""
    from infrastructure.agents import Source, research

    parts: list[str] = []
    for url in urls[:MAX_FETCHES_PER_ROUND]:
        try:
            result = await research(
                task=_FETCH_TASK.get(lang, _FETCH_TASK["en"]).format(url=url),
                source=Source.WEB, api_key=api_key, account_id=account_id,
                lang=lang, max_attempts=1,
            )
            body = result.brief if result.found else (
                "(страница не открылась)" if lang == "ru" else "(the page did not open)"
            )
        except Exception as exc:
            logger.warning("[telegram.responder] FETCH_URL %s failed: %s", url, exc)
            body = f"({'ошибка' if lang == 'ru' else 'error'}: {exc})"
        parts.append(f"{url}\n{body}")
    return "\n\n".join(parts)


async def _search(queries: list[str], *, api_key: str, account_id: str, lang: str) -> str:
    """Run each query through the research agent, worded the way the skill words it.

    The sections come from the web-search skill's own ``prompt.md`` — what he
    reads after a search in the room is what he reads after one in the private
    chat, including the part that tells him not to recite the sources.
    """
    from infrastructure.agents import Source, research
    from infrastructure.skills.web_search.skill import _render_sources, skill as web_skill

    parts: list[str] = []
    for query in queries[:MAX_SEARCHES_PER_ROUND]:
        query = " ".join(query.split())
        try:
            result = await research(
                task=query, source=Source.WEB, api_key=api_key, account_id=account_id, lang=lang,
            )
        except Exception as exc:
            logger.warning("[telegram.responder] WEB_SEARCH %r failed: %s", query[:80], exc)
            parts.append(web_skill.get_section("web_empty", lang, web_query=query))
            continue
        if result.found:
            parts.append(web_skill.get_section(
                "web_continuation", lang, web_query=query, brief=result.brief,
                sources_block=_render_sources([c.to_dict() for c in result.citations]),
            ))
        else:
            parts.append(web_skill.get_section("web_empty", lang, web_query=query))
    return "\n\n".join(parts)


def _web_skill_description(lang: str) -> str:
    """The web-search skill's own description, word for word — see the image one."""
    from infrastructure.skills.web_search.skill import skill as web_skill

    return web_skill.prompt_fragment(lang).strip()


async def _generate_image(raw_match: re.Match, *, api_key: str, account_id: str, lang: str) -> Path | None:
    """Run the private chat's image skill and return the file it saved."""
    from infrastructure.paths import GENERATED_IMAGES_DIR
    from infrastructure.skills.base import SkillContext
    from infrastructure.skills.generate_image.skill import skill as image_skill

    ctx = SkillContext(
        db=None, client=make_llm_client(api_key), account_id=account_id, api_key=api_key,
        lang=lang, recent_pairs=[], current_user_text="", cutoff_days=2,
        logger=logger, dbg=lambda _msg: None,
    )
    try:
        result = await image_skill.execute(raw_match, ctx)
    except Exception as exc:
        logger.error("[telegram.responder:%s] image generation failed: %s", account_id, exc)
        return None
    for event, payload in result.sse_events:
        if event == "image_ready":
            path = GENERATED_IMAGES_DIR / Path(payload["path"]).name
            return path if path.exists() else None
    return None


def _image_skill_description(lang: str) -> str:
    """The image skill's own description, word for word.

    Read from the skill rather than retold here. A one-line retelling was the
    first version, and it dropped the part that matters most in a group: which
    model takes what. That table is what keeps a crude joke from being sent to
    a provider that answers crude jokes with an account warning.
    """
    from infrastructure.skills.generate_image.skill import skill as image_skill

    return image_skill.prompt_fragment(lang).strip()


def _clean(response: str) -> str:
    text = _ANY_CMD_RE.sub("", response)
    text = _UNCLOSED_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── 2. What does he say? ─────────────────────────────────────────────────────


async def compose(
    *,
    account_id: str,
    api_key: str,
    recent: list[ChannelMessage],
    new_rows: list[ChannelMessage],
    trigger: Trigger,
    bot_username: str,
) -> Reply:
    """Ask him what he wants to do in the room, and do the parts that are not talk.

    Notes are filed and links opened here; the words and the picture come back
    in the :class:`Reply` for the caller to post.
    """
    ai_name = get_ai_name()
    shown = recent[-ROOM_CONTEXT_MESSAGES:]
    lang = detect_lang("\n".join(row.text for row in shown))

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
        image_skill=_image_skill_description(lang),
        web_skill=_web_skill_description(lang),
        memories=memories or ("(ничего не всплыло)" if lang == "ru" else "(nothing surfaced)"),
        room=render_room(shown, ai_name=ai_name, lang=lang),
        her_name=_her_name(recent, lang),
        bot_username=bot_username or "?",
        why=_WHY.get(lang, _WHY["en"])[trigger.kind],
        **state,
    )

    reply = Reply(reply_to=trigger.reply_to, fact_ids=fact_ids)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    client = make_llm_client(api_key)
    response = ""

    for round_no in range(1, MAX_ROUNDS + 1):
        response, finish_reason = await client.complete(
            messages=messages, max_tokens=REPLY_MAX_TOKENS, temperature=0.7, return_meta=True,
        )
        response = (response or "").strip()
        if finish_reason == "length":
            # A clipped reply is not a reply. Better one missed line in a group
            # chat than half a sentence posted under his name. Whole notes that
            # made it out before the cut are still his, and are kept.
            logger.warning("[telegram.responder:%s] reply hit max_tokens — not sent", account_id)
            _take_notes(account_id, response, lang, reply.notes)
            return reply

        _take_notes(account_id, response, lang, reply.notes)

        urls = [m.group("url") for m in _FETCH_RE.finditer(response)]
        queries = [m.group("query").strip() for m in _SEARCH_RE.finditer(response)]
        if not (urls or queries) or round_no == MAX_ROUNDS:
            break

        found: list[str] = []
        if urls:
            logger.info("[telegram.responder:%s] opening %s", account_id, ", ".join(urls)[:200])
            pages = await _fetch(urls, api_key=api_key, account_id=account_id, lang=lang)
            found.append(f"{_LINKS_HEAD.get(lang, _LINKS_HEAD['en'])}\n\n{pages}")
        if queries:
            logger.info("[telegram.responder:%s] searching: %s", account_id, " | ".join(queries)[:200])
            found.append(await _search(queries, api_key=api_key, account_id=account_id, lang=lang))
        messages += [
            {"role": "assistant", "content": response},
            {"role": "user", "content": _LOOKUP_BACK.get(lang, _LOOKUP_BACK["en"]).format(
                results="\n\n".join(found),
            )},
        ]

    target = _REPLY_TO_RE.search(response)
    if target:
        wanted = int(target.group("id"))
        if any(row.message_id == wanted for row in recent):
            reply.reply_to = wanted

    image = _IMAGE_RE.search(response)
    if image:
        reply.image_path = await _generate_image(image, api_key=api_key, account_id=account_id, lang=lang)

    text = _clean(response)
    if text.upper().strip(".!") != SILENT:
        reply.text = text
    if not reply.speaks:
        logger.info(
            "[telegram.responder:%s] chose silence (%s), notes=%d",
            account_id, trigger.kind, len(reply.notes),
        )
    return reply


# ── 3. Send and remember ─────────────────────────────────────────────────────


def own_row(sent: dict, *, account_id: str, chat_id: str, bot_id: int | str, ai_name: str) -> ChannelMessage:
    """His message as the room will have it, from what Telegram sent back."""
    stamp = sent.get("date")
    text = sent.get("text") or ""
    if sent.get("photo"):
        caption = sent.get("caption") or ""
        text = f"[photo] {caption}".strip()
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
        text=text,
        created_at=datetime.fromtimestamp(int(stamp), tz=timezone.utc) if stamp else datetime.now(timezone.utc),
    )


async def post(client, chat_id: str, reply: Reply) -> list[dict]:
    """Put the reply into the room; returns what Telegram says was sent.

    A picture carries the words as its caption when they fit; when they do
    not, the words go first and the picture follows.
    """
    from infrastructure.telegram.client import CAPTION_MAX_CHARS

    sent: list[dict] = []
    if reply.image_path is None:
        sent.append(await client.send_message(chat_id, reply.text, reply_to_message_id=reply.reply_to))
        return sent

    if reply.text and len(reply.text) > CAPTION_MAX_CHARS:
        sent.append(await client.send_message(chat_id, reply.text, reply_to_message_id=reply.reply_to))
        sent.append(await client.send_photo(chat_id, reply.image_path))
    else:
        sent.append(await client.send_photo(
            chat_id, reply.image_path, caption=reply.text, reply_to_message_id=reply.reply_to,
        ))
    return sent


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

    reply = await compose(
        account_id=account_id, api_key=api_key, recent=recent, new_rows=new_rows,
        trigger=trigger, bot_username=bot_username,
    )
    if not reply.speaks:
        return None

    client = get_client()
    if client is None:
        return None
    sent = await post(client, chat_id, reply)
    logger.info("[telegram.responder:%s] said: %s", account_id, (reply.text or "[photo]")[:100])

    rows = [
        own_row(item, account_id=account_id, chat_id=chat_id, bot_id=bot.get("id", ""), ai_name=ai_name)
        for item in sent
    ]
    try:
        await asyncio.get_running_loop().run_in_executor(None, listener.fill_embeddings, rows)
        async with get_db_session() as db:
            await ChannelRepository(db).save_many(rows)
    except Exception as exc:
        # The room has the message; only our copy is missing. Say so — the
        # next transcript would otherwise show a reply to nothing.
        logger.error("[telegram.responder:%s] sent but could not store own row: %s", account_id, exc)

    await asyncio.get_running_loop().run_in_executor(None, _mark_used, reply.fact_ids)
    return reply.text or "[photo]"
