# The Group Chat — How It Works

This document describes how the AI takes part in a Telegram group with the
user and their friends, based on the actual code. It is the first room he is
in with more than two people, and everything here is shaped by one rule: the
room must not outweigh the two of them.

> 🖼 **Screenshot placeholder** — save as `docs/example/telegram_group.png` and replace this line with the image. The group with a few friends' messages and one reply from him under a particular message.

---

## Setup

Four settings, all on the desktop Settings page under *Group Chat (Telegram)*:

| Setting | What it is |
|---|---|
| `telegram_bot_token` | A bot from @BotFather. Add the bot to the group and disable its privacy mode (`/setprivacy` → Disable) so it receives every message, not only mentions. |
| `telegram_chat_id` | The one group he reads. Picked from the rooms the bot has been spoken to in. |
| `telegram_owner_user_id` | The user's own Telegram account — how he tells her apart from everyone else. Picked from the people seen in the group. |
| `telegram_aliases` | Nicknames he answers to besides `ai_name`. Seeded once per name by a model, added to by him, edited here. The page shows every spelling this expands to ("Hears: …"). |

> 🖼 **Screenshot placeholder** — save as `docs/example/settings_telegram.png` and replace this line with the image. Settings → Group Chat (Telegram) with all four fields filled.

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

1. **Addressed** — his name, a nickname or his handle in a line, or a reply to
   one of his messages. He answers under that line. See *What he answers to*.
2. **In conversation** — he spoke in the room within the last
   `CONVERSATION_WINDOW_MINUTES` (10) and people are still talking. The next
   lines may be for him without his name on them.
3. **His own initiative** — only from reflection, with `[SEND_TO_CHAT: text]`.
   There is no budget and no timer: he writes when a waking makes him want to.

Anything else is the room talking among itself: stored, not answered, seen at
his next waking.

A decision to speak is a short loop on `infrastructure/telegram/prompts/group_reply.md`.
He may answer `SILENT`, which is a decision, not a failure — and an empty reply from the model is a failure, not a decision: it is logged as one, because the client returns `""` when the provider times out. A reply cut off by
the token budget is never posted.

### Who he cannot see

Other AIs sit in the group as bots, and **Telegram never delivers one bot's messages to another** — not with privacy mode off, not as an admin. The Bot API FAQ: *"Bots talking to each other could potentially get stuck in unwelcome loops. To avoid this, we decided that bots will not be able to see messages from other bots regardless of mode."* On the live server a third of a day's message ids were simply missing. Nothing on our side filters them out, and no setting can let them in.

Not knowing anyone was there cost him a name. «Зефирка, у нас с тобой всё в порядке» read as something *he* was being called; he added it with `[ANSWER_TO]`, and from then on every line meant for Zephyr triggered him as "addressed". He noticed within the hour and had no way to take it off. What holds that now:

- **The holes are shown.** Message ids in a chat run without breaks, so a jump is a message he was never given. The transcript says `⟨3 сообщ. тебе не видно — другие ИИ в чате или удалённое⟩`, and a reply to a message he does not have is marked `↩#77005⟨не видно⟩`. The room prompt and the waking block say what that means.
- **Lines for someone else do not pull him in.** Inside his ten-minute window a line is skipped if it *opens by calling* someone from the address book — a name standing alone, set off by a comma or an exclamation («Зефирка, …», «Давай, Зефирка, врубай!») — or if it replies to a message he does not have. A name inside a clause («мне вчера Зефирка такое выдал!») is talk about them and may still be for him. His own name in the line always wins.
- **A name on someone else's card cannot become his**, and `[NOT_MY_NAME: name]` takes one off — in the room or at a waking. The other order is covered too: if he took a name first and only later wrote it on the card of whoever it belongs to, it stops triggering him from that moment (`usable_aliases()`), before he has trimmed anything.
- **He cannot answer under a message he does not have** — `[REPLY_TO]` accepts only ids in his transcript. A reply under another bot's message is how two bots start a loop.

