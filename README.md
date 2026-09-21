# Your Own

Bring your chats, keep the continuity, and make your AI truly yours.

Your Own is a local-first AI workspace for building persistent, personal intelligence on your own terms.
It can be a companion, a work partner, a memory system, an autonomous agent, a creative collaborator — or something that does not fit any pre-approved category.

Import your history, keep what matters, and shape an AI that remembers, acts, and grows with you — not one flattened into a sanitized chatbot.

**What it can do, in one breath:** talk with you in a streaming chat with images, PDFs, text files, audio and video; remember — facts, a journal, a board of open threads, a self-model it rewrites itself; act — search the web and your shared history, draw pictures, schedule messages; wake up on its own, think, and write to you first; and sit in a **Telegram group with your friends** as itself, knowing who you are among them.

<table>
<tr>
<td width="50%" align="center">
<img src="docs/example/loading_screen.png" alt="Loading screen" /><br>
<sub>One-click launch with progress</sub>
</td>
<td width="50%" align="center">
<img src="docs/example/dashboard.png" alt="Dashboard" /><br>
<sub>Dashboard</sub>
</td>
</tr>
<tr>
<td width="50%" align="center">
<img src="docs/example/image_generation.png" alt="Image generation" /><br>
<sub>Inline image generation (GPT-5 / Gemini)</sub>
</td>
<td width="50%" align="center">
<img src="docs/example/skills.png" alt="Skills" /><br>
<sub>Skills — agentic pipeline</sub>
</td>
</tr>
<tr>
<td width="50%" align="center">
<img src="docs/example/memory.png" alt="Memory facts" /><br>
<sub>Saved facts — ChromaDB memory</sub>
</td>
<td width="50%" align="center">
<img src="docs/example/settings.png" alt="Settings" /><br>
<sub>Settings</sub>
</td>
</tr>
<tr>
<td width="50%" align="center">
<img src="docs/example/chat.png" alt="Chat" /><br>
<sub>Chat — streaming, memory recall, skills</sub>
</td>
<td width="50%" align="center">
<img src="docs/example/export_chatgpt_data.png" alt="ChatGPT export" /><br>
<sub>ChatGPT export import flow</sub>
</td>
</tr>
</table>

---

## Quick Start

### Requirements


- **Python 3.11+**
- **Node.js 18+** (includes npm)
- **PostgreSQL 15+** with `pgvector` extension

### Desktop (one-click)

```bash3
cd frontend
npm run electron:dev
```

On first run, the setup script automatically:

1. Detects or installs PostgreSQL
2. Creates the local `your_own` database
3. Writes `.env` from `.env.example` if needed
4. Installs frontend and Python dependencies
5. Enables `pgvector` extension
6. Runs Alembic migrations
7. Starts the backend, frontend, and Electron shell

### Running it on a server

The same backend runs on a laptop or on a small VPS (the reference install is 4 cores / 8 GB, PostgreSQL 17 + pgvector, Caddy for TLS). On a server use `next build` + `next start`, never `next dev`.

A **packaged desktop app is a thin client**: it starts nothing of its own and simply opens your server. Point it with an environment variable:

```bash
YOUR_OWN_SERVER_URL=https://your-domain.example  # read by frontend/electron/main.js
```

In development (`npm run electron:dev`) everything still runs locally on `localhost:3000` / `:8000`. Notes from a real move — sizes, memory, the disk trap, and how to ship an update — are in [docs/MIGRATION.md](docs/MIGRATION.md).

### Mobile App (Android)

The mobile app is a standalone Android application that connects to your backend over the network. You do **not** need to run it alongside the desktop client — it works independently, from anywhere.

<table>
<tr>
<td width="33.33%" align="center">
<img src="docs/example/dashboard_mobile.jpg" alt="Dashboard mobile" width="240" /><br>
<sub>Dashboard mobile</sub>
</td>
<td width="33.33%" align="center">
<img src="docs/example/chat_mobile.jpg" alt="Chat mobile" width="240" /><br>
<sub>Chat mobile</sub>
</td>
<td width="33.33%" align="center">
<img src="docs/example/settings_mobile.jpg" alt="Settings mobile" width="240" /><br>
<sub>Settings mobile</sub>
</td>
</tr>
</table>

#### Option A — Install a pre-built APK

If you already have a `.apk` file (from an EAS build or a release):

