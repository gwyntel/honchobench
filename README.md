[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/gwyntel/honchobench)

# HonchoBench

Benchmark LLMs against **honcho's** real memory/deriver/dialectic pipeline — the
*actual* production stack, not a reimplementation. Ingests a labeled multi-turn
conversation corpus through honcho's API, lets the real deriver + summarizer +
dialectic agents run against any OpenAI-compatible model gateway, then scores
their output against ground-truth labels.

Built around `plastic-labs/honcho` pinned at commit `4448979`, vendored at
`_vendor/honcho/` (git-ignored — cloned fresh by `scripts/setup_db.sh`).

## Why the "supported way"

Honcho ships its own agent stack (Deriver, Summarizer, Dialectic, Dreamer). A
reimplementation misses the point: the goal is to measure *honcho's* agents, not
a mock of them. This repo stands up the real thing — Postgres + pgvector, the
FastAPI server, the deriver queue consumer — and drives it through the real
`/v3` API.

## Architecture

- **Postgres 15 + pgvector** (docker, `--network host`, port 5433) — vector store + queue
- **Redis 8** (docker, `--network host`) — optional cache
- **honcho API server** (`uvicorn src.main:app`, port 8000) — enqueues work
- **deriver** (`python -m src.deriver`) — drains the queue: representation,
  summarizer, dreamer
- **dataset** — `data/dataset/conversations.json` (Enron, 19 conversations /
  276 messages) + `labels.json` (8 labeled with explicit facts, QA, deductive,
  inductive, short/long summaries)

## Quickstart

```bash
# 1. create the git-ignored key file (dedicated Plexus bench key, never your agent key)
echo "sk-..." > .bench_plexus_key

# 2. vendor honcho, start docker infra, migrate, configure embeddings
./scripts/setup_db.sh

# 3. start API + deriver (uses the dedicated key)
./start_honcho.sh

# 4. smoke test: one conversation end-to-end
python3 smoke_test.py enron_shackleton_nelson_eci_guaranty
```

## Benchmarking a model

Swap the `__MODEL` value in every `*_MODEL_CONFIG__*` line in `.env.example`,
then re-run `setup_db.sh` + `start_honcho.sh`. All 7 roles route through
`MODEL_CONFIG__OVERRIDES__BASE_URL` (Plexus: `https://plexus.nebulosa-bass.ts.net/v1`).

**Critical gotcha (cost us real debugging time):** honcho does **not** inherit
the top-level `MODEL_CONFIG__*` into per-role configs. Each role (`DERIVER_`,
`SUMMARY_`, `DIALECTIC_LEVELS__*__`) defaults to `gpt-5.4-mini` @
`api.openai.com` (401 without a key) unless set explicitly. The `.env.example`
already sets every role.

## Key discipline

- The API key is **never** stored in a tracked file. It lives in
  `.bench_plexus_key` (git-ignored) and is exported by `start_honcho.sh` as
  `PLEXUS_API_KEY`, which honcho resolves via `OVERRIDES__API_KEY_ENV`.
- Use a **dedicated scoped Plexus key** for benchmarking — never the agent's own
  gateway key.

## Scoring

Ground truth is in `data/dataset/labels.json`. After the deriver drains, score
each role's output against labels via the driver (WIP — see
`IRL_EVAL_DESIGN.md` for the full ingestion + scoring plan).

## Files

- `scripts/setup_db.sh` — vendor honcho, docker infra, migrate, embeddings
- `start_honcho.sh` — API + deriver startup
- `smoke_test.py` — bounded single-conversation end-to-end verification
- `IRL_EVAL_DESIGN.md` — full design doc (env vars, ingestion, cost estimates)
- `data/dataset/` — conversations + labels + manifest