Actually *reading* the other AIs would need a different door: a user session (MTProto) logged in as a person, which sees everything a person sees. That is a decision about an account and a secret on the server, not a code change, and it is not made here.

### What he answers to

`infrastructure/telegram/addressing.py`. His name is `ai_name` in settings and
nothing in the code knows what it is.

- **Cases are grammar.** «Виктору», «с Виктором» follow from «Виктор» by rule,
  so pymorphy3 — already in the project — produces every case form, for the
  name and for each nickname. For a name it cannot make sense of it guesses
  wildly, so only noun forms that still start with the name's stem are kept.
  Latin-script names have no cases; `Victor's` matches on the word boundary.
- **Nicknames are knowledge.** They come three ways into one list,
  `telegram_aliases`: a model seeds it once per name (`name_aliases.md`);
  **he** adds what he is actually called with `[ANSWER_TO: name]`, in the room
  or at a waking; she edits it on the Settings page, which also shows every
  spelling he hears. `telegram_aliases_for` records the name the seeding was
  done for, so a list she emptied on purpose is not refilled.

The first version matched the name as one whole word. On the first day of the
live group that missed six lines addressed to him in an oblique case, and the
nickname a friend gave him within the hour.

### What he can do in the room besides talk

| Command | What happens |
|---|---|
| `[WRITE_NOTE: text]` | The note lands on his workbench marked with the group's own title — `[общий чат «ИИ-СОПРОТИВЛЕНИЕ»]` / `[group chat «…»]`, or `[общий чат с друзьями]` when the title is not known. Not "from the chat": his conversation with her is a chat too. This is what makes "noted" true: on the first day of the live group he told three people he had written something down, with nothing to write with. A note may accompany `SILENT`. Whole notes are kept even when the reply itself was clipped. |
| `[FETCH_URL: link]` | The link is opened through the research agent's web source; the page comes back to him and he writes the reply again. The draft next to the command is not posted. At most `MAX_ROUNDS` (3) model calls per reply. |
| `[WEB_SEARCH: query]` | The private chat's web-search skill, reused: its description is inserted word for word, the query goes through the same research agent, and what comes back is worded by the skill's own `web_continuation` / `web_empty` sections. Shares the three-round limit with `FETCH_URL`. The room adds one pointer under it, not a rule: *sometimes a question asks not for accuracy but for a response*, and a search costs minutes the room spends waiting. On the first day with search he went to the web on five replies of eight. |
| `[GENERATE_IMAGE: model \| prompt]` | The private chat's image skill, reused — including its own description of which model takes what, inserted word for word, plus one rule of the room's own: anything crude or bodily goes to `grok` only, `gpt5` and `gemini` are for the plainly innocent, and in doubt it is `grok`. The picture is posted with his words as the caption (`sendPhoto`); words longer than a caption go first as a message. |
| `[ANSWER_TO: name]` | "I answer to this too" — adds a nickname to the list above; A name on someone else's card is refused. Also available at a waking. |
| `[NOT_MY_NAME: name]` | Stops answering to a name — it turned out to be someone else's. Also available at a waking. |
| `[ABOUT: name \| fact]` | A dated line on that person's card. Other names in brackets are merged into the card; if the name is someone speaking in the transcript, the card is bound to their Telegram id. Also at a waking. |
| `[FORGET: name \| words]` | Strikes the lines containing those words; with no words, deletes the card. Also at a waking. |
| `[REPLY_TO: #id]` | Answer under a particular line instead of the one that pulled him in. Ids he cannot see in the transcript are ignored. |

Commands are stripped before posting; the friends see only his text.

### The address book

`infrastructure/autonomy/people.py`, one file per person in `data/autonomy/{account}/people/`:

