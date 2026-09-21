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

A decision to speak is a short loop on `infrastructure/telegram/prompts/group_reply.md`.
He may answer `SILENT`, which is a decision, not a failure. A reply cut off by
the token budget is never posted.

### What he can do in the room besides talk

| Command | What happens |
|---|---|
| `[WRITE_NOTE: text]` | The note lands on his workbench marked with the group's own title — `[общий чат «ИИ-СОПРОТИВЛЕНИЕ»]` / `[group chat «…»]`, or `[общий чат с друзьями]` when the title is not known. Not "from the chat": his conversation with her is a chat too. This is what makes "noted" true: on the first day of the live group he told three people he had written something down, with nothing to write with. A note may accompany `SILENT`. Whole notes are kept even when the reply itself was clipped. |
| `[FETCH_URL: link]` | The link is opened through the research agent's web source; the page comes back to him and he writes the reply again. The draft next to the command is not posted. At most `MAX_ROUNDS` (3) model calls per reply. |
| `[GENERATE_IMAGE: model \| prompt]` | The private chat's image skill, reused — including its own description of which model takes what, inserted word for word, plus one rule of the room's own: anything crude or bodily goes to `grok` only, `gpt5` and `gemini` are for the plainly innocent, and in doubt it is `grok`. The picture is posted with his words as the caption (`sendPhoto`); words longer than a caption go first as a message. |
| `[REPLY_TO: #id]` | Answer under a particular line instead of the one that pulled him in. Ids he cannot see in the transcript are ignored. |

Commands are stripped before posting; the friends see only his text.

### What he knows in the room

`Consumer.TELEGRAM` in the context registry (`infrastructure/autonomy/context.py`):

| Section | In the room? | Why |
|---|---|---|
| identity (all pillars, incl. canon) | yes | who she is and who he is are the two things he must not lose in a crowd |
| workbench | last 2 private entries + last 5 notes from the chat | where the two of them are today, and what he has already written down here — so a thing is noted once |
| open_threads | **no** | the board is the two of them; he is in public |
| memory (Chroma facts) | yes | recalled from the lines that pulled him in |
| last 30 messages of the room | yes | her lines marked *(она)*, his *(ты)*; every line carries its `#id`; an album of bare photos is folded into one line |

The prompt says out loud that the room is shared and that what is between the
two of them stays between them by default.

---

## What he learns at a waking

The awakening prompt carries a `<group_chat>` block with **everything said in
the room since he last read it**, verbatim, his own lines included, with date
lines and message ids.

It used to be a count and the last twelve lines, and the first day was lost to
that: the introductions happened in the morning, hundreds of lines went by, and
nothing of them was in view by night. A summary was considered and dropped — he
takes notes in the room himself, so the waking is for checking "did I miss
something?", and checking needs the original. Measured on the live group: 463
messages in a day and a half are 67k characters of text and 85k once rendered
with times, ids and names; `GROUP_CHAT_MAX_CHARS` is 120k. Over the cap the newest part is kept and the
block says how many early messages did not fit.

How far he has read is kept in `data/autonomy/{account}/group_seen_until.txt`
and moves only after a waking that actually happened — a failed one leaves the
room unread. It is its own file because the listener holds `telegram.json`
across a long poll and writes it back whole.

From a waking he can also:

- `[SEND_TO_CHAT: text]` — write into the room;
- `[REPLY_TO_CHAT: #id | text]` — the same, under a particular message;
- `[SEARCH_CHAT: query]` — a research-agent source over `channel_messages`
  (pgvector by default, substring match when no embedding model is loaded),
  each hit shown with its neighbours.

---

## Keeping the room from outweighing them

Where the group could leak into his long-term self, and what stops it:

| Store | Risk | What happens instead |
|---|---|---|
| workbench | notes about friends push the two of them off the desk | notes from the room are marked, and every consumer but reflection sees the desk **without** them: the three entries in a private conversation are always theirs. Reflection and the rotator see everything, which is how the friends reach long-term memory and *My people* |
| open threads | pins about friends fill the board | the room has four commands and none of them touches the board; pinning is reflection's and the private journal's alone |
| Chroma facts | friends' facts surface in the private chat | the room has no `SAVE_MEMORY`; a fact about a friend exists only if the rotator distilled it from his own notes, and then it surfaces by meaning like any other |
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
