# Your Own

Every person has the right to interact with AI on their own terms — not what corporations decide is "acceptable", "appropriate", or "safe".

Whether you want a digital companion, a work assistant, a creative partner, a memory-keeper, or something that does not fit any approved category — that choice should belong to you.

**Your Own** is a local-first desktop app for building that kind of relationship with AI: personal, persistent, emotionally aware, and not flattened into a sanitized chatbot.

<table>
<tr>
<td width="50%" align="center">
<img src="docs/example/chat.png" alt="Chat" /><br>
<sub>Chat — streaming, memory recall, skills</sub>
</td>
<td width="50%" align="center">
<img src="docs/example/image_generation.png" alt="Image generation" /><br>
<sub>Inline image generation (GPT-5 / Gemini)</sub>
</td>
</tr>
<tr>
<td width="50%" align="center">
<img src="docs/example/dashboard.png" alt="Dashboard" /><br>
<sub>Dashboard</sub>
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
<img src="docs/example/loading_screen.png" alt="Loading screen" /><br>
<sub>One-click launch with progress</sub>
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

Then run:

```bash
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

### Default Ports

| Service    | Port   |
|------------|--------|
| Frontend   | `3000` |
| Backend    | `8000` |
| PostgreSQL | `5432` |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Electron Desktop Shell                                 │
│  ┌───────────────────────────────────────────────────┐  │
│  │  Next.js 14 (App Router) + React + Tailwind       │  │
│  └────────────────────┬──────────────────────────────┘  │
│                       │ SSE streaming                    │
│  ┌────────────────────▼──────────────────────────────┐  │
│  │  FastAPI Backend                                   │  │
│  │  ┌─────────────┐ ┌───────────┐ ┌───────────────┐  │  │
│  │  │ Agentic     │ │ Memory    │ │ LLM Client    │  │  │
│  │  │ Pipeline    │ │ Retrieval │ │ (OpenRouter)  │  │  │
│  │  └──────┬──────┘ └─────┬─────┘ └───────┬───────┘  │  │
│  │         │              │               │           │  │
│  │  ┌──────▼──────┐ ┌─────▼─────┐ ┌───────▼───────┐  │  │
│  │  │ ChromaDB    │ │ PostgreSQL│ │ Image Gen     │  │  │
│  │  │ (facts)     │ │ + pgvector│ │ GPT-5/Gemini  │  │  │
│  │  └─────────────┘ └───────────┘ └───────────────┘  │  │
│  └────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## Features

### Chat
- Streaming responses via SSE
- Markdown rendering with code blocks, tables, and copy
- Multiple image attachments and paste from clipboard
- Inline image generation with pulsing shimmer during creation
- Lightbox view and download for generated images
- Pagination for older chat history

### Two-Layer Memory

| Layer              | Store                  | Purpose                                        | Source                    |
|--------------------|------------------------|-------------------------------------------------|---------------------------|
| Raw conversations  | PostgreSQL + pgvector  | Sentence-level chunks with embeddings + keywords | ChatGPT import + live chat |
| Distilled facts    | ChromaDB               | Key facts rated by importance (1–4 stars)        | AI via `[SAVE_MEMORY]`     |

**ChromaDB facts** are automatically loaded into the AI context as its "memory block" — filtered by age so only older, settled memories surface.

**pgvector** is used when the AI explicitly calls `[SEARCH_MEMORIES]` to dig into raw past conversations.

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

### Agentic Skill Pipeline

The AI doesn't just respond — it acts. During a conversation, the model invokes skills autonomously.

| Skill | What it does |
|-------|-------------|
| **`[SAVE_MEMORY: fact]`** | Extracts a key fact, categorizes it, rates importance 1–4, deduplicates via AI, stores in ChromaDB |
| **`[SEARCH_MEMORIES: query]`** | Searches raw conversation history in pgvector. Results are fed back as a continuation prompt — AI replies with awareness of what it found. Up to 5 searches per reply |
| **`[WEB_SEARCH: query]`** | Searches the live web for current information (weather, news, prices, addresses). Uses OpenRouter's `:online` model suffix |
| **`[GENERATE_IMAGE: model \| prompt]`** | Generates an image using `gpt5` (GPT-5 Image — photorealistic) or `gemini` (Gemini 3 Pro — design, diagrams, text). AI chooses the model and writes the prompt. Can share images spontaneously. Saved as PNG, shown inline |

**How the agentic loop works:**

1. AI streams its reply
2. Backend detects skill commands and buffers the stream
3. For `[SEARCH_MEMORIES]` / `[WEB_SEARCH]` — executes the action, injects results, AI continues
4. For `[GENERATE_IMAGE]` — calls the image API, saves PNG, shows inline with pulsing shimmer
5. For `[SAVE_MEMORY]` — extracts fact via LLM, rates, deduplicates, stores in ChromaDB
6. Skill commands are stripped; only `[SAVED_FACT: ...]` and `[GENERATED_IMAGE: ...]` markers persist in the database

### ChatGPT Export Import

1. Export your data from ChatGPT: **Settings → Data controls → Export data**
2. Upload `conversations.json` on the Memory screen
3. The import parses conversations, builds sentence-level embeddings, and stores them in PostgreSQL

### Dashboard
- Memory statistics
- Skill overview with live status
- Chroma fact management (categories, ratings, edit, delete)
- Settings panel

---

## Manual Setup

If you want to run pieces separately:

```bash
# Backend
pip install -r requirements.txt
alembic upgrade head
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# Frontend + Electron
cd frontend
npm install
npm run electron:dev
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Desktop shell | Electron |
| Frontend | Next.js 14 (App Router), React, Tailwind CSS, shadcn/ui |
| Backend | FastAPI with SSE streaming |
| Raw memory | PostgreSQL + pgvector |
| Fact memory | ChromaDB |
| ORM / migrations | SQLAlchemy (async) + Alembic |
| Embeddings | sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`, 384-dim) |
| NLP (Russian) | pymorphy3 + RuWordNet |
| NLP (English) | NLTK WordNet |
| LLM provider | OpenRouter (GPT, Claude, Gemini, Llama, Qwen, and more) |
| Image generation | OpenRouter → GPT-5 Image, Gemini 3 Pro Image |

---

## Roadmap

- Voice input and output
- Avatar presence with lip-sync
- Video-call style interaction
- Autonomous reflection loop (AI writes notes, rethinks, reaches out)
- Identity core (persistent self-model the AI maintains)
- Scheduled messages and proactive check-ins
- Better packaging for non-technical users

---

## Why This Exists

Most AI products are built around compliance, moderation optics, and brand safety.

**Your Own** is built around agency.

It is for people who want continuity, memory, emotional depth, private experimentation, unconventional AI relationships, and a system they can shape to fit their own life.

This project is opinionated about personal AI. It is not trying to be neutral. It is not trying to be "safe" in the corporate sense. It is trying to be yours.