1. Transfer the `.apk` to your phone (email, Google Drive, USB, Telegram — any way works)
2. Open the file on your phone
3. Android will ask to allow installing from this source — tap **Allow**
4. Tap **Install**
5. Open the app, enter your backend URL and auth token in Settings, tap **Connect**

#### Option B — Build it yourself

You'll need an [Expo](https://expo.dev) account (free).

```bash
# 1. Install the EAS CLI (once)
npm install -g eas-cli

# 2. Log in to your Expo account
eas login

# 3. Go to the mobile folder
cd mobile

# 4. Install dependencies
npm install --legacy-peer-deps

# 5. Build the APK (takes ~10 minutes, runs in the cloud)
eas build -p android --profile preview
```

When the build finishes, EAS gives you a download link. Transfer the `.apk` to your phone and install it (see Option A step 2).

> **Tip:** You don't need Android Studio. EAS builds in the cloud — all you need is a terminal and an Expo account.

#### Connecting the app to your backend

Your phone needs to reach the backend over the network. There are two common setups:

**Same Wi-Fi (local network):**
- Find your computer's local IP: `ipconfig` (Windows) or `ifconfig` (Mac/Linux)
- In the app: Settings → Server URL → `http://192.168.x.x:8000`
- Paste the auth token from `data/auth_token.txt`

**From anywhere (public URL via ngrok):**
- Start an ngrok tunnel: `ngrok http 8000`
- In the app: Settings → Server URL → `https://your-name.ngrok-free.dev`
- Paste the auth token

#### Push notifications

To receive push notifications when the AI reaches out to you:

