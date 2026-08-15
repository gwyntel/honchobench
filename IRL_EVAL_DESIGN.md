# HonchoBench — IRL Evaluation Design: Running Real Honcho v3.0.12 Against the Enron Dataset

**Date:** 2026-08-15
**Scope:** Feasibility + design report (evaluation only — **nothing below has been executed**; no server started, no LLM calls made, no long-running containers launched).
**Source root:** `/home/hermes/projects/honchobench/_vendor/honcho` (commit `4448979`, v3.0.12)
**Author's context:** Standalone HonchoBench harness exists at `/home/hermes/projects/honchobench/` and is verified working; this report determines how close to **real, in-the-wild honcho** that harness gets and what a full local instance would require.

---

## 0. Executive summary / recommendation

**Bottom line: Full IRL is feasible on this box with modest, well-scoped changes — the single hard prerequisite is `pgvector` (Postgres + the `vector` extension), plus a small Redis for the optional cache. Everything else is already satisfiable.**

| Question | Answer |
|---|---|
| Can we run the real honcho API + deriver on this machine? | **Yes.** FastAPI + uv/uvloop worker run fine on 15 GB RAM, 38 GB free disk. |
| Is Postgres+pgvector the hard blocker? | **Yes, the real blocking dependency.** Honcho's vector store defaults to **pgvector** (`VectorStoreSettings.TYPE = "pgvector"`) and the process **fails closed at boot** (`startup/embedding_validator.py`) if the `documents`/`message_embeddings.embedding` column dim ≠ `EMBEDDING_VECTOR_DIMENSIONS`. Nothing servers without it. |
| Is Redis required? | **No — optional.** Redis is used **only** for the cache layer (`src/cache/client.py`), which both API and deriver catch and degrade gracefully (`main.py` logs "proceeding without cache"). The **work queue is Postgres-backed** (`src/deriver/enqueue.py` inserts `QueueItem` rows; `QueueManager` polls Postgres), not Redis. Believable default `redis://localhost:6379/0?suppress=true`, `CACHE.CLUSTER=false`. |
| LLM/embedding routing to Plexus gateway? | **Yes, natively supported** via per-role `MODEL_CONFIG` + `overrides.base_url` + `overrides.api_key`, credentials resolved from env (`ModelOverrideSettings`, `config.py:107-138`, `resolve_model_config` `config.py:499-530`). |
| **Main risk** | **Embedding dimension mismatch.** Our gateway's `nomic-embed-text-v1.5` is **768-dim**; the shipped default is `text-embedding-3-small` @ **1536-dim**. The pgvector columns are created at the *configured* dim, so we must set `EMBEDDING_VECTOR_DIMENSIONS=768` **before** `alembic upgrade head`, or run `scripts/configure_embeddings.py` to rebuild columns. |
| Second risk | **Deriver batch delay.** Default `REPRESENTATION_BATCH_MAX_AGE_SECONDS=1800` will idle non-message work for 30 min. Must set `DERIVER_FLUSH_ENABLED=true` (and/or `REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS=0`, `REPRESENTATION_BATCH_MAX_AGE_SECONDS=0`). |
| Third risk | **Dream threshold gating.** Auto-dreams need `DREAM.DOCUMENT_THRESHOLD=50` explicit docs and `MIN_HOURS_BETWEEN_DREAMS=8h`. The manual route `POST /v3/workspaces/{ws}/schedule_dream` **bypasses** these gates — use it. |

**Recommendation:** Implement the IRL path as described in §2–§4, running honcho's two real processes (`fastapi dev src/main.py` + `python -m src.deriver`) against dockerized `pgvector/pgvector:pg16` + optional `redis/redis:7-alpine`. Use it as a **validation environment** for the standalone harness, not as a full replacement for every run — the standalone harness remains faster/cheaper for iteration, and the IRL run validates the gap areas in §5.

---

## 1. INFRA INVENTORY

### 1.1 What real honcho needs, exactly

Confirmed from source:

