# The Group Chat — How It Works

This document describes how the AI takes part in a Telegram group with the
user and their friends, based on the actual code. It is the first room he is
in with more than two people, and everything here is shaped by one rule: the
room must not outweigh the two of them.

---

## Setup

Three settings, all on the desktop Settings page under *Group Chat (Telegram)*:

| Setting | What it is |
|---|---|
| `telegram_bot_token` | A bot from @BotFather. Add the bot to the group and disable its privacy mode (`/setprivacy` → Disable) so it receives every message, not only mentions. |
| `telegram_chat_id` | The one group he reads. Picked from the rooms the bot has been spoken to in. |
| `telegram_owner_user_id` | The user's own Telegram account — how he tells her apart from everyone else. Picked from the people seen in the group. |

`PUT /api/settings/telegram/verify` asks Telegram who the token belongs to;
`GET /api/settings/telegram/status` returns the rooms and people seen so far.

Until a group is chosen, nothing is stored — only the list of rooms the bot
has seen, so one can be picked.

---

## Storage — a room, not pairs

Group messages live in their own table, `channel_messages`, one row per message
including his own. They are **not** in `messages`: that table is pairs between
two people, and every reader of it — chat history, reflection timing, the
post-dialogue journal — assumes that shape.

| Column | Meaning |
|---|---|
| `chat_id`, `message_id` | Telegram's identity of the message; unique together, so a replayed poll changes nothing |
| `sender_id`, `sender_name` | Who wrote it |
| `is_owner` | Her |
| `is_self` | Him |
| `reply_to_message_id` | What it answers, if anything |
| `embedding` | vector(384), same space as `messages`, for `SEARCH_CHAT` |

Consequences that fall out of the separation:

- The reflection cooldown is measured from **her** messages in the private
  chat only. The group never wakes him and never delays a waking.
- The last N pairs shown in chat and at a waking are the private dialogue only.

---

## Listening

`infrastructure/telegram/listener.py`, driven by the `telegram` worker in
`main.py`. One tick is one long poll (`getUpdates`, 25 s), which returns on its
own when something arrives. The polling cursor and the rooms seen are kept in
`data/autonomy/{account}/telegram.json`, so a restart does not replay the night.

Media without text is stored as a token (`[photo]`, `[voice message]`, …);
service messages are dropped.

---

## When he speaks

`infrastructure/telegram/responder.py`, called after every poll that stored
new rows. Three ways the room becomes his to answer:

1. **Addressed** — his name or his handle in a line, or a reply to one of his
   messages. He answers under that line.
2. **In conversation** — he spoke in the room within the last
   `CONVERSATION_WINDOW_MINUTES` (10) and people are still talking. The next
   lines may be for him without his name on them.
3. **His own initiative** — only from reflection, with `[SEND_TO_CHAT: text]`.
   There is no budget and no timer: he writes when a waking makes him want to.

Anything else is the room talking among itself: stored, not answered, seen at
his next waking.

A decision to speak is one model call on `infrastructure/telegram/prompts/group_reply.md`.
He may answer `SILENT`, which is a decision, not a failure. A reply cut off by
the token budget is never posted.

### What he knows in the room

`Consumer.TELEGRAM` in the context registry (`infrastructure/autonomy/context.py`):

| Section | In the room? | Why |
|---|---|---|
| identity (all pillars, incl. canon) | yes | who she is and who he is are the two things he must not lose in a crowd |
| workbench | last 2 entries | where the two of them are today, and no more |
| open_threads | **no** | the board is the two of them; he is in public |
| memory (Chroma facts) | yes | recalled from the lines that pulled him in |
| last 30 messages of the room | yes | her lines marked *(она)*, his *(ты)* |

The prompt says out loud that the room is shared and that what is between the
two of them stays between them by default.

---

## What he learns at a waking

The awakening prompt carries a `<group_chat>` block: how many messages since
his last waking, and the last 12 of them with the same marks. He can read
further with `[SEARCH_CHAT: query]` — a research-agent source over
`channel_messages` (pgvector by default, substring match when no embedding
model is loaded), each hit shown with its neighbours.

---

## Keeping the room from outweighing them

Where the group could leak into his long-term self, and what stops it:

| Store | Risk | What happens instead |
|---|---|---|
| workbench | a note after every reply drowns the desk | nothing writes to the desk from the room automatically. He notes what matters himself, at a waking, if he chooses |
| open threads | pins about friends fill the board | the room prompt has no commands; pinning is reflection's alone |
| Chroma facts | friends' facts surface in the private chat | the room prompt has no `SAVE_MEMORY`; facts still come only from the private dialogue and the rotator |
| identity | friends seep into "Who she is" / "Our story" | a seventh section, **Мои люди / My people**, is where the friends belong. The rotator's consolidation and canon-promotion prompts know it; the five pillars that are theirs stay theirs |
| reflection timing | a busy room keeps him awake | the group is not a message from her; only she moves the clock |

---

## Key files

| File | Role |
|---|---|
| `infrastructure/telegram/client.py` | Bot API on aiohttp: `getMe`, `getUpdates`, `sendMessage` |
| `infrastructure/telegram/listener.py` | poll → rows, the cursor, the rooms seen |
| `infrastructure/telegram/responder.py` | addressed / in conversation, the reply, his own row |
| `infrastructure/telegram/prompts/group_reply.md` | who he is in the room |
| `infrastructure/database/models/channel_message.py` | the table |
| `infrastructure/database/repositories/channel_repo.py` | reads and writes |
| `infrastructure/autonomy/helpers.py` — `send_to_chat` | `[SEND_TO_CHAT]` from reflection |
| `infrastructure/autonomy/reflection_engine.py` — `_build_group_chat_block` | the `<group_chat>` block at a waking |
| `infrastructure/agents/sources.py` — `probe_chat` | `[SEARCH_CHAT]` |
| `api/settings_api.py` — `/telegram/status`, `/telegram/verify` | the settings page's picker |
