# Memory Retrieval — How It Works

This document describes exactly how memories are selected and injected into each chat dialogue, based on the actual code.

There are two independent memory stores for the private conversation. Both are queried on every chat request. A third, for the Telegram group, is kept apart on purpose — see the end of this document.

---

## Two Stores, Two Purposes

| Store | Technology | What it holds | When it's used |
|---|---|---|---|
| **key_info** | ChromaDB | Distilled facts — events, decisions, relationships, self-insights. Rated 1–4 by importance. | Automatically injected into every chat as the AI's passive memory |
| **messages** | PostgreSQL + pgvector | Raw conversation chunks with embeddings | Queried only when the AI explicitly calls `[SEARCH_DIALOGUE: query]` |

---

## Automatic Chroma Injection (Every Chat)

### Step 1 — Multi-query extraction

`chroma_pipeline.py` → `query_similar_multi()`

The current user message is split into queries:
- The full message text (always)
- Each sentence separately, if the message is longer than 80 characters (up to 4 sentences)

Each query is run through ChromaDB independently. Results are merged and deduplicated by `id`, keeping the **lowest distance** (best match) per fact.

### Step 2 — Hard filters

`chroma_pipeline.py` → `_query_similar()`

Two facts are excluded from every result:

1. **Distance > 0.65** — too semantically distant, not relevant enough.
2. **Created or last used within the last `days_cutoff` days** (default: 2 days) — recently surfaced facts are suppressed to prevent the same anchors from dominating every conversation.

The `days_cutoff` filter checks both `created_at` and `last_used` metadata fields.

### Step 3 — Scoring and boosts

After raw vector search, each candidate fact's distance score is adjusted by a series of boosts and penalties. Lower final score = higher priority.

```
initial_score = cosine_distance (0.0 = identical, 1.0 = opposite)

boosts applied in order:

1. Keyword boost         −0.25 per matching lemma/synonym
2. Exact match boost     −0.15 if full text matches, −0.10 if subset
3. Impressive boost      −0.12 if impressive=4, −0.05 if impressive=3
                         (skipped entirely for "Вдохновение/Inspiration" category)
4. Recency penalty       +small per day beyond 60 days old (max +0.10)
                         (skipped for "Вдохновение/Inspiration" category)
5. Inspiration penalty   applied ONLY to "Вдохновение/Inspiration" category:
                         +0.15 if last_used within 3 days
                         +0.03 per use, capped at +0.20
```

The Inspiration-category facts (self-insights about who the AI is) have their own dedicated penalty track. They cannot get the impressive boost or recency boost — only their own frequency/recency suppression. This prevents the same character anchors from appearing in every reply.

### Step 4 — Final selection

Facts are sorted ascending by final score. Top `top_k` (default: 5) are returned.

### Step 5 — Injection into context

`api/chat.py` → `_build_chroma_block()`

Selected facts are formatted as:

```
Your memories:
— (today) fact text
— (3 days ago) fact text
— (2 month ago) fact text
```

This block is injected as an **assistant role message** in the LLM message list — just before the current user message. It appears as the AI "remembering" rather than as a system instruction.

```
[SYSTEM]      soul.md + skill instructions + workbench
[USER]        history pair N-5 user text
[ASSISTANT]   history pair N-5 assistant reply
...
[USER]        history pair N user text
[ASSISTANT]   history pair N assistant reply
[ASSISTANT]   "Вот что я помню:\n— fact1\n— fact2..."    ← Chroma block here
[USER]        current message
```

### Step 6 — Usage update

After the response is sent, `update_usage()` is called for every fact that was retrieved. This increments the `frequency` counter and stamps `last_used` in the metadata. ChromaDB doesn't support in-place updates, so each updated fact is deleted and re-added with new metadata.

---

## Explicit Memory Search (AI-initiated)

When the AI emits `[SEARCH_DIALOGUE: query]` during a response, the `ResearchAgent` drives a different pipeline:

`infrastructure/memory/retrieval.py` → `retrieve_relevant_pairs()`

This searches **raw past conversations** stored in PostgreSQL, not distilled facts.

1. The question is read once into a `_Query`: pipeline lemmas, surface forms, the
   normalised text, and an embedding.