| Component | Requirement | Evidence / default | Verdict on this box |
|---|---|---|---|
| **PostgreSQL** | Any modern PG (tests use PG13+ per `conftest.py` `WITH (FORCE)`) | `DB.CONNECTION_URI = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"` (`config.py:704`) | Must add via docker |
| **pgvector extension** | `CREATE EXTENSION IF NOT EXISTS vector` | `tests/conftest.py:289`; startup dim check in `src/startup/embedding_validator.py` | Must add (the real blocker) |
| **Embedding schema dim** | Columns `documents.embedding` + `message_embeddings.embedding`, dim must equal `EMBEDDING_VECTOR_DIMENSIONS` | `embedding_validator.py:_EMBEDDING_TABLES`, `_assert_pgvector_dims_match` | **Set to 768** to match `nomic-embed-text-v1.5` |
| **Postgres-backed work queue** | `QueueItem` table (task_types: `representation`, `summary`, `dream`, `reconciler`, `webhook`, `deletion`, `scope_backfill/removal`) | `src/deriver/enqueue.py:insert(QueueItem)`; `consumer.py` dispatch | Built-in — no extra service |
| **Redis (optional cache)** | Only `cache/`, defaults `redis://localhost:6379/0`, `CACHE.CLUSTER=false` | `config.py:1280`; `main.py:119-124` degrades; `cache/client.py` `init_cache` | Optional; add for fidelity |
| **Deriver worker** | One long-running uvloop process | `src/deriver/__main__.py`; `DERIVER.WORKERS=1` (bool workers scale *within* one process) | Run `uv run python -m src.deriver` |
| **API process** | One FastAPI/uvicorn process, port 8000 | `Dockerfile CMD ["fastapi","run","--host","0.0.0.0","src/main.py"]` | Run `uv run fastapi dev src/main.py` |
| **Migrations** | alembic, `alembic upgrade head` | `alembic.ini` + `migrations/`; `embedding_validator` says "Run `alembic upgrade head` first" | Run once + `scripts/configure_embeddings.py` if dim differs |
| **Python** | 3.13 (Docker image) / any 3.10+ with uv | `Dockerfile FROM python:3.13-slim-bookworm`; `uv` toolchain | uv already installed |
| **Embedding/LLM calls** | OpenAI-compatible via `transport="openai"` | `src/llm/backends/openai.py`, `src/embedding_client.py` | Route to Plexus |
| **Auth** | Off by default | `AUTH.USE_AUTH=false` | Leave off — fatal-auth not a blocker |

### 1.2 Docker images / compose services to use

The repo does **not** pin a Postgres image (`docker/` only ships `entrypoint.sh`, `prometheus.yml`, `grafana-datasource.yml`). Tests run against whatever `DB.CONNECTION_URI` points at. Proposal:

```yaml
# /home/hermes/projects/honchobench/_vendor/honcho/docker-compose.irl.yml
services:
  pgvector:
    image: pgvector/pgvector:pg16   # official; bundles the `vector` extension
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
    ports: ["5432:5432"]
    # ensure the vector extension dir exists in the image; honcho's conftest
    # runs CREATE EXTENSION IF NOT EXISTS vector itself.
  redis:
    image: redis:7-alpine           # OSS standalone; CACHE.CLUSTER=false
    ports: ["6379:6379"]
```

- **Postgres: `pgvector/pgvector:pg16`** (official on Docker Hub; `pg16` tag matches production-age PG; you can also use `pgvector/pgvector:pg17`). Chosen because the repo gives no pinned image and this is the canonical image that ships `vector`.
- **Redis: `redis:7-alpine`** (standalone, single-node). **Redis topology reality:** honcho's cache uses a single `redis://` URL and `CACHE.CLUSTER` is a bool; `cluster=false` means standalone. We do **not** need Redis Cluster. If we skip Redis entirely, honcho still runs (degraded cache).
- Honcho app itself: **not** containerized here — run via `uv` in the vendored clone so we touch real source and config directly. (The Dockerfile exists but adds no benefit over `uv run` on this box.)

### 1.3 Honcho's own runtime topology (from `CLAUDE.md` + source)

Two cooperating processes sharing Postgres:
1. **API server** — `uv run fastapi dev src/main.py` (FastAPI, port 8000, `/health`). Handles HTTP; on message create enqueues background tasks and triggers **immediate-embed** (`reconciler/embed_now.py`, capped by `EMBEDDING_MAX_CONCURRENT_EMBEDDINGS=10`); hosts the inline **Dialectic** agent.
2. **Deriver worker** — `uv run python -m src.deriver` (uvloop). Consumes the Postgres `QueueItem` queue: runs Deriver + Summarizer + Dreamer. Also hosts the in-process **Reconciler scheduler** (`src/reconciler/scheduler.py`) that periodically embeds `sync_state='pending'` rows and cleans stale queue items.

