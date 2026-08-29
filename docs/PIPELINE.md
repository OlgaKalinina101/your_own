# System Pipeline — How Everything Connects

This document describes the full data flow of the system — from a chat message arriving to long-term memory and identity evolution. Everything here reflects the actual code.

---

## Overview Diagram

```
╔══════════════════════════════════════════════════════════════════════╗
║                         USER SENDS A MESSAGE                         ║
╚══════════════════════════╦═══════════════════════════════════════════╝
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     CONTEXT ASSEMBLY  (api/chat.py)                  │
│                                                                      │
│  Which state blocks each consumer may see is decided in ONE place:   │
│  infrastructure/autonomy/context.py — not by whoever builds a prompt.│
│                                                                      │
│  1. soul.md            → base system prompt (who the AI is)          │
│  2. chat_skills.md     → skill instructions appended to system       │
│  3. canon              → dated identity beams (chat gets the canon,  │
│                          not the whole identity — that is reflection)│
│  4. open_threads       → the board of unfinished threads             │
│  5. workbench          → the last 3 desk entries                     │
│  6. current time + timezone label                                    │
│  7. PostgreSQL         → last 6 canonical dialogue pairs             │
│  8. ChromaDB key_info  → top 5 scored facts → assistant turn         │
│                                                                      │
│  Final LLM message list (_assemble_llm_messages):                    │
│  [SYSTEM] soul + skills + canon + board + desk + time                │
│  [USER/ASST] × 6 pairs of history (internal markers stripped)        │
│  [ASST] "Your memories: ..."  ← chroma facts                         │
│  [USER] current message                                              │
└──────────────────────────────────────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    LLM STREAMING RESPONSE                            │
│                                                                      │
│  Agentic loop — AI can emit skill commands mid-stream:               │
│                                                                      │
│  [SEARCH_DIALOGUE: q]  → ResearchAgent (dialogue) → brief + excerpts │
│  [WEB_SEARCH: q]       → ResearchAgent (web) → brief injected back   │
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
│  - identity.md (whole — the registry hands post-analysis the same    │
│    pillars reflection gets)                                          │
│  - Workbench (last 3 entries)                                        │
│  - Pending/sent push messages from today                             │
│  - Current time                                                      │
│                                                                      │
│  Frame: "write in your inner journal, not for the user"              │
│  If nothing resonated → LLM returns SKIP, nothing happens            │
│                                                                      │
│  Otherwise the LLM can:                                              │
│  [SEND_MESSAGE: text]        → Pushy push now + saved to DB          │
│  [SCHEDULE_MESSAGE: t|text]  → autonomy_tasks row (PENDING)          │
│  [CANCEL_MESSAGE: t]         → marks task CANCELLED                  │
│  [RESCHEDULE_MESSAGE: t1→t2] → updates scheduled_at                  │
│  [REWRITE_MESSAGE: t|text]   → updates task payload                  │
│  [PIN/UNPIN/UPDATE_THREAD]   → the open-threads board                │
│  free text (journal)         → wb.append() → workbench.md            │
│                                                                      │
│  A command that fails, or that finds nothing to act on, is named in  │
│  the journal entry beside the plan it belongs to. The journal is     │
│  what he reads to remember: it must not describe a message he        │
│  scheduled if no task was created.                                   │
│                                                                      │
│  A lookup of today's pushes that fails says so, rather than showing  │
│  an empty list — empty reads as "nothing is scheduled", and he       │
│  schedules it again.                                                 │
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
│  - First reflection: cooldown_h (default 4h) of silence after msg    │
│  - Subsequent: interval_h (default 12h) since last reflection        │
│  Persists last run time per account, under data/autonomy/{id}/       │
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
│  [SEARCH_FACTS: q]          → ResearchAgent (facts) → Chroma key_info│
│  [SEARCH_NOTES: q]          → ResearchAgent (notes) → archive + wb   │
│  [SEARCH_DIALOGUE: q/date]  → ResearchAgent (dialogue) → pg / by date│
│  [WEB_SEARCH: q]            → ResearchAgent (web) → brief + sources  │
│  [WRITE_NOTE: text]         → wb.append() → workbench.md             │
│  [WRITE_IDENTITY: s|text]   → identity.append(section, bullet)       │
│  [SEND_MESSAGE: text]       → Pushy push + DB + workbench log        │
│  [SCHEDULE_MESSAGE: t|text] → autonomy_tasks row                     │
│  [CANCEL_MESSAGE: t]        → cancel pending task                    │
│  [RESCHEDULE_MESSAGE: t→t2] → update scheduled_at                    │
│  [REWRITE_MESSAGE: t|text]  → update task payload                    │
│  [PIN_THREAD: text]         → add a thread to the board              │
│  [UNPIN_THREAD: #id]        → close it (the only way one leaves)     │
│  [UPDATE_THREAD: #id|text]  → rewrite one in place                   │
│  [CANCEL_ALL_SCHEDULED]     → drop every pending message at once     │
│  [VITALS]                   → his own instrument panel, on demand    │
│  [EXTEND: N]                → add N more steps (max 3 extensions)    │
│  [SLEEP]                    → end loop                               │
│                                                                      │
│  A command that finds nothing — a message already sent, a thread not │
│  on the board — is answered in words on the next step. He decides    │
│  what that means; the engine does not decide for him.                │
│                                                                      │
│  A waking that does not happen is recorded three ways: the log for   │
│  us, vitals for the retry and the next waking's deltas, and a note   │
│  in his own journal, so the gap is a named absence rather than a     │
│  silent hole between two entries.                                    │
│                                                                      │
│  All free-text reasoning (LLM output with commands stripped)         │
│  → automatically appended to workbench.md if > 30 chars              │
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
- The post-analyzer context (whole)
- The rotator's review and consolidation prompts

The soul (`data/soul.md`) **is** injected into every chat as the base system prompt. These are separate: soul is the fixed voice and character, identity is the living self-model that accumulates over time.

---

## What Lives Where

| Data | File / Store | Written by | Read by |
|---|---|---|---|
| AI voice and character | `data/soul.md` | Human (settings UI) | Every chat (system prompt) |
| Distilled facts about user + AI | ChromaDB `key_info` | `[SAVE_MEMORY]`, rotator self-insights | Every chat (memory block), reflection search |
| Raw past conversations | PostgreSQL `messages` | Chat handler | `[SEARCH_DIALOGUE]` skill |
| Archived workbench notes | ChromaDB `workbench_archive` | Rotator | Reflection `[SEARCH_NOTES]` |
| Short-term scratchpad | `data/autonomy/{id}/workbench.md` | Post-analyzer, reflection | Reflection reads it whole (rotation is its job); everyone else the last 3 entries |
| Self-model | `data/autonomy/{id}/identity.md` | Rotator, reflection `[WRITE_IDENTITY]` | Reflection context, post-analyzer context |
| Scheduled messages | PostgreSQL `autonomy_tasks` | Post-analyzer, reflection | Scheduled push worker, reflection context |
| Open threads (the board) | `data/autonomy/{id}/threads.md` | Reflection, post-analyzer | Every consumer — chat included |
| Instrument panel | `data/autonomy/{id}/vitals.json` | Reflection worker, heartbeat | Reflection (deltas unasked, full panel on `[VITALS]`) |
| Every LLM call, in full | `data/dataset/calls-YYYY-MM.jsonl` (older months gzipped) | `llm/client.py` | Kept, not rotated — the record of his own thinking |
| Settings + API keys | `data/settings.json` | Settings UI | Every component |
| Auth token | `data/auth_token.txt` | Generated on first run | Every request |

---

## Data Flow Summary (Text Version)

**During a chat message:**

1. Soul loaded as base system prompt
2. Skill instructions and the state blocks chat is entitled to — canon, board, last 3 desk entries, local time — appended to system
3. Last 6 dialogue pairs loaded from PostgreSQL
4. Top 5 Chroma facts selected via multi-query scoring, injected as assistant turn
5. LLM streams reply; commands parsed in real time
6. `[SAVE_MEMORY]` → 2 LLM sub-calls (extract + rate) → dedup check → ChromaDB
7. `[SEARCH_DIALOGUE]` → ResearchAgent → pgvector KNN, re-query on a miss → brief injected → AI continues
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
| `infrastructure/llm/call_log.py` | The call corpus: monthly segments, gzip on close, bounded tail reads |
| `infrastructure/autonomy/context.py` | The registry: which state block each consumer sees, and why |
| `infrastructure/autonomy/commands.py` | The command vocabulary, and the one place a command happens |
| `infrastructure/autonomy/threads.py` | The open-threads board |
| `infrastructure/autonomy/vitals.py` | The instrument panel: wakings, uptime, key, memory model, disk, spend |
| `infrastructure/clock.py` | One timezone. Stored = UTC instant, shown = his local time |
| `infrastructure/language.py` | One rule for what language to answer in |
| `infrastructure/state_file.py` | Atomic writes, quarantine of a file that will not parse |
| `infrastructure/paths.py` | Where the project root and every data directory are |
| `infrastructure/account.py` | One account, stated as an invariant rather than assumed |
| `infrastructure/single_process.py` | One backend at a time — two would corrupt the state files |
