# Your Own

Every person has the right to interact with AI on their own terms, not what corporations decide is "acceptable", "appropriate", or "safe".

Whether you want a digital companion, a work assistant, a creative partner, a memory-keeper, or something that does not fit any approved category, that choice should belong to you.

`Your Own` is a local-first desktop app for building that kind of relationship with AI: personal, persistent, emotionally aware, and not flattened into a sanitized chatbot.

## What It Is

`Your Own` is an Electron desktop app with:

- a `Next.js` frontend
- a `FastAPI` backend
- `PostgreSQL + pgvector` for raw conversation memory
- `ChromaDB` for distilled long-term semantic facts
- an agentic skill pipeline the AI uses autonomously
- OpenRouter model support
- chat history import from ChatGPT exports

The goal is simple: give people a framework for AI that can become personally meaningful, not just commercially acceptable.

## Quick Start

### Requirements

Install these first:

- `Python 3.11+`
- `Node.js` (which also gives you `npm`)

Then run:

```bash
cd frontend
npm run electron:dev
```

On first run, the setup script automatically tries to:

1. Detect or install `PostgreSQL`
2. Create the local `your_own` database
3. Write `.env` from `.env.example` if needed
4. Install frontend dependencies
5. Install Python dependencies
6. Install/enable `pgvector`
7. Run Alembic migrations
8. Start the frontend, backend, and Electron app

For most local setups, that should be enough.

## Default Local Ports

| Service | Port |
|---------|------|
| Frontend | `3000` |
| Backend | `8000` |
| PostgreSQL | `5432` |

## What Already Works

- Electron desktop shell
- Chat UI with streaming responses
- Multiple image attachments in chat, including paste from clipboard
- Markdown rendering in messages, including code blocks with copy
- Persistent local settings via Electron
- Two-layer memory: `pgvector` for raw conversations, `ChromaDB` for distilled facts
- Agentic skill pipeline: AI autonomously saves memories, searches past conversations, and looks things up on the web
- ChatGPT `conversations.json` import with sentence-level embeddings
- Focus-point extraction for keyword boosting
- Semantic retrieval of older memories into live chat context
- Chroma fact management UI with categories, ratings, edit, and delete
- Pagination for older chat history in the chat screen
- Dashboard with memory stats and skill overview

## Memory: How It Works

Memory is not stored as one giant blob.

When you import a ChatGPT export:

1. The parser walks through `conversations.json`
2. Each user/assistant exchange is grouped as a shared `pair_id`
3. Messages are split into sentence-level chunks
4. Each chunk gets:
   - its own embedding
   - its own `focus_point` keywords
   - role metadata (`user` or `assistant`)
5. Those chunks are stored in PostgreSQL and searched later with `pgvector`

During live chat:

1. The current user message is saved
2. Recent conversation pairs are loaded from chat history
3. Older relevant pairs are retrieved semantically from memory
4. The model receives:
   - recent active thread
   - recalled older moments
   - the current message
5. After streaming completes, the assistant reply is saved back into memory

The retrieval itself is hybrid:

- vector similarity for semantic recall
- keyword overlap via `focus_point`
- exact/subset boosts for stronger matches

This gives the app something closer to lived continuity rather than plain "chat history".

## Agentic Pipeline

The AI doesn't just respond — it acts. During a conversation, the model can invoke skills on its own, without being explicitly asked.

### Skills

**`[SAVE_MEMORY: <fact>]`** — The AI extracts a key fact from the conversation and saves it to ChromaDB. Only meaningful, long-term facts get saved: life events, decisions, relationships, fears, values. Not routine or mood. The AI can save multiple facts in a single reply.

Each saved fact gets:
- a category (e.g. `Работа`, `Семья`, `Отношения`, `Ценности`)
- an impressiveness rating (1–4 stars)
- a timestamp
- deduplication against existing facts

**`[SEARCH_MEMORIES: <query>]`** — The AI searches raw conversation history in pgvector for relevant past context. Results are injected as an agentic step: the AI receives them and replies with awareness of what it found. Up to 5 searches per reply, so it can rephrase and try again if the first query misses.