---

## 2. INGESTION PLAN

### 2.1 Dataset grounding

`/home/hermes/projects/honchobench/data/dataset/conversations.json`: **19 conversations**, each `{conversation_id, messages:[{sender,text}], target_peer}`. `target_peer` values are the observed-person Enron addresses (e.g. `jeff.dasovich@enron.com`), and `sender` values are email addresses. Ground truth in `labels.json` (8 labeled). The fixture messages carry **no authoritative timestamps** (`store.py` synthesizes them; `roles.py:_format_messages` invents `2026-01-01 09:00:00 + 4min*i`). This matters: see §2.4.

### 2.2 Workspace / peer / session cardinality decision

**Recommendation (justified):**
- **One workspace** for the whole dataset: `honchobench`. Rationale: workspace is the root namespace; the Enron set is one evaluation corpus, and a single workspace = no cross-workspace auth/delete churn, one queue to watch. Honcho namespaces vector data per workspace+collection; a single workspace gives clean per-peer collection separation anyway.
- **One peer per `target_peer`** — a peer is a *durable identity* (a person), shared across the multiple conversations that mention them (e.g. `tana.jones@enron.com` appears in 2 conversations; `sara.shackleton@enron.com` in 4). Creating one peer per target peer correctly accumulates that person's representation across all their conversations — this is exactly how honcho forms a global memory. (One peer per *conversation* would fragment the representation and is the wrong model.)
- **Two peers per session required:** honcho sessions are **multi-partipant**; to form a representation of a target peer we put **both the target peer and a synthetic "assistant"/observer peer** in the session. Each Enron **conversation = one session**. Justification: a session is a discrete conversation context; `target_peer` messages go in with `peer_id=<target_peer>` and the other sender(s) with a fixed observer peer id (e.g. `honcho_observer`). This makes the deriver observe the target peer (its messages) and lets the dialectic answer from the observer's perspective.

  > **Configuration nudge:** to have honcho *build a representation of a peer* it must be configured to observe. Set `PeerConfig.observe_me` — but the default behavior already observes peers in a session; leave defaults unless validation shows the target peer isn't collected. If needed, pass `peer_config={"observe_me": true}`.

### 2.3 Exact API call sequence (all `POST`, base `http://localhost:8000/v3`)

Auth is off (`AUTH.USE_AUTH=false`), so no key header is needed. Use `curl` or the Python SDK (see `tests/sdk/` for the canonical client shapes).

**Step 1 — Create workspace** (idempotent, `get_or_create`):
```
POST /v3/workspaces
{"name": "honchobench", "metadata": {"bench": "enron"}}
```
Returns 201/200 with `Workspace`.

**Step 2 — Create the observer peer** (one-time):
```
POST /v3/workspaces/honchobench/peers
{"name": "honcho_observer", "metadata": {"role": "observer"}}
```

**Step 3 — Create target peer(s).** Idempotent `get_or_create`; one per distinct `target_peer` across the dataset:
```
POST /v3/workspaces/honchobench/peers
{"name": "jeff.dasovich@enron.com", "metadata": {"person": "Jeff Dasovich"}}
```

**Step 4 — Create a session per conversation.** `SessionCreate` body is `{id, peers?, configuration?, scopes?}` (`api.py:418`). Attach the two peers so both are members:
```
POST /v3/workspaces/honchobench/sessions
{
  "id": "enron_dasovich_steffes_marketpower",
  "peers": {"honcho_observer": {}, "jeff.dasovich@enron.com": {}}
}
```
(`peers` maps peer_id → `{observe_me?, observe_others?}`; `{}` = defaults.)

