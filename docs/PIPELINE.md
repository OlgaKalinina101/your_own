# System Pipeline — How Everything Connects

This document describes the full data flow of the system — from a chat message arriving to long-term memory and identity evolution. Everything here reflects the actual code.

---

## Overview Diagram

```
╔══════════════════════════════════════════════════════════════════════╗
║                         USER SENDS A MESSAGE                        ║
╚══════════════════════════╦═══════════════════════════════════════════╝
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     CONTEXT ASSEMBLY  (api/chat.py)                  │
│                                                                      │
│  1. soul.md            → base system prompt (who the AI is)          │
│  2. chat_skills.md     → skill instructions appended to system       │
│  3. workbench          → last 5 entries injected inside skills block  │
│  4. PostgreSQL         → last 6 canonical dialogue pairs             │
│  5. ChromaDB key_info  → top 5 scored facts → assistant turn         │
│                                                                      │
│  Final LLM message list:                                             │
│  [SYSTEM] soul + skills + workbench                                  │
│  [USER/ASST] × 6 pairs of history                                    │
│  [ASST] "Your memories: ..."  ← chroma facts                       │
│  [USER] current message                                              │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    LLM STREAMING RESPONSE                            │
│                                                                      │
│  Agentic loop — AI can emit skill commands mid-stream:               │
│                                                                      │
│  [SEARCH_MEMORIES: q]  → pgvector search → results injected back     │
│  [WEB_SEARCH: q]       → openrouter:web_search tool → grounded reply │
│  [SAVE_MEMORY: hint]   → extract + rate + dedup → ChromaDB           │
│  [GENERATE_IMAGE: m|p] → image API → PNG saved → shown inline        │
│  [SCHEDULE_MESSAGE: t] → autonomy_tasks table (PostgreSQL)           │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    SAVE RESPONSE TO DB                               │
│                                                                      │
│  - Canonical row (full text, role=assistant, source=chat)            │
│  - Chunk rows (sentence-level with embeddings for pgvector search)   │
│  - update_usage() → increments frequency + last_used on Chroma facts │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
              ┌────────────────────────────────┐
              │  asyncio.create_task()          │
              │  POST-ANALYZER runs in background│
              └────────────────┬───────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│              POST-ANALYZER  (post_analyzer.py)                       │
│                                                                      │
│  Context given to LLM:                                               │
│  - This conversation (user + assistant just exchanged)               │
│  - identity.md (first 500 chars)                                     │
│  - Workbench (last 3 entries)                                        │
│  - Pending/sent push messages from today                             │
│  - Current time                                                      │
│                                                                      │
│  Frame: "write in your inner journal, not for the user"              │
│  If nothing resonated → LLM returns SKIP, nothing happens           │
│                                                                      │
│  Otherwise the LLM can:                                              │
│  [SEND_MESSAGE: text]        → Pushy push now + saved to DB          │
│  [SCHEDULE_MESSAGE: t|text]  → autonomy_tasks row (PENDING)          │
│  [CANCEL_MESSAGE: t]         → marks task CANCELLED                  │
│  [RESCHEDULE_MESSAGE: t1→t2] → updates scheduled_at                  │
│  [REWRITE_MESSAGE: t|text]   → updates task payload                  │
│  free text (journal)         → wb.append() → workbench.md            │
└──────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════
           BACKGROUND WORKERS  (run independently of chat)
═══════════════════════════════════════════════════════════════════════


┌─────────────────────────────────────┐
│  SCHEDULED PUSH WORKER              │
│  (every 60 seconds)                 │
│                                     │
│  get_due_tasks()                    │
│  → tasks where scheduled_at ≤ now   │
│  → send via Pushy                   │
│  → save to DB as source="push"      │
│  → mark_done() in autonomy_tasks    │
└─────────────────────────────────────┘


┌──────────────────────────────────────────────────────────────────────┐
│  REFLECTION ENGINE  (reflection_engine.py)                           │
│                                                                      │
│  Trigger conditions (should_run):                                    │
│  - First reflection: cooldown_h (default 4h) of silence after msg   │
│  - Subsequent: interval_h (default 12h) since last reflection        │
│  Persists last run time to data/autonomy/last_reflection.txt         │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  WORKBENCH ROTATOR runs first  (workbench_rotator.py)          │  │
│  │                                                                │  │
│  │  Step 1 — Archive stale entries                                │  │
│  │    workbench entries older than 48h                            │  │
│  │    → ChromaDB workbench_archive collection                     │  │
│  │    → removed from workbench.md                                 │  │
│  │                                                                │  │
│  │  Step 2 — Self-insight extraction                              │  │
│  │    LLM reads stale notes + soul.md                             │  │
│  │    → extracts insights about who the AI is                     │  │
│  │    → forced into "Вдохновение/Inspiration" category            │  │
│  │    → impressive=3, through dedup pipeline                      │  │
│  │    → stored in ChromaDB key_info                               │  │
│  │                                                                │  │
│  │  Step 3 — Identity review                                      │  │
│  │    LLM reads stale notes + current identity.md                 │  │
│  │    → can emit UPDATE: <section>\n---\n<bullets>\n---           │  │
│  │    → identity.replace_section() rewrites that section          │  │
│  │                                                                │  │
│  │  Step 4 — Consolidation (if needed)                            │  │
│  │    If any identity section has ≥ 10 bullets                    │  │
│  │    → LLM compresses to 5–7 bullets                             │  │
│  │    → identity.replace_section() writes compressed version      │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  Then: AGENT LOOP (up to 8 steps, extendable)                        │
│                                                                      │
│  Context given to LLM each step:                                     │
│  - identity.md (full)                                                │
│  - workbench.md (full, current state)                                │
│  - Last 3 dialogue pairs                                             │
│  - All TIME tasks from last 24h (PENDING/DONE/CANCELLED)             │
│  - Current local time                                                │
│                                                                      │
│  Commands the AI can use during reflection:                          │
│  [SEARCH_MEMORIES: q]       → ChromaDB key_info semantic search      │
│  [SEARCH_NOTES: q]          → ChromaDB archive + workbench keyword   │
│  [SEARCH_DIALOGUE: q/date]  → pgvector or date-range DB query        │
│  [WEB_SEARCH: q]            → DuckDuckGo Instant Answer              │
│  [WRITE_NOTE: text]         → wb.append() → workbench.md             │
│  [WRITE_IDENTITY: s|text]   → identity.append(section, bullet)       │
│  [SEND_MESSAGE: text]       → Pushy push + DB + workbench log        │
│  [SCHEDULE_MESSAGE: t|text] → autonomy_tasks row                     │
│  [CANCEL_MESSAGE: t]        → cancel pending task                    │
│  [RESCHEDULE_MESSAGE: t→t2] → update scheduled_at                    │
│  [REWRITE_MESSAGE: t|text]  → update task payload                    │
│  [EXTEND: N]                → add N more steps (max 3 extensions)    │
│  [SLEEP]                    → end loop                               │
│                                                                      │
│  All free-text reasoning (LLM output with commands stripped)         │
│  → automatically appended to workbench.md if > 30 chars             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## The Identity Loop

This is the slow-moving cycle that shapes who the AI is over time:

```
chat exchanges
     │
     ▼