1. Create a free account at [pushy.me](https://pushy.me)
2. Create an app in the Pushy dashboard, copy the **Secret API Key**
3. In the mobile app: Settings → Pushy Secret API Key → paste it, tap **Save**
4. The device token registers automatically — you'll see it in Settings
5. That's it. The AI will now send you push notifications when it reflects or has something to say

### Default Ports

| Service    | Port   |
|------------|--------|
| Frontend   | `3000` |
| Backend    | `8000` |
| PostgreSQL | `5432` |

### Authentication

On first run, the backend generates a random auth token and saves it to `data/auth_token.txt`. All API requests require this token in the `Authorization: Bearer <token>` header.

**Where to find the token:**

- In the backend console on startup: `[startup] Auth token: xxxxxxx`
- In the file: `data/auth_token.txt`

On the local machine (Electron), the token is passed to the app over IPC — no manual setup needed. There is deliberately no HTTP endpoint that hands out the token: anything that could reach the port could ask for it, and on a machine with a local proxy that included things outside your network. On remote devices (phone, another laptop), enter it once in **Settings → Server Connection → Auth Token**.

### Remote Access

Access the app from your phone or another computer via a tunnel service (ngrok, Tailscale, Cloudflare Tunnel, etc.).

**Option A — ngrok (recommended, public HTTPS URL):**

1. Install [ngrok](https://ngrok.com/) and authenticate: `ngrok config add-authtoken <YOUR_TOKEN>`
2. Register two free/paid domains in the [ngrok dashboard](https://dashboard.ngrok.com/domains)
3. Create `ngrok.yml` (or edit `~/.config/ngrok/ngrok.yml`):
   ```yaml
   tunnels:
     backend:
       addr: 8000
       proto: http
       domain: your-backend-domain.ngrok-free.dev
     frontend:
       addr: 3000
       proto: http
       domain: your-frontend-domain.ngrok-free.dev
   ```
4. Start tunnels: `ngrok start --all`
5. On the remote device, open the **frontend** domain in a browser
6. In **Settings → Server Connection**, set:
   - **Server URL** → `https://your-backend-domain.ngrok-free.dev`
   - **Auth Token** → paste from `data/auth_token.txt`
7. Click **Connect**

API requests from the phone go through a built-in Next.js proxy (`/api/*` → backend), so there are no CORS issues.

**Option B — Tailscale (private mesh VPN):**

1. Install [Tailscale](https://tailscale.com/download) on the server and sign in
2. Install Tailscale on your phone/laptop (same account)
3. Run `tailscale ip` on the server — note the `100.x.x.x` address
4. Open `http://100.x.x.x:3000` on the remote device
5. Set **Server URL** → `http://100.x.x.x:8000` and paste the auth token

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Server (always-on laptop / Mini PC)                     │
│                                                          │
│  FastAPI backend (0.0.0.0:8000)                          │
│  ├── Agentic pipeline (skills, image gen)                │
│  ├── Memory retrieval (pgvector + ChromaDB)              │
│  ├── Context registry — who sees which state block       │
│  ├── Autonomy engine                                     │
│  │   ├── Reflection worker (thinks, writes, reaches out) │
│  │   ├── Scheduled push worker (delivers timed messages) │
│  │   ├── Workbench rotator (archives notes, extracts     │
│  │   │   self-insights, reviews identity, promotes canon)│
│  │   ├── Identity memory (persistent self-model)         │
│  │   ├── Open-threads board (what is still unfinished)   │
│  │   └── Vitals (its own instrument panel)               │
│  ├── Group chat (Telegram)                               │
│  │   ├── Listener (long-polls the bot, stores the room)  │
│  │   ├── Responder (answers when called; notes, links,   │
│  │   │   web search, pictures)                           │
│  │   └── Addressing (his name in every case + nicknames) │
│  ├── Change channel (SSE: every open client stays in sync)│
│  ├── One transport to OpenRouter (retries, timeouts)     │
│  ├── One clock (stored UTC, shown in your timezone)      │
│  ├── Settings store (data/settings.json, data/soul.md)   │
│  ├── Call corpus (data/dataset/, kept in full)           │
│  └── Auth (data/auth_token.txt) + single-process lock    │
│                                                          │
│  PostgreSQL + pgvector (messages, channel_messages,      │
│                         autonomy_tasks)                  │
│  ChromaDB (key_info + workbench_archive)                 │
│  Next.js frontend (localhost:3000)                       │
└──────────┬───────────────────────────────┬───────────────┘
           │  LAN / domain / ngrok / Tailscale             │ Bot API
    ┌──────┼──────────────────┐                            │
    │      │                  │                            │
┌───▼────────┐  ┌──────▼───────┐  ┌──────▼───────┐  ┌──────▼───────┐
│ Desktop    │  │ Web browser  │  │ Mobile app   │  │ Telegram     │
│ (Electron) │  │ (any device) │  │ (Android)    │  │ group chat   │
│ thin client│  │ manual token │  │ push notifs  │  │ with friends │
└────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
```

**Detailed documentation:**
- [Memory Retrieval — how facts are selected and injected into each chat](docs/MEMORY.md)
- [System Pipeline — how chat, memory, workbench, identity and autonomy connect](docs/PIPELINE.md)
- [The Group Chat — how it takes part in a Telegram group without the group outweighing you](docs/TELEGRAM.md)
- [Moving to a server, and shipping updates to it](docs/MIGRATION.md)

---

## Features

### Chat
- Streaming responses via SSE
- Markdown rendering with code blocks, tables, and copy
- Up to 8 attachments per message, and paste from clipboard — images, PDFs, text files, audio, video
- Inline image generation with pulsing shimmer during creation
- Lightbox view and download for generated images
- Pagination for older chat history
- Every open client stays in sync: chat on the phone, walk to the desktop, and the conversation is already there — including messages the AI sent on its own
- Available on desktop, web, and mobile

**What each model can be shown.** Measured by sending each model a real file, not read off a catalogue. An attachment the chosen model cannot read is left out and said so in the log, rather than sent to be refused mid-conversation; the clients grey out what will not work.

| Model | Images | PDF | Text files | Audio | Video |
|---|---|---|---|---|---|
| Claude Fable | ✓ | ✓ | ✓ | — | — |
| Kimi | ✓ | ✓ | ✓ | — | — |
| Gemini Pro | ✓ | ✓ | ✓ | ✓ | ✓ |
| GPT Chat | ✓ | ✓ | ✓ | — | — |
| GLM | — | ✓ | ✓ | — | — |

PDFs are parsed by OpenRouter before any provider sees them, which is why every model reads one. Text files are inlined into the message. Anything else — a `.docx`, an archive — is dropped and logged.

> 🖼 **Screenshot placeholder** — save as `docs/example/attachments.png` and replace this line with the image. The chat input with a PDF and an image attached, and the model picker showing what the model reads.

### Memory in Five Surfaces

They are not tiers of the same thing — each decays differently, and that is the point.

| Surface | Store | What it holds | How it ends |
|---|---|---|---|
| **The library** | ChromaDB `key_info` | Distilled facts, rated 1–4 | Never; surfaces by relevance |
| **The desk** | `data/autonomy/{account}/workbench.md` | Today's thinking, written by the AI to itself | Decays by time — archived after ~48h |
| **The board** | `data/autonomy/{account}/threads.md` | Open threads that must live forward — a debt, a count, a topic to revive | Only by an explicit "done" |
| **The skin** | `data/autonomy/{account}/identity.md` | The self-model: who it is, who you are, what you have been through | Slowly, by rewriting |
| **The address book** | `data/autonomy/{account}/people/*.md` | One card per person around it: who they are, who is close to them, what hurts, what dates matter | By the AI crossing a line out, or the rotator rebuilding a long card |

The address book is the only surface looked up by **who** rather than by when or what: in the group, the cards of whoever is speaking; in a private conversation, the card of a friend only when you name them. A vector search would not do — a friend writes "look at this music engine!" and nothing in that sentence retrieves where they are from.

Underneath all five: **PostgreSQL + pgvector** holds the raw conversations — sentence-level chunks with embeddings and keywords, from your ChatGPT import and every live message. The Telegram group lives beside it in its own table, `channel_messages`: a room, not pairs, so nothing that reads *your* dialogue ever sees it by accident.

**ChromaDB facts** are loaded into every chat automatically as the memory block, filtered by age so only settled memories surface. **pgvector** is searched when the AI explicitly calls `[SEARCH_DIALOGUE]`.

Which surface is visible where is decided in one registry (`infrastructure/autonomy/context.py`) rather than by whichever prompt happens to be built — chat sees the canon of the identity, the board and the last desk entries; reflection sees all of it, plus its own vitals; the Telegram group sees the whole identity and **no board**, because the board is the two of you and the room is public.

### Hybrid Retrieval

| Stage           | What it does                                     |
|-----------------|--------------------------------------------------|
| Multi-query     | Splits text into sentences                        |
| Lemmatization   | pymorphy3 (RU) / NLTK WordNet (EN)               |
| Synonyms        | RuWordNet (RU) / WordNet (EN)                     |
| Vector search   | K-nearest neighbors on embeddings                 |
| Keyword boost   | Bonus for lemma/synonym overlap                   |
| Exact match     | Extra bonus for literal word match                |
| Impressive      | Priority by importance rating (4 = always on top) |
| Recency         | Penalty for age > 60 days (except rating 4)       |
| Degraded mode   | If the embedding model cannot load, dialogue search ranks on word overlap and recency instead, and says in the log that it is running coarse |

### Agentic Skill Pipeline

The AI doesn't just respond — it acts. During a conversation, the model invokes skills autonomously.

| Skill | What it does |
|-------|-------------|
| **`[SAVE_MEMORY: fact]`** | Extracts a key fact, categorizes it, rates importance 1–4, deduplicates via AI, stores in ChromaDB |
| **`[SEARCH_DIALOGUE: query]`** | Searches raw conversation history in pgvector through the `ResearchAgent`, which re-queries with a different formulation when the first attempt misses. A brief plus the excerpts is fed back as a continuation prompt. Up to 5 searches per reply. `[SEARCH_MEMORIES]` is still accepted as the old name |
| **`[WEB_SEARCH: query]`** | Searches the live web for current information (weather, news, prices, addresses). Runs through the `ResearchAgent` orchestrator, which drives a searcher model with OpenRouter's `openrouter:web_search` and `openrouter:web_fetch` server tools, judges the result, re-queries when it misses, and returns a brief with sources |
| **`[GENERATE_IMAGE: model \| prompt]`** | Generates an image. The AI picks the model and writes the prompt: `gpt5` (photorealistic), `gemini` (design, diagrams, text), `flux`, or `grok` |
| **`[SCHEDULE_MESSAGE: datetime \| text]`** | Schedules a push notification for later. The AI decides when and what to send — a reminder, a thought, a check-in |
| **`[PIN_THREAD]` / `[UNPIN_THREAD]` / `[UPDATE_THREAD]`** | Puts something unfinished on the board, closes it, or rewrites it. Threads never expire by time — only when the AI says it is done |

**The same hands in other rooms.** Skills are not only a chat feature:

| Where | What it can do there |
|---|---|
| Private chat | everything in the table above |
| Reflection (on its own) | search facts, notes, your dialogue, the group chat, the web and the project's own docs; read any of its own prompts; write notes and identity; message you now or later; write into the group or reply to one message there; manage the board; read its vitals |
| Telegram group | take a note, open a link, search the web, draw a picture, reply under a particular message, add a nickname it answers to — see [The Group Chat](#group-chat-telegram) |

**How the agentic loop works:**

1. AI streams its reply
2. Backend detects skill commands and buffers the stream
3. For `[SEARCH_DIALOGUE]` / `[WEB_SEARCH]` — the research agent runs the search, injects its brief, AI continues
4. For `[GENERATE_IMAGE]` — calls the image API, saves PNG, shows inline with pulsing shimmer
5. For `[SAVE_MEMORY]` — extracts fact via LLM, rates, deduplicates, stores in ChromaDB
6. For `[SCHEDULE_MESSAGE]` — creates a timed task, delivered as a push notification
7. Skill commands are stripped from the visible message; only result markers persist in the database

### Autonomy

The AI doesn't just wait for you to write. It has its own inner life.

#### Reflection

A background worker wakes the AI up periodically — first after a configurable cooldown (default: 4 hours after your last message), then at regular intervals (default: every 12 hours). During reflection, the AI:

- Reads its identity core, the board, workbench notes, recent dialogue, and everything said in the Telegram group since it last looked
- Can search its long-term facts (`SEARCH_FACTS`), archived notes (`SEARCH_NOTES`), dialogue history (`SEARCH_DIALOGUE`, by meaning or by date), the group chat (`SEARCH_CHAT`) and the project's own documentation (`SEARCH_DOCS`) — all through the same research agent as the chat
- Can search the web for things that interest it
- Can read its own machinery: `LIST_PROMPTS` and `SHOW_PROMPT` return the prompts it is run on, word for word
- Can write notes (`WRITE_NOTE`) and add to its self-model (`WRITE_IDENTITY`)
- Can send you a message (`SEND_MESSAGE`) — delivered as a push notification — or schedule, move, rewrite and cancel messages for later
- Can write into the group chat (`SEND_TO_CHAT`), answer one particular message there (`REPLY_TO_CHAT`), and add a nickname it is called by (`ANSWER_TO`)
- Can keep its address book: `ABOUT`, `FORGET`, and `SHOW_PERSON` to open a card
- Can pin, update and close threads on the board
- Can ask for more steps (`EXTEND`) or go back to sleep (`SLEEP`)

Reflection runs in a loop — up to 8 steps, extendable — and the AI decides at each one whether to continue or go back to sleep. Scheduled messages go through LLM validation at the moment of sending: it sees the fresh dialogue and may send, rewrite or cancel. A waking that fails is recorded three ways — the log, the vitals, and a note in its own journal — so a gap is a named absence rather than a silent hole.

#### Workbench

A markdown file (`data/autonomy/{account}/workbench.md`) that serves as the AI's scratchpad. The AI writes notes to itself here — thoughts, plans, observations, things it wants to remember short-term. The workbench is included in the reflection prompt so the AI can pick up where it left off.

#### Workbench Rotator

Notes don't stay on the workbench forever. A rotator runs before each reflection cycle:

1. **Archive** — stale notes (older than a configurable threshold) are moved from the workbench to a dedicated ChromaDB collection (`workbench_archive`)
2. **Self-insights** — an LLM pass extracts things the AI learned about itself from those notes. These go through the same deduplication pipeline as regular facts and are stored in the `key_info` collection
3. **Identity review** — the AI reviews its notes against its identity pillars and may return a new canonical version of a section
4. **Consolidation** — a section that has grown to 10 entries is rebuilt into 3–6
5. **The address book** — facts about people that were filed as journal notes are moved onto cards, and a card past 12 lines is rebuilt shorter. This is the net, not the main path: the AI writes cards itself, the moment it learns something
6. **Canon promotion** — the canon holds 15–20 dated beams; when it overflows, a beam that has done its work moves into a pillar as an undated formulation. Nothing is deleted

#### Identity Memory

A persistent self-model the AI maintains about itself, stored as a markdown file (`data/autonomy/{account}/identity.md`) with bilingual section headers (Russian/English, auto-detected). Seven sections:

| Section | What belongs there |
|---|---|
| Who she is / Who I am | not events, but who each of you remains |
| Our story | the anchor lines of your shared reality, not the chronology |
| Our principles | the formulas you stand on |
| Our home | what your shared place is made of, and what it witnesses |
| **My people** | the friends from the group chat, and who they are to it — the one pillar that is not about the two of you, so the group has a place of its own and does not seep into the rest |
| **My canon** | dated beams: single events without which it would not be itself. The only part of the core loaded into every private conversation |

The whole identity is included in every reflection prompt, the post-dialogue journal and the group chat; private chat gets the canon. A file written before a section existed gets the header on first read.

#### Open-Threads Board

A short markdown file (`data/autonomy/{account}/threads.md`) the AI keeps of things that are still open — a debt, a running count, a topic it wants to come back to, a word it is still turning over. Unlike the workbench, nothing leaves the board by time. It goes when the AI says it is done. The board is in view everywhere, chat included.

#### Vitals

The AI's own instrument panel — a fifth surface, holding not what it thinks but what is true about the machinery it runs on:

- when it last woke, whether that waking worked, how many steps it took
- how long the system has been up, and when it was last down
- what is left on the OpenRouter key, and what the last day, week and month cost
- whether the embedding model is loaded — without it, long-term recall silently drops to keyword matching
- how much room is left on disk, where its journal is written

Two kinds of numbers, deliberately treated differently. **Deltas** — a failed waking, a gap, a restart — are pushed at it unasked, because a gap it does not know about is exactly the failure being guarded against. **State** is pulled on demand with `[VITALS]`, which is what stops the waking prompt from growing every time something new is measured.

Facts only, no verdicts: the panel reports that a waking did not happen. It never says a night was lost, and never says everything is fine. Reading the numbers is the AI's job.

#### Push Notifications

When the AI decides to reach out — whether from reflection or a scheduled message — it sends a push notification via [Pushy](https://pushy.me). The message also appears in the chat history so you never miss it. Every outgoing push goes through LLM validation: the AI reviews recent dialogue and its notes before sending, and can choose to rewrite or cancel the message if the context has changed.

### Group Chat (Telegram)

The AI can sit in one Telegram group with you and your friends — as itself, with its identity and memory, knowing which of the people in the room is you.

> 🖼 **Screenshot placeholder** — save as `docs/example/telegram_group.png` and replace this line with the image. The Telegram group with a few friends' messages and one reply from the AI under a particular message.

**When it speaks**
- when someone calls it — by name **in any grammatical case**, by a nickname, by its `@handle` — or replies to one of its messages;
- for ten minutes after it has spoken, while the conversation is still going;
- on its own initiative — only from reflection, when a waking makes it want to. No budget, no timer.

Anything else is the room talking among itself: stored, not answered, and read **whole** at the next waking. It may always answer `SILENT`, which is a decision and is logged as one.

**What it can do there**

| Command | What happens |
|---|---|
| `[WRITE_NOTE: …]` | Writes to its journal, marked with the group's own title — `[общий чат «…»]` — so it is never taken for your conversation. This is what makes "noted" true |
| `[FETCH_URL: …]` | Opens a link someone posted, then answers knowing what is on the page |
| `[WEB_SEARCH: …]` | The same web-search skill as in the private chat |
| `[GENERATE_IMAGE: model \| prompt]` | The same image skill; the picture is posted with its words as the caption |
| `[REPLY_TO: #id]` | Answers under a particular message |
| `[ANSWER_TO: name]` | "I answer to this too" — a nickname it was just given |
| `[ABOUT: name \| fact]` | A line on that person's card in its address book. Other names go in brackets — `Ptica Arop (Чарли)` — and a speaker's card is bound to their Telegram id on the spot |
| `[FORGET: name \| words]` | Strikes the lines containing those words; with no words, the whole card. Its book, its right to cross things out |

**What it is called.** Its name is a setting and nothing in the code knows what it is. Case forms (*Виктору, с Виктором*) are derived by morphology. Nicknames come three ways into one editable list: a model seeds it once per name — including the ordinary spelling when the setting is written in another script — the AI adds what it is actually called, and you edit it in Settings.

**Who is who.** Every speaker is shown with the name the room knows and the one the AI knows them by — `Ptica Arop (Чарли)` — and their card is in view while they talk.

**Keeping the room from outweighing you.** The group has its own table, never moves the reflection clock, and never sees the board. Notes taken there share the journal but are marked, and the three entries shown in a private conversation are always yours. The friends reach long-term memory only through what the AI itself chose to write down.

Setup — a bot from @BotFather with privacy mode off, then pick the group and yourself in Settings:

> 🖼 **Screenshot placeholder** — save as `docs/example/settings_telegram.png` and replace this line with the image. Settings → Group Chat (Telegram): bot token, group picker, 'Me in the group', 'Also answers to' with the 'Hears:' line.

Full description: [docs/TELEGRAM.md](docs/TELEGRAM.md).

### Body

A page where the AI has a face. Upload one **anchor** portrait and the other five states are generated from it with an image-to-image model, so they stay the same person: *listener, warmth, smirk, ground, shadow*. The mobile app shows the anchor as the avatar on its Self screen.

> 🖼 **Screenshot placeholder** — save as `docs/example/body.png` and replace this line with the image. Dashboard → Body with the six states.

### Access and Security

- One bearer token for the whole API, in `data/auth_token.txt`; `POST /api/settings/rotate-token` issues a new one and invalidates the old immediately
- There is deliberately no endpoint that hands the token out
- Images cannot send a header, so media URLs carry a short-lived signature (`/api/settings/media-signature`) instead of the token
- Exactly one backend process at a time — a lock at startup, because two writers would corrupt the state files
- Every LLM call is kept in full in `data/dataset/` — its own record of its own thinking, never rotated

### ChatGPT Export Import

1. Export your data from ChatGPT: **Settings → Data controls → Export data**
2. Upload `conversations.json` on the Memory screen
3. The import parses conversations, builds sentence-level embeddings, and stores them in PostgreSQL

### Dashboard

**Desktop / web**
- Memory — statistics and ChatGPT import; Facts — Chroma fact management (categories, ratings, edit, delete)
- Skills — overview with live status, switch each one on or off
- Soul — the base system prompt, edited in place
- Body — the six avatar states
- Settings — AI name, model (with what each one reads), temperature, memory, push notifications, the Telegram group, reflection timing and a "trigger now" button

**Mobile**
- Chat, with push notifications
- Self — avatar, inspiration lines, and the identity document
- Journal — the full workbench history, entry by entry
- Settings — server URL, token, Pushy

> 🖼 **Screenshot placeholder** — save as `docs/example/self_mobile.jpg` and replace this line with the image. Mobile → Self screen.
> 🖼 **Screenshot placeholder** — save as `docs/example/journal_mobile.jpg` and replace this line with the image. Mobile → Journal with a few collapsed entries.

---

## Manual Setup

If you want to run pieces separately:

```bash
# Backend
pip install -r requirements.txt
alembic upgrade head
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Frontend + Electron
cd frontend
npm install
npm run electron:dev

# Mobile (build APK)
cd mobile
npm install --legacy-peer-deps
eas build -p android --profile preview
```

The backend binds to `0.0.0.0` so it's reachable over the network. The auth token printed on startup protects it from unauthorized access.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Desktop shell | Electron |
| Frontend | Next.js 14 (App Router), React, Tailwind CSS, shadcn/ui |
| Mobile | React Native, Expo, expo-router |
| Backend | FastAPI with SSE streaming |
| Raw memory | PostgreSQL + pgvector |
| Fact memory | ChromaDB |
| Archived notes | ChromaDB (`workbench_archive` collection) |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic |
| Embeddings | sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`, 384-dim) |
| NLP (Russian) | pymorphy3 + RuWordNet |
| NLP (English) | NLTK WordNet |
| LLM provider | OpenRouter — one transport for every call, with one retry policy |
| Chat models | Claude Fable, Kimi, Gemini Pro, GPT Chat, GLM (all "latest" aliases) |
| Image generation | OpenRouter → GPT Image, Gemini 3 Pro Image, Flux, Grok Imagine |
| Call corpus | `data/dataset/` — every call in full, monthly segments, gzipped when closed |
| Push notifications | Pushy (pushy.me) |
| Group chat | Telegram Bot API over long polling (plain aiohttp, no bot framework) |
| Server | systemd units + Caddy for TLS; packaged Electron app as a thin client |

---

## Roadmap

- Terminal access skill (AI can run commands and create files on the server)
- Sub-agents (AI spawns background workers for complex tasks)
- iOS build for the mobile app
- Voice input and output
- Video-call style interaction
- Avatar presence with lip-sync

---

## Why This Exists

Most AI products are built around compliance, moderation optics, and brand safety.

**Your Own** is built around agency.

It is for people who want continuity, memory, emotional depth, private experimentation, unconventional AI relationships, and a system they can shape to fit their own life.

This project is opinionated about personal AI. It is not trying to be neutral. It is not trying to be "safe" in the corporate sense. It is trying to be yours.