**Step 5 — Batch-insert messages.** Endpoint + max-100 batch:
```
POST /v3/workspaces/honchobench/sessions/enron_dasovich_steffes_marketpower/messages
{
  "messages": [
    {"peer_id": "jeff.dasovich@enron.com", "content": "<text>", "created_at": "2001-01-01T08:00:00Z"},
    {"peer_id": "honcho_observer", "content": "<text>", "created_at": "2001-01-01T08:01:00Z"}
  ]
}
```
- `MessageCreate` fields: `content` (≤ `MAX_MESSAGE_SIZE`=25,000 chars), `peer_id` (alias for `peer_name`, `api.py:340-345`), optional `metadata`, `configuration`, `created_at`.
- **Ordering:** honcho assigns `seq_in_session` from DB insertion/position; the deriver sorts the batch by message id ascending. **Insert in dataset order** and let the DB assign seq. Do **not** rely on `created_at` for message order — but providing `created_at` matters for the deriver's `{YYYY-MM-DD HH:MM:SS}` timestamp formatting and for `get_messages_by_date_range`.
- **Timestamps:** the fixture has none. Two options:
  - *Faithful real-world:* synthesize plausible Enron-era timestamps (e.g. spread over 2000-2002 in conversation order) and pass `created_at`. This makes date-range tools + temporal search meaningful.
  - *Simplest:* omit `created_at` and let honcho stamp now. **Validity risk:** all messages appear "today"; date-based dialectic tools and the deriver's absolute-date requirement get skewed. **Recommend synthesis.**

**Step 6 — Verify queue drain.** Check nothing is stuck before dreaming:
```
GET /v3/workspaces/honchobench/queue/status
```
(`src/routers/workspaces.py:186`, `QueueStatus`; filters `observer_id`, `sender_id`, `session_id`.)

**Should each conversation be its own workspace? No.** One workspace (as above) is cleaner and matches how a single production app serves thousands of sessions under one workspace.

---

## 3. PIPELINE ACTIVATION

### 3.1 Deriver flush / immediate-processing knobs (avoid the 30-min wait)

All under the `DeriverSettings` (`config.py:857-973`), env prefix `DERIVER_`:

| Config | Env var | Default | Set to |
|---|---|---|---|
| Immediate-flush master switch | `DERIVER_FLUSH_ENABLED` | `False` | `true` |
| Token accumulation gate | `DERIVER_REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS` | `512` | `0` (disables accumulation gate entirely — "claimable as soon as anything is pending", per docstring) |
| Age-based flush | `DERIVER_REPRESENTATION_BATCH_MAX_AGE_SECONDS` | `1800` (30 min) | `0` (docstring: "0 disables age-based flushing"; combined with FLUSH_ENABLED work is processed immediately) |
| Poll interval | `DERIVER_POLLING_SLEEP_INTERVAL_SECONDS` | `1.0` | `1.0` (fine) |
| Workers (loop within process) | `DERIVER_WORKERS` | `1` | `1-4` for paralllelism |

**Confirmed in source:** `queue_manager.py:336` ("unless FLUSH_ENABLED is True"), `:400` (`if not settings.DERIVER.FLUSH_ENABLED and work_unit_target_tokens > 0`), `:428`. **Set `DERIVER_FLUSH_ENABLED=true` as the primary fix** — that alone bypasses the token gate. Adding the two `=0` values removes the age gate too, so nothing ever idles.

### 3.2 Summarizer activation

Summaries run off the same queue (`consumer.py:123` → `summarizer.summarize_if_needed`); no separate flush knob needed — they fire per enqueued `summary` task. Message windows: `SUMMARY.MESSAGES_PER_SHORT_SUMMARY=20`, `SUMMARY.MESSAGES_PER_LONG_SUMMARY=60`. Config toggles: `SUMMARY.ENABLED=true`, `SUMMARY_MAX_TOKENS_SHORT=1000`, `SUMMARY_MAX_TOKENS_LONG=4000`, model via `SUMMARY_MODEL_CONFIG__*` (see §4).

### 3.3 Dream activation (on demand)

Honcho's dream cycle = **omni dreamer** (`DREAM.ENABLED_TYPES=["omni"]`, `config.py:1336`) which internally runs **DeductionSpecialist → InductionSpecialist** (`src/dreamer/orchestrator.py`). `card_refresh` is a separate `DreamType` (`configuration.py:16-23`: `OMNI="omni"`, `CARD_REFRESH="card_refresh"`).

**Bypass the auto gates with the manual route** (endpoint bypasses document threshold + min-hours, see `workspaces.py:230-237`):
```
POST /v3/workspaces/honchobench/schedule_dream
{
  "observer": "honcho_observer",
  "observed": "jeff.dasovich@enron.com",
  "dream_type": "omni",
  "session_id": "enron_dasovich_steffes_marketpower",
  "rebuild": false
}
```
`ScheduleDreamRequest` fields (`api.py:882-894`): `observer` (required), `observed` (defaults to observer), `dream_type` (required enum), `session_id` (optional), `rebuild` (bool, card_refresh only). **Requires `DREAM.ENABLED=true`** (else 400; `workspaces.py:240`).