2. **With an embedding** — KNN via pgvector `<=>` cosine distance, top 200 candidates,
   then scored:
   ```
   composite = min(1.0, cosine_similarity + keyword_boost + exact_boost)
   ```
   - `keyword_boost`: +0.10 per matching token, max +0.25
   - `exact_boost`: +0.15 for exact match, +0.10 for subset
   - floors: cosine < 0.35 or total < 0.40 are discarded
3. **Without an embedding** — the model failed to load, which is what happens on a
   fresh machine. Candidates come from a PostgreSQL array `&&` overlap on
   `focus_point`, and are ranked by how much of the question they carry, newest
   first among equals. There is no score threshold here: with one or two concepts
   per question a threshold either admits everything or nothing. The run is
   coarser than usual and says so in the log — memory that quietly answers worse
   is the failure this branch exists to avoid.
4. Deduplicate by `pair_id`, keep the best-scored chunk per pair.
5. Full user+assistant text fetched for the top pairs — the chunk only decides
   *which* moment; he is shown the whole exchange.

Results are injected back into the conversation as a continuation prompt, and the AI continues its reply with awareness of what it found.

---

## Memory Writing — How Facts Get Created

Facts are created through three paths:

### Path 1 — AI saves during chat (`[SAVE_MEMORY]`)

`infrastructure/memory/key_info.py` → `extract_and_store()`

1. AI emits `[SAVE_MEMORY: hint]` in its reply.
2. LLM call with `key_info_extraction.md` prompt: extracts a clean fact + category from the last 2–3 conversation pairs, guided by the hint.
3. LLM call with `key_info_impressive.md`: rates the fact 1–4.
4. Dedup check: `find_similar()` with threshold 0.35. If a similar fact exists, an LLM call with `key_info_dedup.md` decides: `skip` / `replace` / `keep_both`.
5. `pipeline.add_entry()` stores the fact in ChromaDB with embedding.

### Path 2 — Self-insights from reflection (`workbench_rotator.py`)

After workbench notes age past 48h, the rotator runs an LLM pass over them and extracts insights about the AI's own character. These are forced into the **Вдохновение / Inspiration** category with `impressive=3` and go through the same dedup pipeline.

### Path 3 — Reflection writes directly (`reflection_engine.py`)

During autonomous reflection, the AI can emit `[WRITE_NOTE: text]` (workbench) and `[WRITE_IDENTITY: section | text]` (the self-model). Neither is a Chroma fact yet: notes become facts only through Path 2, when they age past 48 h and the rotator reads them.

### What does *not* create facts

The Telegram group. Its prompt has no `[SAVE_MEMORY]`. A fact about a friend exists only if the AI wrote a note about it — in the room, or at a waking — and the rotator later distilled that note. This is deliberate: a busy group must not be able to fill the library on its own.

---

## Notes from the group chat

`infrastructure/autonomy/workbench.py`

Notes taken in the Telegram group land on the same workbench, marked with the group's own title:

```
### 2026-09-21 19:24 (Asia/Yerevan)
[общий чат «ИИ-СОПРОТИВЛЕНИЕ»] Ptica Arop до сих пор хранит на телефоне приложение DeepSeek…
```

The mark is added by the program (and de-duplicated if he writes it himself). It names the room because "from the chat" was ambiguous in exactly the way that matters — his conversation with her is a chat too.

| Reader | What it sees of the desk |
|---|---|
| Private chat, post-analysis, push validator | the last 3 entries that are **not** from the group |
| The group reply | the last 2 private entries + his own last 5 notes from the room, so a thing is noted once |
| Reflection, the rotator | everything — which is how the friends reach Chroma and the *My people* section of identity |

## The address book

`infrastructure/autonomy/people.py` → `data/autonomy/{account}/people/*.md`

Notes are for what is his — what stirred in him, what is happening today. What stays true of a *person* goes on that person's card instead, with `[ABOUT: name | fact]`, and is struck with `[FORGET: name | words]`.