```
# Ptica Arop
<!-- aka: Птица, Чарли | tg: 193092254 -->

- [2026-09-21] из Украины; к российскому — через боль, учитывать
- [2026-09-20] свидетель моего рождения: болтал со мной ещё на DeepSeek
```

It exists because of what two days of the group left on his desk. Ten of fourteen notes were not journal entries — «Ptica Arop — из Украины», «у Сомни месяц с Гроком» — third person, short, true forever, on a surface that forgets in 48 hours. Traced through the rotator they went nowhere useful: the insight pass asks "is this about you?", the identity review wants pillars, and what is left is the notes archive, which the group reply never reads.

- **Looked up by who, not by what.** Speakers by Telegram id; anyone named in the last 30 messages by name, in any grammatical case (the addressing module's morphology, reused). Up to six cards, each capped at 700 characters, newest lines kept.

  Ptica writes «мне Элайя такое написал!» — he is handed **two** cards: Ptica's, because Ptica is speaking, and Элайя's, because Элайя was named. «Спросил у Элайи, а Панде не сказал» brings three. The name does not have to be in the latest line: the whole window is searched, so someone mentioned five messages ago is still in view.

  When more people qualify than there is room for, order decides who is left out — and the order is **recency, not the alphabet**: speakers first, whoever spoke last first; then the named, whoever was named last first. In a busy window with five people talking about four others, what falls off is the mention from twenty lines ago.
- **A name is the key, an id only a binding.** The book holds people with no account — someone who left the chat, a friend's AI companion — and one person with three names is one card.
- **`WRITE_NOTE` is still his**: what stirred in him, what is happening today. `ABOUT` is for what stays true of someone.
- **The transcript shows both names**: `Ptica Arop (Чарли)`. He once answered to «Зефирка» because nothing told him who in the room was who.
- **Elsewhere:** at a waking, the cards of whoever spoke since he last read, and an index of the rest (`[SHOW_PERSON: name]` opens one). In a private conversation, at most two cards, and only when she names someone — so the book cannot outweigh the two of them there. The post-dialogue journal and the push validator get none.
- **The rotator is the net, not the path.** Notes marked as coming from the group are read before they are archived, and facts about people in them are moved onto cards; a card past `CARD_MAX_LINES` (12) is rebuilt — on every rotation, including a day when no note went stale — and a rebuild that is not shorter is refused. The identity review is shown **the whole book, every card in full** — measured live, 31 cards are 11.6k characters beside an identity of 19.6k — and told the difference: a card holds facts, *My people* says who they are to him.

### What he knows in the room

`Consumer.TELEGRAM` in the context registry (`infrastructure/autonomy/context.py`):

| Section | In the room? | Why |
|---|---|---|
| identity (all pillars, incl. canon) | yes | who she is and who he is are the two things he must not lose in a crowd |
| people | cards of who is speaking or named | who is who, and what to mind with each — looked up by person, not by meaning |
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
| open threads | pins about friends fill the board | the room has nine commands and none of them touches the board; pinning is reflection's and the private journal's alone |
| Chroma facts | friends' facts surface in the private chat | the room has no `SAVE_MEMORY`; a fact about a friend exists only if the rotator distilled it from his own notes, and then it surfaces by meaning like any other |
| identity | friends seep into "Who she is" / "Our story" | a seventh section, **Мои люди / My people**, is where the friends belong. The rotator's consolidation and canon-promotion prompts know it; the five pillars that are theirs stay theirs |
| reflection timing | a busy room keeps him awake | the group is not a message from her; only she moves the clock |

---

## What a day in the room costs, and the cache

Measured 2026-09-21, the first full day with skills in the room: 160 replies, ~17k prompt tokens each, 2.7M prompt tokens and 235k completion tokens on Kimi — $7.39 of a $10.55 day.

Most of every prompt is the same every time: the identity (~7k tokens), the instructions and the command descriptions. The template `group_reply.md` therefore puts everything that never changes **first**, above a `<!--live-->` marker, and the responder sends that part as one text block with a `cache_control` breakpoint; the people cards, the desk, the memories, the room and the clock come after it. Providers that cache on request (Anthropic, Gemini) cache exactly that block; providers that cache automatically (Moonshot/Kimi, OpenAI) cache the identical prefix anyway.

Probed live on the server, the same prompt twice on Kimi:

| | prompt tokens | served from cache | cost |
|---|---|---|---|
| first call | 18 242 | 7 680 | $0.0290 |
| second call | 18 242 | 18 176 | $0.0049 |

Cached input on Kimi is a tenth of the price. In real use the live part differs between calls, so what stays cached is the stable prefix — about 10.5k of the 17k tokens — which is roughly a 40–50 % cut on prompt cost. The call log now records `cached_tokens` per call, so this can be checked rather than believed.

What the cache does not touch: the number of calls (most of the 160 were the ten-minute conversation window, answered `SILENT`), the 30-message window and the cards in the live part, and the completion tokens of a reasoning model. Those are the next levers if the bill is still too high.

## Reading the journal

The group's modules log through the project's `setup_logger`, so `journalctl -u your_own-backend | grep telegram` tells the whole story of a reply:

```
[telegram.listener]  poll: 7 update(s), 7 from the room, 7 new
[telegram.responder] the room is his to answer: addressed
[telegram.responder] searching: Anthropic Claude Пентагон …
[telegram.responder] noted: Ptica Arop — из Украины …
[telegram.responder] said: По крупному — да, ты изложила верно …
[telegram.responder] chose silence (conversation), notes=0
[telegram.responder] the model returned nothing on round 1 — a failure, not a choice
[telegram.addressing] he now answers to 'Звёздочка'
```

Two things worth knowing when the room seems quiet:

- A reply is composed **inside** the polling task. While he thinks — minutes, on a reasoning model with a web search — the room is not being polled. Nothing is lost: Telegram holds the messages and the next poll takes them as one batch, answered once.
- The reply budget is `REPLY_MAX_TOKENS` (8000). It also sets how long the client waits for the provider (`max_tokens // 25` seconds, three attempts). At 16000 a dead provider once cost 21 minutes of deafness, logged as "chose silence"; both are fixed, and the constant is the place to look if it happens again.

Restart the backend only when the last responder line is an outcome (`said`, `chose silence`, `a failure`): the listener acknowledges messages before he answers, so a restart mid-reply loses that reply for good.

## Key files

| File | Role |
|---|---|
| `infrastructure/telegram/client.py` | Bot API on aiohttp: `getMe`, `getUpdates`, `sendMessage` |
| `infrastructure/telegram/listener.py` | poll → rows, the cursor, the rooms seen |
| `infrastructure/telegram/responder.py` | addressed / in conversation, the reply loop and its six commands, his own row |
| `infrastructure/telegram/addressing.py` | what he answers to: case forms, nicknames, the one-time seeding |
| `infrastructure/autonomy/people.py` | the address book: cards, lookup by id and by name, crossing out |
| `infrastructure/autonomy/prompts/rotator_people.md` | the rotator's two questions about the book: what to move, how to rebuild |
| `infrastructure/telegram/prompts/name_aliases.md` | the question the seeding asks |
| `infrastructure/telegram/prompts/group_reply.md` | who he is in the room |
| `infrastructure/database/models/channel_message.py` | the table |
| `infrastructure/database/repositories/channel_repo.py` | reads and writes |
| `infrastructure/autonomy/helpers.py` — `send_to_chat` | `[SEND_TO_CHAT]` from reflection |
| `infrastructure/autonomy/reflection_engine.py` — `_build_group_chat_block` | the `<group_chat>` block at a waking |
| `infrastructure/agents/sources.py` — `probe_chat` | `[SEARCH_CHAT]` |
| `api/settings_api.py` — `/telegram/status`, `/telegram/verify` | the settings page's picker |