- This enqueues a `dream` `QueueItem`; the deriver's `QueueManager` picks it up (dream scheduling lives in the deriver process's `DreamScheduler`).
- To also exercise `card_refresh`, call the same route with `"dream_type": "card_refresh"` (+ `"rebuild": true/false`).
- If we want the specialists isolated (deduction only, no induction), that is **not** exposed as a public route — the omni orchestration runs both sequentially inside `run_dream`. The standalone harness can still isolate them, but IRL the omni is the atomic unit. **Flag this as a fidelity boundary** (see §5).

### 3.4 How the dialectic gets exercised

Via the chat endpoint (`/chat`) with `reasoning_level`:
```
POST /v3/workspaces/honchobench/peers/honcho_observer/chat
{
  "query": "<benchmark question from labels.json>",
  "reasoning_level": "low",            # minimal|low|medium|high|max
  "session_id": "enron_dasovich_steffes_marketpower",
  "target": "jeff.dasovich@enron.com",  # observed: answer from observer's understanding of target
  "stream": false
}
```
`DialecticOptions` (`api.py:756-804`): `query`, `reasoning_level` (default `low`), `session_id`, `target`, `filters`, `scope`, `stream` (bool; SSE when true), `response_format` (optional JSON schema). Returns `DialecticResponse{content}` (`api.py:812`). The dialectic is agentic (tool loop, `peers.py:278` → `src/dialectic/chat.py:agentic_chat`), with `max_tool_iterations` per level (low=5, medium=2, high=4, max=10, minimal=1).

### 3.5 Reading back conclusions / representation for scoring

- **All conclusions for a peer (explicit + derived):**
  ```
  POST /v3/workspaces/honchobench/conclusions/list
  {"filters": {"observer_id": "honcho_observer", "observed_id": "jeff.dasovich@enron.com"}}
  ```
  (`conclusions.py:60`, `ConclusionGet`; paginated).
- **Semantic conclusion search (embedding-backed):**
  ```
  POST /v3/workspaces/honchobench/conclusions/query
  {"query": "<fact from gt>", "top_k": 20,
   "filters": {"observer_id": "honcho_observer", "observed_id": "jeff.dasovich@enron.com"}}
  ```
  (`conclusions.py:94`; requires both observer+observed in filters, else 422).
- **Peer representation (markdown summary):**
  ```
  POST /v3/workspaces/honchobench/peers/honcho_observer/representation
  {"target": "jeff.dasovich@enron.com", "search_query": "<optional>", "search_top_k": 10, "max_conclusions": 25}
  ```
  Returns `RepresentationResponse{representation: str}` (`api.py:307`).
- **Peer card:** `GET /v3/workspaces/honchobench/peers/honcho_observer/card?target=jeff.dasovich@enron.com`.

**Scoring harness** reads `conclusions/list` output (the public `Conclusion` objects: `content`, `observer_id`, `observed_id`, `session_id`, `level`, `created_at`) and/or the markdown representation — this is what replaces the standalone harness's in-memory observation extraction.

---

## 4. ROLE-PER-ROLE CONFIG (point every role at Plexus)

### 4.1 Env-var naming model (verified)

- Settings use pydantic-settings with `env_prefix` + `env_nested_delimiter="__"` (`config.py` throughout). So a nested path `DERIVER.MODEL_CONFIG.OVERRIDES.BASE_URL` → env var **`DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL`**.
- `ModelOverrideSettings` (`config.py:107-138`): `api_key`, `api_key_env`, `base_url`, `provider_params`. Credentials resolve in `resolve_model_config` via `_resolve_secret` (`config.py:499-530`): `overrides.api_key` literal wins; else `os.getenv(overrides.api_key_env)`.
- Base URL: passed verbatim to `AsyncOpenAI(base_url=...)` (`src/llm/registry.py:127`, `src/embedding_client.py:219`). **Plexus base** = `https://plexus.nebulosa-bass.ts.net/v1` (OpenAI SDK appends `/chat/completions`, `/embeddings` to it). Put the Plexus key in **`LLM_OPENAI_API_KEY`** (global) or per-role `overrides.api_key_env`.
- For the **embedding** client, `resolve_encoding_format` (`config.py:840-854`) detects our non-OpenAI host (`plexus...` not in `_EMBEDDING_BASE64_CAPABLE_HOSTS`) → uses **float** on the wire. Good — no base64 surprises.

