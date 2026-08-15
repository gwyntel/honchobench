# HonchoBench

A standalone, **model-agnostic benchmark harness** for the seven AI-memory roles
that make up [honcho](https://github.com/plastic-labs/honcho)'s "always-on AI
memory" system — packaged so anyone can run a reproducible bench with **one
command**.

It reproduces the exact prompts and tool contracts from `ROLE_SPEC.md` and scores
each role's structured output against ground-truth labels, **without** needing a
live honcho server, Postgres, or Redis. The dataset is the curated Enron-derived
corpus in `data/dataset/` (19 conversations, 8 labeled).

This repo is the **standalone harness** (fast, cheap iteration). For the full
real-honcho "in the wild" (IRL) fidelity run see `IRL_EVAL_DESIGN.md`.

---

## Quick start (containerized, one command)

```bash
# 1. One-time setup: copy .env.example -> .env, build the image, print next step
make setup

# 2. Edit .env and set your gateway key
#    (PLEXUS_API_KEY=<your key>)

# 3. Run the default bench
make bench
```

That's it. Reports (`.md` + `.csv` per role, plus a summary `reports/README.md`)
land in `./reports` on the host.

> Default target: the **self-hosted Plexus gateway** at
> `https://plexus.nebulosa-bass.ts.net/v1` (OpenAI-compatible), with the exemplar
> **chat model `LiquidAI/LFM2_5-2_6B`** and the documented embedding model
> **`nomic-ai/nomic-embed-text-v1.5`**. Nothing is hardcoded — all of it comes
> from `.env`/environment at runtime.

### The one-line command to produce reports

```bash
docker compose run --rm honchobench
```

or its Makefile alias:

```bash
make bench
```

Both run the default bench against the Plexus gateway and write reports to
`./reports`. A quick smoke run is `make bench LIMIT=1`.

---

## Requirements

- [Docker](https://docs.docker.com/engine/install/) with compose v2
  (`docker compose`). No other host-side Python or model stack is needed.
- An API key for whatever model endpoint you use (default: a Plexus gateway key).

---

## How it works / components

| File | Purpose |
|------|---------|
| `Dockerfile` | `python:3.11-slim`; pip-installs `requirements.txt`, copies `src/`, `ENTRYPOINT honchobench`. |
| `docker-compose.yml` | Single `honchobench` service; `env_file: .env`, bind-mounts `./data` and `./reports`. |
| `.env.example` | Every env var the runner honours, with placeholders. |
| `requirements.txt` | Pinned runtime deps (`openai anthropic google-genai pydantic jinja2 httpx rich python-dotenv`). |
| `Makefile` / `scripts/quickstart.sh` / `bench` | Setup + one-command wrappers. |
| `src/honchobench/` | The harness: `prompts.py clients.py store.py tool_loop.py roles.py scoring.py report.py __main__.py`. |
| `data/dataset/` | `conversations.json` (19 convos) + `labels.json` (8 labeled). |
| `reports/` | Generated `.md`/`.csv` per role (created on your host by the mount). |

---

## Building the image

```bash
docker build --network host -t honchobench:latest .        # or: make build
```

> `--network host` is used because some Docker daemons (this box included)
> cannot reach pypi.org from the default bridge network during the
> `pip install` step. It is harmless on normal daemons, and `make build` /
> `scripts/quickstart.sh` already set it for you.

Verify it works in-container without any keys (no network touched):

```bash
docker run --rm honchobench --help            # prints the CLI help
```

---

## Running a bench

### Default bench (recommended models)

```bash
make bench          # or: docker compose run --rm honchobench
```

- Runs all seven roles over all 19 conversations.
- Chat model: `LiquidAI/LFM2_5-2_6B` (via the `openai` transport) → Plexus gateway.
- Embedding model `nomic-ai/nomic-embed-text-v1.5` is a *gateway router* concern
  for semantic search; the standalone harness itself makes no embedding calls, so
  it is documented here and in `.env.example`, not invoked by the runner.
- Writes `deriver.csv/.md`, `summary_short.…`, `summary_long.…`, `dialectic.…`,
  `deduction.…`, `induction.…`, `card_refresh.…` and `README.md` to `./reports`.

### Custom model list

```bash
# via Makefile
make bench MODELS=gpt-4o,gpt-4o-mini

# via the ./bench wrapper
./bench gpt-4o gpt-4o-mini

# straight through docker compose
docker compose run --rm honchobench --models gpt-4o,gpt-4o-mini
```

Transport is chosen per model id:

- `anthropic/…` prefix → Anthropic SDK (`ANTHROPIC_API_KEY`)
- `gemini/…` prefix → Google GenAI (`GEMINI_API_KEY`)
- anything else → OpenAI-compatible (`PLEXUS_API_KEY`/`OPENAI_API_KEY`)

> **Unknown model ids / missing keys never crash a run.** Each model is
> validated before use; if its transport isn't configured, or a per-conversation
> call throws, the harness logs a clear line and **skips** it, then still writes
> reports for everything that succeeded. A bare default run needs at least one
> usable key; otherwise it exits with a helpful message.

### Default model list via env

Set `HONCHOBENCH_MODELS` in `.env` (or the shell) to override the default list
without touching the CLI:

```bash
export HONCHOBENCH_MODELS='gpt-4o,gpt-4o-mini'
docker compose run --rm honchobench
```

Precedence: `--models` argument > `HONCHOBENCH_MODELS` env > exemplar default
(`LiquidAI/LFM2_5-2_6B`).

---

## Configuration & env-var conventions

All keys/base URLs are read from the environment **at runtime** — never baked
into the image. The runner loads them in this order (highest precedence first):

1. `--env-file <path>` passed to the CLI,
2. `.env` in the repo root (auto-loaded by `docker compose` via `env_file`),
3. the process environment.

| Variable | Meaning |
|----------|---------|
| `PLEXUS_API_KEY` | Key for the self-hosted Plexus gateway (default target). |
| `PLEXUS_BASE_URL` | Base URL for the gateway, `https://plexus.nebulosa-bass.ts.net/v1` by default. |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | Alternative OpenAI-compatible creds (used when `PLEXUS_*` are unset; base URL falls back `OPENAI_BASE_URL` → `CUSTOM_BASE_URL` → `PLEXUS_BASE_URL` → `https://api.openai.com/v1`). |
| `ANTHROPIC_API_KEY` | Needed only for `anthropic/…` models. |
| `GEMINI_API_KEY` | Needed only for `gemini/…` models. |
| `GROQ_API_KEY` | Optional stub (reserve for Groq-hosted OpenAI-compatible endpoints). |
| `HONCHOBENCH_MODELS` | Default comma-separated model list when `--models` isn't passed. |

### About "no Discord markdown tables"

The CLI's chat replies won't render Discord-style markdown tables — but that
restriction applies **only to chat/model output**, not to anything you read. The
report files in `./reports/*.md` are plain GitHub-flavored Markdown with standard
tables, and `./reports/*.csv` are plain CSVs — both display fine anywhere.

---

## Output & reports

After a run, `./reports` contains, per role:

- `README.md` — summary index.
- `<role>.md` — per-model aggregate tables + per-conversation detail + raw sample.
- `<role>.csv` — `model,conversation_id,metric,value` rows for machines.

Reports are written **inside the container** to `/app/reports`, which is
bind-mounted to `./reports` on your host, so you never need to copy files out.

---

## Notes

- **Cost/scale:** full default run ≈ 100–530 LLM calls on the cheap Plexus alias
  (a few dollars at most; ~free on local models). Use `make bench LIMIT=1`
  to smoke-test first.
- **Reproducibility:** for a fully pinned rebuild use
  `docker build --no-cache .` (pins the latest compatible releases of the
  ranges in `requirements.txt`).
- **IRL mode:** `IRL_EVAL_DESIGN.md` details running the real honcho server +
  Postgres/pgvector for full retrieval fidelity; the containerized harness here is
  the fast standalone path.
- **Runs stay in-network:** the harness only talks to your configured base URL;
  there's no telemetry/phone-home.

## Project layout

```
Dockerfile  docker-compose.yml  Makefile  bench  requirements.txt
.env.example  README.md
data/dataset/{conversations,labels}.json
src/honchobench/{cli,roles,prompts,clients,store,tool_loop,scoring,report}.py
reports/                 # generated .md + .csv (host-mounted)
scripts/quickstart.sh
```