| Reader | What it gets |
|---|---|
| The group reply | cards of whoever is speaking (by Telegram id) or named (by name, any case) in the last 15 messages — up to 6 |
| Reflection | cards of whoever spoke since he last read the room, an index of the rest, `[SHOW_PERSON]` |
| Private chat | up to 2 cards, only when she names someone |
| Post-analysis, push validator | nothing |
| Rotator | **the whole book, every card in full**, for the identity review — with the rule that a card holds facts and *My people* says who they are to him. Not cut to fit: 31 cards measured 11.6k characters beside an identity of 19.6k |

How a card is found, in the order it is tried:

1. **Who is speaking** — the Telegram id on the message. Exact, free, and it survives a change of display name.
2. **Who was named** — every name and nickname on every card, matched in any grammatical case («у Элайи», «Панде»), across the whole window of messages, not only the latest.
3. **Who goes first** — speakers before the named, newest before oldest. This is what decides who is left out when a prompt has room for six cards and nine people qualify.

It is not a Chroma collection on purpose. Semantic retrieval answers "what is this about?"; in a room the useful question is "who is this?", and the answer is an exact key.

## The third store — the group itself

| Store | Technology | What it holds | When it's used |
|---|---|---|---|
| **channel_messages** | PostgreSQL + pgvector | Every message of the Telegram group, his own included, each with an embedding | The last 15 when he replies in the room; everything since he last read it at a waking; `[SEARCH_CHAT: query]` from reflection |

`[SEARCH_CHAT]` is a research-agent source (`probe_chat` in `infrastructure/agents/sources.py`): cosine similarity with a floor of 0.35, each hit shown with three neighbours on either side, because a single line out of a group chat is rarely legible alone. Without an embedding model it falls back to a substring match and says so in the log.

It is never mixed into `messages`: that table is pairs between two people, and chat history, reflection timing and the post-dialogue journal all assume that shape.

---

## The two vocabularies

`focus_point` — the keyword array on every chunk — is written by
`extract_focus_fast` over each stored sentence, so it holds whatever form that
sentence used. On a live corpus both forms are there: "говорить" in 85 chunks and
"говорили" in 13, "чувствовать" in 53 and "чувствовала" in 41.

The two extractors applied to a *question* produce disjoint sets. So every lookup
into `focus_point` — the keyword boost and the no-embedding branch alike — uses
the **union** of both. Consulting either alone reads half the index and reports
the other half as absent.

The subset test inside `exact_boost` deliberately keeps the narrower set: against
the union it would ask for every word in both its forms at once, which nothing
satisfies.

---

## NLP Pipeline

Both Chroma and pgvector retrieval use `FocusPointPipeline` (`infrastructure/memory/focus_point.py`) for keyword extraction:

- Language detection (Russian vs. English) — the rule itself lives in
  `infrastructure/language.py`. Cyrillic wins; text with **no letters at all** is
  not evidence of anything, and falls back to the language the soul prompt is
  written in rather than to English
- Tokenization and lemmatization:
  - **Russian**: `pymorphy3` for morphological analysis, `RuWordNet` for synonyms
  - **English**: NLTK tokenizer, `WordNet` for synonyms
- Stop-word removal
- Returns a ranked list of lemmas + synonyms used for keyword boosting

---

## Key Files

| File | Role |
|---|---|
| `infrastructure/memory/chroma_pipeline.py` | ChromaDB reads/writes, scoring, boosts, penalties |
| `infrastructure/memory/retrieval.py` | pgvector search over raw conversations |
| `infrastructure/memory/key_info.py` | SAVE_MEMORY handler — extract, rate, dedup, store |
| `infrastructure/memory/focus_point.py` | NLP — lemmatization, synonyms, language detection |
| `api/chat.py` — `_build_chroma_block()` | Formats facts for context injection |
| `api/chat.py` — `_assemble_llm_messages()` | History, memory block, the question — the list as the model sees it |
| `api/chat.py` — `_build_system_prompt()` | Soul + skills + the state blocks the registry allows chat |
| `infrastructure/autonomy/context.py` | Which state blocks each consumer gets, and why |
| `infrastructure/language.py` | One rule for what language to answer in |
| `infrastructure/autonomy/workbench.py` — `append_group_note()`, `get_recent_entries(origin=…)` | The mark on notes from the group, and who sees the desk with or without them |
| `infrastructure/agents/sources.py` — `probe_chat()` | `[SEARCH_CHAT]` over `channel_messages` |