### 4.2 Full env matrix (swap `MODEL=…` between runs to sweep models)

Plexus base: `LEXUS_BASE=https://plexus.nebulosa-bass.ts.net/v1` (set once), `PLEXUS_API_KEY` (never printed).

**Global / foundational:**
```
DB_CONNECTION_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
EMBEDDING_MODEL_CONFIG__TRANSPORT=openai
EMBEDDING_MODEL_CONFIG__MODEL=nomic-embed-text-v1.5
EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=https://plexus.nebulosa-bass.ts.net/v1
EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=PLEXUS_API_KEY
EMBEDDING_VECTOR_DIMENSIONS=768          # MUST match nomic-embed-text-v1.5 dim; set BEFORE migrations
LLM_OPENAI_API_KEY=$PLEXUS_API_KEY        # optional global key for LLM transports
AUTH_USE_AUTH=false
DERIVER_FLUSH_ENABLED=true
DERIVER_REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS=0
DERIVER_REPRESENTATION_BATCH_MAX_AGE_SECONDS=0
DREAM_ENABLED=true
SUMMARY_ENABLED=true
```

**Per-role LLM** (each `transport=openai`, `base_url` = Plexus, model swapped per run):

| Role | Env var root (`__MODEL`, `__TRANSPORT`, `__OVERRIDES__BASE_URL`, `__OVERRIDES__API_KEY_ENV`) | Effective default cited |
|---|---|---|
| 1 deriver | `DERIVER_MODEL_CONFIG__…` | `transport=openai, model=gpt-5.4-mini` |
| 2 summary_short | `SUMMARY_MODEL_CONFIG__…` | `transport=openai, model=gpt-5.4-mini` |
| 3 summary_long | `SUMMARY_MODEL_CONFIG__…` (same MODEL_CONFIG used for both) | same |
| 4 dialectic (all levels) | `DIALECTIC_LEVELS__<level>__MODEL_CONFIG__…` for `minimal\|low\|medium\|high\|max` | `transport=openai, model=gpt-5.4-mini` |
| 5 dream_deduction | `DREAM_DEDUCTION_MODEL_CONFIG__…` | `transport=openai, model=gpt-5.4-mini` |
| 6 dream_induction | `DREAM_INDUCTION_MODEL_CONFIG__…` | `transport=openai, model=gpt-5.4-mini` |
| 7 dream_card_refresh | **`DREAM_DEDUCTION_MODEL_CONFIG__…`** (reuses deduction config — see `ROLE_SPEC.md` §7.6) | `transport=openai, model=gpt-5.4-mini` |

Example single-role override (deriver → a specific Plexus alias, e.g. `qwen3-coder`-style alias `MODEL_X`):
```
DERIVER_MODEL_CONFIG__TRANSPORT=openai
DERIVER_MODEL_CONFIG__MODEL=MODEL_X
DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=$PLEXUS_BASE
DERIVER_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=PLEXUS_API_KEY
```
A sweep = re-export `MODEL`/the `__MODEL` vars for the 7 roles and re-run the IRL stage. Everything is env-driven; no code edits.

**Structured-output caveat (deriver):** the deriver uses `response_model=PromptRepresentation` + a `response_format`; OpenAIBackend prefers **Structured Outputs (`json_schema`)** (`openai.py:197`) but **falls back** to `json_object` when the provider returns a 400 (logging "set structured_output_mode=json_object"). If Plexus rejects `json_schema`, **set `DERIVER_MODEL_CONFIG__STRUCTURED_OUTPUT_MODE=json_object`** to skip the failing round-trip. Verify during smoke.

---

## 5. FAITHFULNESS GAP ANALYSIS (standalone vs IRL) — the core "how close"

**What the standalone harness (`src/honchobench/`) does today:** builds exact prompts per `ROLE_SPEC.md`, calls an OpenAI-compatible client via `src/honchobench/clients.py`, and backs the agentic roles with `InMemoryStore` (`store.py`) = **substring/keyword-overlap "semantic" search over synthetic observations** (one explicit observation per target-peer message, `_seed_observations`), no real embeddings, no DB, no queue, no timing.