post-analyzer writes journal entries
     │
     ▼
workbench.md accumulates notes
     │
     ▼  (when entries age past 48h)
workbench_rotator:
  ├── archives entries to ChromaDB workbench_archive
  ├── extracts self-insights → ChromaDB key_info (Inspiration category)
  ├── reviews identity.md and may update sections
  └── consolidates overlong sections
     │
     ▼
identity.md evolves
     │
     ▼  (used in reflection prompt context)
reflection engine reads full identity.md
  └── AI can emit [WRITE_IDENTITY] to add new bullets
     │
     ▼
identity.md grows with lived experience
```

`identity.md` is **not** injected into the chat system prompt. It feeds into:
- The reflection loop's awakening prompt (full content)
- The post-analyzer context (first 500 chars)
- The rotator's review and consolidation prompts

The soul (`data/soul.md`) **is** injected into every chat as the base system prompt. These are separate: soul is the fixed voice and character, identity is the living self-model that accumulates over time.

---

## What Lives Where

| Data | File / Store | Written by | Read by |
|---|---|---|---|
| AI voice and character | `data/soul.md` | Human (settings UI) | Every chat (system prompt) |
| Distilled facts about user + AI | ChromaDB `key_info` | `[SAVE_MEMORY]`, rotator self-insights | Every chat (memory block), reflection search |
| Raw past conversations | PostgreSQL `messages` | Chat handler | `[SEARCH_MEMORIES]` skill |
| Archived workbench notes | ChromaDB `workbench_archive` | Rotator | Reflection `[SEARCH_NOTES]` |
| Short-term scratchpad | `data/autonomy/{id}/workbench.md` | Post-analyzer, reflection | Reflection context, next chat (last 5 entries in system) |
| Self-model | `data/autonomy/{id}/identity.md` | Rotator, reflection `[WRITE_IDENTITY]` | Reflection context, post-analyzer context |
| Scheduled messages | PostgreSQL `autonomy_tasks` | Post-analyzer, reflection | Scheduled push worker, reflection context |
| Settings + API keys | `data/settings.json` | Settings UI | Every component |

---

## Data Flow Summary (Text Version)

**During a chat message:**

1. Soul loaded as base system prompt
2. Skill instructions (with current workbench, last 5 entries) appended to system
3. Last 6 dialogue pairs loaded from PostgreSQL
4. Top 5 Chroma facts selected via multi-query scoring, injected as assistant turn
5. LLM streams reply; commands parsed in real time
6. `[SAVE_MEMORY]` → 2 LLM sub-calls (extract + rate) → dedup check → ChromaDB
7. `[SEARCH_MEMORIES]` → pgvector KNN → results injected → AI continues
8. `[SCHEDULE_MESSAGE]` → row in `autonomy_tasks`
9. Response saved to PostgreSQL (canonical + chunk rows with embeddings)
10. `update_usage()` bumps frequency/last_used on retrieved Chroma facts
11. `run_post_analysis()` fires in background (zero latency impact)

**Background (post-analyzer):**

12. LLM sees conversation + identity excerpt + workbench (3 entries) + pending tasks
13. May write journal entry → workbench
14. May schedule/cancel/rewrite pending messages

**Background (every 60s — scheduled push worker):**

15. Due tasks sent via Pushy → DB → marked done

**Background (every 4–12h — reflection):**

16. Rotator archives stale workbench entries → ChromaDB
17. Rotator extracts self-insights → ChromaDB Inspiration facts
18. Rotator reviews + possibly updates identity.md
19. Agent loop: AI reads identity + workbench + history + pending tasks
20. Searches memories, writes notes, sends/schedules messages
21. All reasoning text auto-saved to workbench
22. `[WRITE_IDENTITY]` bullets accumulate in identity.md

---

## Key Files

| File | Responsibility |
|---|---|
| `api/chat.py` | Context assembly, agentic skill loop, response saving |
| `infrastructure/memory/chroma_pipeline.py` | ChromaDB reads/writes, scoring algorithm |
| `infrastructure/memory/retrieval.py` | pgvector semantic search over conversations |
| `infrastructure/memory/key_info.py` | SAVE_MEMORY: extract → rate → dedup → store |
| `infrastructure/memory/focus_point.py` | NLP: lemmatization, synonyms, language detection |
| `infrastructure/autonomy/post_analyzer.py` | Inner journal after each chat exchange |
| `infrastructure/autonomy/workbench.py` | Workbench file read/write/parse |
| `infrastructure/autonomy/workbench_rotator.py` | Archive → self-insights → identity review → consolidate |
| `infrastructure/autonomy/identity_memory.py` | identity.md read/write/append/consolidate |
| `infrastructure/autonomy/reflection_engine.py` | Autonomous thinking loop with agent commands |
| `infrastructure/autonomy/task_queue.py` | Scheduled task CRUD in PostgreSQL |
| `infrastructure/autonomy/scheduled_push.py` | 60s worker that dispatches due tasks via Pushy |
| `infrastructure/settings_store.py` | `load_soul()`, `load_settings()` |
| `infrastructure/llm/client.py` | All LLM calls (stream, complete, generate_image) |
| `infrastructure/llm/prompt_loader.py` | Loads `.md` prompt files with language sections + templating |