**`[WEB_SEARCH: <query>]`** — The AI searches the live web for fresh external facts: weather, news, prices, addresses. Uses OpenRouter's native `:online` model suffix. The AI never says "I don't have internet access" — it uses this skill instead.

### How It Works

1. The AI streams its reply
2. If the reply contains skill commands, the backend parses them
3. For `[SEARCH_MEMORIES]` or `[WEB_SEARCH]`, the backend executes the action and feeds the results back to the AI as a continuation prompt
4. The AI continues its reply with the new context
5. For `[SAVE_MEMORY]`, facts are extracted and stored in ChromaDB after the reply finishes

The user sees inline indicators in chat: a pulsing label while the skill runs, then a static result label. Skill commands themselves are stripped from the visible message.

### Two-Layer Memory

| Layer | Store | Purpose | Populated by |
|-------|-------|---------|-------------|
| Raw conversations | PostgreSQL + pgvector | Sentence-level chunks with embeddings and keywords | Chat import + live chat |
| Distilled facts | ChromaDB | Key facts rated by importance | AI via `[SAVE_MEMORY]` |

ChromaDB facts are automatically loaded into the AI's context as its "memory block" — filtered by age (configurable cutoff in settings) so only older, settled memories surface, not yesterday's conversation recycled.

pgvector is used when the AI explicitly calls `[SEARCH_MEMORIES]` to dig into raw past conversations.

## ChatGPT Export Import

To import old conversations:

1. Open ChatGPT
2. Go to `Settings -> Data controls -> Export data`
3. Download the export
4. Extract the archive
5. Upload `conversations.json` inside the app on the `Memory` screen

The import screen shows progress while the backend:

- parses the export
- builds embeddings
- stores semantic chunks in PostgreSQL

## Why This Exists

Most AI products are built around compliance, moderation optics, and brand safety.

`Your Own` is built around agency.

It is for people who want:

- continuity
- memory
- emotional depth
- private experimentation
- unconventional AI relationships
- a system they can shape to fit their own life

## Interface Examples

### Chat

![Chat UI](docs/example/chat.png)

### Dashboard

![Dashboard](docs/example/dashboard.png)

### Settings

![Settings](docs/example/settings.png)

### Skills — Agentic Pipeline

![Skills](docs/example/skills.png)

### Saved Facts — ChromaDB Memory

![Memory Facts](docs/example/memory.png)

### Loading Screen

![Loading Screen](docs/example/loading_screen.png)

### ChatGPT Export Flow

![Export ChatGPT Data](docs/example/export_chatgpt_data.png)

## Roadmap

Planned next steps include:

- voice input and voice output
- avatar presence with lip-sync
- video-call style interaction
- autonomous reflection loop (AI writes notes, rethinks, reaches out on its own)
- identity core (persistent self-model the AI maintains)
- scheduled messages and proactive check-ins
- better packaging for non-technical users

## Manual Setup

If you want to run pieces separately:

```bash
# backend dependencies
pip install -r requirements.txt

# database migrations
alembic upgrade head

# backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# frontend / electron
cd frontend
npm install
npm run dev
```

## Tech Stack

- `Electron` — desktop shell
- `Next.js 14` — frontend (App Router)
- `React` + `Tailwind CSS` — UI
- `FastAPI` — backend with SSE streaming
- `PostgreSQL` + `pgvector` — raw conversation memory with vector search
- `ChromaDB` — distilled fact store for long-term semantic memory
- `SQLAlchemy` + `Alembic` — ORM and migrations
- `sentence-transformers` — multilingual embeddings (`paraphrase-multilingual-MiniLM-L12-v2`, 384-dim)
- `pymorphy3` + `ruwordnet` — Russian lemmatization and synonym expansion
- `NLTK WordNet` — English lemmatization and synonyms
- `OpenRouter` — LLM provider (supports GPT, Claude, Llama, Gemini, Qwen, and more)

## Notes

- This project is opinionated about personal AI.
- It is not trying to be neutral.
- It is not trying to be "safe" in the corporate sense.
- It is trying to be yours.