| # | Role | What IRL exercises that standalone does **NOT** | Where standalone diverges / validity risk |
|---|---|---|---|
| 1 | **deriver** | Real Postgres-claimed **queue batching**; `REPRESENTATION_BATCH_TARGET_INPUT_TOKENS` window assembly (queued + interleaved context); real embedding of extracted obs; **dedup** (exact + semantic) at save; `WORKING_REPRESENTATION_MAX_OBSERVATIONS` application. | Standalone: single-shot, no queue, no interleaved context window, no DB dedup/embed, synthetic `created_at`. **The batch-window assembly + dedup are the biggest deltas here.** |
| 2/3 | **summary_short / summary_long** | Real summarize-if-needed on the queue; real **message-token estimation** (`o200k_base`, `MessageCreate.validate_and_set_token_count`); previous-summary folding across messages; exact `MESSAGES_PER_SHORT/LONG` windowing against DB seq. | Standalone: single call, manually windowed, no previous-summary chain, no token-count estimation. |
| 4 | **dialectic** | Real **embedding-backed retrieval** (`search_memory`/`search_messages` via pgvector `query_documents`), **prefetch** of relevant observations (`_prefetch_relevant_observations` embeds query + 2 searches), **session-history injection**, `get_reasoning_chain` against a real reasoning tree, temporal tools over real timestamps. | Standalone: keyword-overlap retrieval over synthetic obs, no prefetch, no session history, no reasoning chain, date range approximated by sequence order. **This is the largest fidelity delta in the whole harness — retrieval semantics determine answer grounding.** |
| 5 | **dream_deduction** | Real dream **scheduling** (queue + `execute_dream` finds latest session), real observation store + `source_ids` referencing real doc ids, `update_peer_card` shared card state. | Standalone: tool-loop over in-memory observations; source_ids are synthetic; no real collection/document persistence. |
| 6 | **dream_induction** | Same as deduction + runs **after** deduction (sees its new deductive obs) within `run_dream`. | Standalone: isolated, doesn't see whatever the deduction specialist produced unless chained manually. |
| 7 | **dream_card_refresh** | Reads real peer card + real observations; `rebuild` mode reconstruction; real emit/save of card. | Standalone: in-memory `peer_card` list only; no persistence. |
| **all** | — | Queue semantics (claiming, ordering, retries), immediate-embed vs reconciler fallback, embedding latency/throughput, real API auth/responses. | Standalone: stateless, no queue, no auth, no persistence. |

**Honest conclusion on closeness:** the **prompts** and **LLM-call shapes** (model, max_tokens, temperature, tools, `max_tool_iterations`) are byte-for-byte faithful in the standalone harness (modulo the deriver's BATCH-model input). The **retrieval/memory/dream-scheduling layer** is where the standalone version diverges most. IRL closes that gap entirely by running real pgvector search + the real queue + real dream orchestration. It is the *only* way to exercise the actual retrieval semantics. Remaining IRL-specific approximations to acknowledge:
1. **Fixture messages have no genuine timestamps** → we must synthesize them (§2.4); real honcho production would have real `created_at`.
2. **`omni` is atomic** — IRL can't drive the deduction specialist alone; you get deduction+induction together.
3. **Embedding model** differs from honcho's shipped default (`nomic-embed-text-v1.5` vs `text-embedding-3-small`); retrieval quality is evaluated against *our* embedding, which is the honest production posture for this machine's gateway.
4. **No cross-machine scale** — a single node means both processes + Postgres + Redis share 15 GB; fine for 19 conversations, not a load test.

---

## 6. SMOKE PLAN + COST

> ⚠️ **Nothing runs until the user approves.** This section is the plan and estimate only.

### 6.1 Minimal first run (validation)

- 1–2 conversations (use the 2 `tana.jones@enron.com` conversations — one is 8-labeled-adjacent and they share a peer), **one fast cheap model alias** across all 7 roles + embeddings.
- Steps: bring up `pgvector` + `redis` containers → `uv sync` in the clone → export env block (§4.2) once → `alembic upgrade head` (+ `configure_embeddings.py` if dim drift) → start API + deriver → ingest §2.2-2.5 → verify `queue/status` drained → call `schedule_dream` (`omni` + `card_refresh`) → run 2 dialectic chat queries → read `conclusions/list` + representation for scoring.

**Estimated LLM call count (2 conversations, 1 label):**

| Stage | Calls | Notes |
|---|---|---|
| Deriver (2 conv) | ~2–4 | 1 per drained representation batch; small convs may merge, FLUSH_ENABLED makes it per-batch. |
| Summaries (2 conv) | ~2–4 | short + long per conversation once threshold hit. |
| Dream omni (1 peer) | ~2–20 | deduction + induction specialists, ≤12/≤10 tool iterations each, but short when few obs. |
| Dream card_refresh (1 peer) | ~1 | single specialist, ≤6 iterations. |
| Dialectic chat ×2 | ~2–20 | low level, ≤5 iterations each, plus **1 embedding call per retrieval + 1 for prefetch** per question. |
| **TOTAL LLM calls** | **≈ 10–50** | Cheap alias → cents. |

**Embedding calls:** every message + every extracted observation + each dialectic query/retrieval. For 2 convs (~50 msgs) that's ~50 + observations (~50) + retrieval (~10) ≈ **~110 embedding calls**, ~cents.

### 6.2 Full run estimate (19 conversations, 8 labels)

| Stage | Scale | Est. LLM calls |
|---|---|---|
| Deriver | 19 convs, ~1300 msgs | ~19–40 |
| Summaries | 19 convs | ~38 |
| Dream omni | per distinct target peer (~7 distinct) | ~7×(4–40) ≈ 30–280 |
| Dream card_refresh | per peer | ~7–14 |
| Dialectic | 8 labels (queries from `labels.json`) | 8×(2–20) ≈ 16–160 |
| **Full IRL LLM calls** | | **≈ 110–530** |
| Embedding calls | messages + obs + retrieval | ≈ 1300 + ~1300 + ~160 ≈ **~2800** |

**Cost:** on a cheap/self-hosted alias through Plexus, this is **a few dollars at the very most**; on fast local models it's effectively free. Dominant cost is the agentic dialectic/dreamer tool loops (each iteration = 1 LLM call). If cost per iteration needs trimming, lower `reasoning_level` (fewer tool iterations) or cap `max_tool_iterations`.

**Estimated wall-clock:** ingesting 19 convs in ~1–5 min with FLUSH_ENABLED; deriver+summary drain < a few minutes; dreams few minutes; dialectic ~1–5 s per query on a local model. Whole full run: **well under an hour**, mostly waiting on tool loops.

### 6.3 Explicit approvals required before execution

- Start `pgvector/pgvector:pg16` and `redis:7-alpine` containers (persistent, ~200–400 MB disk; within the 38 GB budget but long-running).
- `uv sync` in the vendored clone (installs deps).
- **Make LLM + embedding API calls to Plexus** (real money, real traffic).
- Run honcho's API + deriver processes for the duration of a run.

---

## Appendix A — Key file paths (for the implementer)

- Config: `_vendor/honcho/src/config.py` (env prefix rules 676-699, model override 107-138, deriver knobs 857-973, dream 1327-1406, dialectic 1068-1164, summary 1167-1199, embedding 786-854, cache 1276-1299, db 701-728, auth 731-742).
- Config resolution / secrets: `src/utils/config_helpers.py`, `resolve_model_config` `config.py:499-530`.
- Server entry: `src/main.py` (lifespan 104-137, embed-schema gate 117, routers 172-179).
- Deriver worker entry: `src/deriver/__main__.py`; queue: `src/deriver/queue_manager.py` (claim logic ~330-447, main 1145); consumers: `src/deriver/consumer.py`.
- Enqueue (DB queue + dream): `src/deriver/enqueue.py`.
- Role prompts / invocation: `ROLE_SPEC.md` (spec) + `src/deriver/deriver.py`, `src/utils/summarizer.py`, `src/dialectic/core.py`, `src/dreamer/specialists.py`, `src/dreamer/orchestrator.py`.
- Routes: `src/routers/{workspaces,peers,sessions,messages,conclusions}.py`.
- Schemas: `src/schemas/api.py`, `src/schemas/configuration.py`.
- Embedding client + startup gate: `src/embedding_client.py`, `src/startup/embedding_validator.py`.
- Migrations: `alembic/`, `alembic.ini`; reconfigure dims: `scripts/configure_embeddings.py`.
- Test infra for reference: `tests/conftest.py` (DB/extension spin-up), `tests/bench/` (benchmarks), `tests/sdk/` (client shapes), `tests/unified/` (scenario cases).
- Dataset: `data/dataset/conversations.json` (19 convs) + `labels.json` (8 labeled).
- Standalone harness: `src/honchobench/{roles,store,prompts,clients,scoring,tool_loop}.py`.
