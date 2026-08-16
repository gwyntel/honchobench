# Honcho Bench Runner — Portable Implementation Specification

Target: a **portable, CI-runnable benchmark runner** living in the `honchobench` repo, executing on a GitHub Actions runner, that:

1. reads a list of Plexus **alias names** from an env var,
2. for each alias, points **all** of honcho's per-role model configs at that alias via the Plexus gateway (`https://plexus.nebulosa-bass.ts.net/v1`),
3. runs the vendored honcho bench suite **plus** the local Enron dataset through the running honcho stack,
4. emits per-alias numbers as JSON + markdown.

All contracts below are **read from source** at `_vendor/honcho` (commit `4448979`, v3.0.12). Line numbers are cited. Nothing is paraphrased.

---

## 0. Critical ground truth (read from source — read these first)

### 0.1 There is NO `run_tests.py` in this vendored commit

The README (`_vendor/honcho/tests/bench/README.md`) documents a `run_tests.py` "test runner" with flags `--tests-dir`, `--test`, `--honcho-url`, `--anthropic-api-key`, `--timeout` (README.md:176-244) and a "Test JSON Format" (README.md:207-236). **That file does not exist in this vendored clone.** Verified by `git ls-files` and `ls` — the `tests/bench/` directory contains only:

```
README.md, .gitignore, harness.py, runner_common.py,
longmem.py, longmem_common.py, longmem_baseline.py,
locomo.py,  locomo_common.py,  locomo_baseline.py, locomo_summary.py,
beam.py,    beam_common.py,    beam_baseline.py,
oolong.py,  oolong_common.py,
molecular.py, coverage.py, calculate_expected_events.py
```

There is also **no `tests/` subdirectory** (where run_tests.json files would live), and no tracked data dirs. `tests/bench/.gitignore` (lines 1-7) ignores `longmemeval_data`, `eval_results`, `perf_metrics`, `beam_data`, `obexeval_data`, `locomo_data`, `oolongeval_data`.

**Consequence:** This spec treats the six runners that actually exist (**longmem, locomo, beam, oolong, molecular, coverage**) as the suite. The `run_tests.py` JSON schema is documented below **only** from the README for completeness, and is **NOT runnable** — the target "full suite" is composed of longmem + locomo + beam + oolong + molecular + coverage. (See §7 data-availability table.)

### 0.2 Harness is headless and CI-safe, but has quirks

`harness.py` (`python tests/bench/harness.py`) runs the full stack headlessly: Dockerized Postgres + Redis, Alembic provisioning, FastAPI (`uvicorn src.main:app` on port 8000), deriver (`python -m src.deriver`). See §1.1.

---

## 1. The harness: how to launch headless in CI

### 1.1 Invocation and CLI

Module running form (README.md:19): `python tests/bench/harness.py` — or, from the Honcho project root, `python -m tests.bench.harness` (used in the module docstrings, e.g. longmem.py:25).

`harness.py:main()` (lines 1100-1203) arg list:

| Flag | Default | Source |
|------|---------|--------|
| `--port` | `5433` | harness.py:1116-1120 |
| `--api-port` | `8000` | harness.py:1123-1127 |
| `--redis-port` | `6379` | harness.py:1130-1134 |
| `--pool-size` | `1` | harness.py:1137-1141 |
| `--project-root` | `Path.cwd()` | harness.py:1144-1148 |

`HonchoHarness.run()` (lines 822-918) is the orchestrator. Order: backup `.env`, copy `tests/bench/.env` → project root `.env` (831-834), build temp docker-compose (836), `docker compose up -d database` (231-246), `up -d redis` (261-276), wait for both, `init_cache`, `scripts/provision_db.py` (402-425), `verify_empty_database` (427-466), start FastAPI + deriver, then block in a monitor loop.

### 1.2 Headless / TTY answer — **no TTY needed, no Ctrl+C needed**

- The harness never reads STDIN. It blocks in an infinite `while True:` monitor loop (904-911) that returns when any process dies. **Termination is via `process.terminate()`/`kill()` from the parent, or `SIGINT`** (handled at 913-914, with cleanup guaranteed in the `finally: await self.cleanup()` at 917-918). In CI, run it as a **background process group** and kill it in a `finally:` block / step.
- Auto-cleanup: `cleanup()` (749-820) terminates the API + deriver `Popen`s (756-765), `docker compose down --volumes --remove-orphans` (777-791), `rm -rf` the temp dir (803-810), closes cache, restores `.env`.
- **Unstructured `.env` handling is a CI hazard:** it **renames** project-root `.env` → `.env.backup.<ts>` (170-179) and **copies** `tests/bench/.env` into the project root (831-834). That `tests/bench/.env` is a secret-bearing untracked file; in CI you must **create it** before invoking the harness, or the copy at line 832 throws `FileNotFoundError`. **Plan: render `tests/bench/.env` from the workflow with the Plexus + judge keys.** (The file is currently present locally but git-untracked; do not rely on it being in a source checkout.)
- Ports are configurable via `--port/--api-port/--redis-port` — required if a shared runner already uses 5433/8000/6379.

### 1.3 Env vars the harness itself sets

`get_database_env_vars()` (193-204) sets exactly three, in `os.environ` **and** in each subprocess env:
- `DB_CONNECTION_URI` = `postgresql+psycopg://postgres:postgres@localhost:{db_port}/postgres`
- `CACHE_ENABLED` = `"true"`
- `CACHE_URL` = `redis://localhost:{redis_port}/0`

The deriver is launched with the extra env var `DERIVER_FLUSH_ENABLED=true` (line 522) so messages process immediately without batch-threshold delay.

Everything else (per-role model config, judge API keys, embedding config) is **inherited from the harness's own environment** — i.e. you export them on the process that runs `harness.py`, and the FastAPI + deriver children inherit them (the Popen env is `os.environ.copy()` + the three DB vars, 480-504, 519-533). **This is the injection point for the per-alias role config: set them on the harness process (or the shell that launches it).**

### 1.4 Can it run in CI without a TTY?

**Yes.** It is designed for this (README.md:164-172 "Integration with CI/CD": "Use temporary directories for isolation … Clean up all resources on exit … Use environment variables for configuration (no file conflicts)"). Constraints: (a) Docker + Docker Compose v2 available; (b) `scripts/provision_db.py` exists; (c) a `tests/bench/.env` exists; (d) `uv sync` done (deps incl. `uvicorn`, `psycopg`, `redis`, `pyarrow` for oolong, `scipy` for beam's Kendall tau).

### 1.5 Ready-check

`wait_for_fastapi()` (574-627) polls `GET http://localhost:{api_port}/docs` for HTTP 200 (593-595). After the harness prints the banner (885-893) the API is up. Runners connect to `http://localhost:8000` by default (`--base-api-port 8000`, runner_common.py:113-117 ⟹ `_get_honcho_url` returns `http://localhost:{port}`, runner_common.py:799-805).

---

## 2. Runner contracts (per-runner)

All `BaseRunner` based runners (`longmem`, `locomo`, `beam`, `oolong`) share:
- `--base-url` / `--api-key` / `--base-api-port` / `--pool-size` / `--timeout` / `--batch-size` / `--json-output` / `--cleanup-workspace` / `--use-get-context` / `--reasoning-level` / `--skip-dream` / `--max-concurrent` (runner_common.py:91-177).
- `RunnerConfig.from_args` (runner_common.py:56-76) maps CLI → config.
- `--json-output <path>` controls where the JSON report goes; otherwise each runner writes a timestamped file under `tests/bench/eval_results/`.
- `--reasoning-level {minimal,low,medium,high,max}` (runner_common.py:32-33, 158-164).
- Honcho client is created with `Honcho(workspace_id=…, base_url=honcho_url, api_key=config.api_key)` (runner_common.py:807-813).
- Every runner waits for the deriver queue to empty `_wait_for_queue_empty` (819-841), triggers **dreams** `schedule_dream(observer, session, observer)` (688-693), then executes questions.

### 2.1 longmem (`python -m tests.bench.longmem`)

Docstring invocation (longmem.py:13-55), `--test-file` required (712-717). LongMemEval-specific flags: `--anthropic-api-key` (723-727), `--merge-sessions` (729-733), `--test-count` (735-739), `--question-id` (741-745). `main()` at 696-780; default timeout overridden to `10000`s (765).

**Judge model:** `judge_response(...)` in `longmem_common.py:234-295` calls **`model="gpt-4o-2024-08-06"`** (line 266) through the **OpenAI client** built by `create_openai_client()` (longmem.py:167). That uses `OPENAI_API_KEY` (see §3). Docstring note: "Judge is GPT-4o (per LongMemEval paper)" (longmem.py:53).

**Question execution:** either dialectic `.chat` to the `user` or `assistant` peer (longmem.py:480-489), or with `--use-get-context` uses `session.aio.context(summary=True, peer_target=…)` fed to Anthropic `claude-sonnet-4-5` (469-472).

**JSON output schema** (`summary` dict, longmem.py:644-683): `{metadata{test_file,execution_timestamp,runner_version,base_api_port,pool_size,timeout_seconds,reasoning_level,deriver_settings,dialectic_settings,dream_settings,summary_settings}, summary_statistics{total_questions,passed,failed,success_rate_percent,statistics_by_type}, timing{...}, token_efficiency{...}, detailed_results[{question_id,question_type,workspace_id,passed,duration_seconds,start_time,end_time,error,query_executed}]}`.

**Data format** (`longmem_common.py:load_test_file`, 126-136 — `json.load` returns a **list of question dicts**):

```json
[
  {
    "question_id": "q123",          // may contain "_abs" → abstention prompt branch (longmem_common.py:161)
    "question_type": "single-session-user|single-session-assistant|multi-session|temporal-reasoning|knowledge-update|single-session-preference",
    "question": "…",
    "answer": "expected answer or rubric string",
    "haystack_sessions": [ [ {"role":"user|assistant","content":"…"}, … ], … ],  // one list per session
    "haystack_session_ids": ["…", …],   // used as session ids (longmem.py:189-193)
    "haystack_dates": ["2023/05/20 (Sat) 02:21", …],  // parsed by parse_longmemeval_date (longmem_common.py:97-123)
    "question_date": "…"               // optional, prefixed "[date] " onto the question (longmem.py:428-430)
  }
]
```

Messages >25000 chars are chunked by 25000 (longmem.py:268-293, 350-365). Sessions are created per haystack session (or merged with `--merge-sessions`).

### 2.2 locomo (`python -m tests.bench.locomo`)

Docstring invocation (locomo.py:40-41): `python -m tests.bench.locomo --data-file tests/bench/locomo_data/locomo10.json`. `--data-file` required (525-530). Locomo flags: `--anthropic-api-key` (536-540), `--sample-id` (542-546), `--test-count` (548-552), `--question-count` (554-558).

**Judge model:** `judge_response` in `locomo_common.py:371-458` calls **`model="gpt-4o-mini"`** (line 401) through the OpenAI client (`create_openai_client()`, locomo.py:206) — an evidence-conditional "SUFFICIENT / INSUFFICIENT" JSON judge (system prompt at 286-348).

**Question execution:** dialectic `.chat` to whichever speaker the question targets, `determine_question_target` (locomo.py:136-166), or `--use-get-context` → Anthropic `claude-sonnet-4-5` (locomo.py:391-400).

**Data format** (`load_locomo_data`, locomo_common.py:100-111; extraction `extract_sessions`, 143-171). Required shape of a conversation item:

```json
{
  "sample_id": "sample_0",
  "conversation": {
    "speaker_a": "Caroline",
    "speaker_b": "Melanie",
    "session_1": [ {"speaker":"Caroline","text":"…","dia_id":"D1:3","blip_caption":null,"img_url":[],"query":null}, … ],
    "session_1_date_time": "1:56 pm on 8 May, 2023",   // parsed: "%I:%M %p %d %B, %Y" (114-141)
    "session_2": […], "session_2_date_time": "…", …    // any number
  },
  "qa": [
    {"question":"…","answer":"…","category":1,"evidence":["D1:3","D2:8"]}
  ]
}
```

- `category` integer → `CATEGORY_NAMES` (locomo_common.py:33-39): 1=single_hop, 2=multi_hop, 3=temporal, 4=commonsense, 5=adversarial. `filter_questions(..., exclude_adversarial=True, ...)` drops category 5 by default (locomo.py:356-360; locomo_common.py:268-269).
- `evidence` `dia_id` refs are resolved against `msg["dia_id"]` across all sessions (locomo_common.py:209-244) to hand the judge the evidence block (sufficiency-lean).
- Messages run through `format_message_with_image` (locomo.py:104-133) → content + optional `metadata`.

### 2.3 beam (`python -m tests.bench.beam`)

Docstring invocation (beam.py:43): `python -m tests.bench.beam --context-length 100K`. Flags: `--context-length` (480-486, choices `1K/100K/500K/1M/10M`), `--conversation-ids` comma-separated (488-492). Data dir is **hardcoded**: `data_dir = Path(__file__).parent / "beam_data"` with explicit failure if absent (beam.py:506-509).

**Judge model — env-driven, OpenAI-compatible transport:**
```python
openrouter_base_url = os.getenv("LLM_OPENAI_COMPATIBLE_BASE_URL", "https://openrouter.ai/api/v1")   # beam.py:134-135
self.openrouter_client = create_openai_client(base_url=openrouter_base_url, env_key_name="LLM_OPENAI_COMPATIBLE_API_KEY")  # 137-140
self.judge_model = os.getenv("BEAM_JUDGE_MODEL", "anthropic/claude-sonnet-4.5")                     # 143-145
```
So the beam judge is driven by exactly two env vars: **`LLM_OPENAI_COMPATIBLE_BASE_URL`** and **`LLM_OPENAI_COMPATIBLE_API_KEY`**, with the model overridable via **`BEAM_JUDGE_MODEL`**.

**Judges used:** `judge_nugget_based` (beam_common.py:168-371, tool-call JSON scoring each `rubric` nugget 0/0.5/1, `passed = score >= 0.5`) for all abilities **except** `event_ordering`, which uses `judge_event_ordering` (396+, Kendall tau-b via `scipy.stats.kendalltau`, needs `scipy`). Both call the OpenRouter-compatible client with the same `judge_model`.

**Data layout** (`load_conversation`, beam_common.py:78-104; `list_conversations`, 107-121):
```
beam_data/{context_length}/{conversation_id}/
    chat.json                       # {"turns": [[ {role, content}, … ]]} list-of-batches
    probing_questions/probing_questions.json   # {ability_name: [ {question, rubric:[…], answer|ideal_response|ideal_answer}, … ]}
```
- Conversation IDs are digit-named subdirs (`list_conversations` returns those whose `d.name.isdigit()`, 119-121).
- `chat.json` standard structure: a top-level JSON **list** of batches, each batch is a dict that may contain a `.turns` list of lists of `{role, content}` turns; the **10M** variant instead has keys `plan-1…plan-N` (beam.py:206-224; beam_common.py:138-153).
- Abilities = keys of `probing_questions.json`, one of the 10: `abstention`, `contradiction_resolution`, `event_ordering`, `information_extraction`, `instruction_following`, `knowledge_update`, `multi_session_reasoning`, `preference_following`, `summarization`, `temporal_reasoning` (beam.py:1-25).
- **`instruction_following` is special-cased**: it always uses `get_context` + OpenRouter `judge_model` (beam.py:282, 288-314), never the dialectic chat path.

**JDBC-style gotchas for the Enron adapter:** the beam runner reads a directory of per-conversation subdirs with a preset layout; the Enron data (19 flat conversations) will need conversion into exactly this layout (see §6.5).

### 2.4 oolong (`python -m tests.bench.oolong`)

Invocation (README.md:35-46): `--variant synth|real --data-dir <path>`. Flags: `--variant` (459-465, default `synth`), `--data-dir` required (466-471), `--split test|validation` (472-477, default `test`), `--max-examples` (479-483), `--context-size` (485-493), `--max-context-len` (494-498), `--labels` (501-504), `--min-context-len` (506-510, default `1024`), `--context-window-id` (512-516), `--no-merge-sessions` (518-524). `--use-get-context` is **explicitly rejected** (535-537).

**Judge:** no external judge model — **scoring is rule-based** against the gold `answer`:
- Synth: `score_synth_response` (oolong_common.py:494-580): exact-string 1.0, `0.75^|gold-model|` numeric partial credit, exact-date match 1.0, else 0. `passed = score >= 0.99` (oolong.py:331).
- Real: `score_real_response` (583+): `\boxed{}` extraction, numeric partial credit / string exact / Jaccard list-overlap.

**Data layout:**
- Synth: `data_dir/data/{split}-*.parquet` (oolong_common.py:174-184), streamed via `pyarrow.parquet` — rows have at least `id`, `context_window_id`, `context_len`, `question`, `answer`, `answer_type`, `task_group`, `dataset`, `context_window_text` (and `context_window_text_with_labels` when `--labels`, oolong.py:213-214, 287-288).
- Real: `data_dir/dnd/{split}.jsonl` (oolong_common.py:257-265) — same field surface, `question_type` used as `task_group` (oolong.py:281-282).

**Context ingestion:** `parse_synth_context_messages` (oolong_common.py:307-360) parses each `context_window_text` line `Date: … || User: … || Instance: <text> [Label: …]` into a message with `metadata{date,user_id,label}`; `parse_real_context_messages` (363-425) splits a D&D transcript on `Speaker: text` lines. Non-merged mode chunks the context into sessions of 200 messages (oolong.py:227-239).

### 2.5 molecular (`python -m tests.bench.molecular`) — no Honcho needed

**This is a trace-*evaluator*, not a runner.** Docstring (molecular.py:45-47): `python -m tests.bench.molecular --traces path/to/traces.jsonl`. CLI (1064-1079): `--traces <json|jsonl>`, `--trace-dir`, `--output-dir` (default `tests/bench/molecular_results`), `--provider {anthropic,openai,openrouter}` (default `anthropic`), `--api-key`, `--model` (default `claude-sonnet-4-20250514`), `--verbose`, `--limit`, `--sample`, `--batch-size` (default 1). **It classifies honcho's extracted propositions**, not answers.

**Trace data contract** (runner_common.py):
- `load_traces` (294-341): legacy JSON-array **or** JSONL (one object/line), or `--trace-dir` globs `*.json`/`*.jsonl` (molecular.py:1104-1108).
- `extract_propositions` (344-362): reads **`trace["output"]["content"]["explicit"]`** → list of `{"content": "<claim>"}` — i.e. the **deriver's extraction output** captured after honcho processes the conversation.
- `extract_messages` (365-389): parses **`trace["input"]["prompt"]`** for a `<messages>…</messages>` block, lines `speaker: text`.
- `extract_peer_name` (392-402): reads the subject predicate from propositions (`"<X> name is …"`).
- `extract_conversation_id` (405-407): `trace["conversation_id"]` or `trace["id"]` or `trace_{index:04d}`.

So the `judge.evaluate(props, msgs, peer, conv_id)` call (molecular.py:1141) runs on **honcho-produced traces**, and MolecularJudge (460+) scores decontextuality × minimality (geometric-mean "molecular score", 215-232). Output `{timestamp, model, count, duration, averages{molecular,decontextuality,minimality}, classification_totals{…}, results:[MolecularReport.to_dict()]}` (1173-1194; report schema 263-301).

**Judge model selection:** `--provider anthropic` → `create_anthropic_client(args.api_key)` ⇒ `LLM_ANTHROPIC_API_KEY`; `--provider openrouter` → `create_openai_client(base_url="https://openrouter.ai/api/v1", env_key_name="OPENROUTER_API_KEY")`; `--provider openai` → `create_openai_client(args.api_key)` ⇒ `OPENAI_API_KEY` (molecular.py:1082-1096). `--model` sets the judge model id (default `claude-sonnet-4-20250514`, 1073).

### 2.6 coverage (`python -m tests.bench.coverage`) — no Honcho needed

Same trace-evaluator family. CLI (coverage.py:1234-1251): `--traces`, `--trace-dir`, `--output-dir`, `--provider {anthropic,openai,openrouter}` (default `anthropic`), `--api-key`, `--model` (default `claude-sonnet-4-20250514`), `--no-qa`, `--verbose`, `--limit`. Judge = `CoverageJudge` (402+) which, like MolecularJudge, uses `create_anthropic_client` / `create_openai_client` (coverage.py:1258-1268). It extracts gold facts then matches them against the proposition set via `extract_propositions` (imported in coverage.py:64) — the same `output.content.explicit` trace contract as molecular. Metrics: recall, partial_recall, weighted_recall, precision, f1, densities, optional QA coverage. Report schema `CoverageReport.to_dict()` (194-254).

### 2.7 `run_tests.py` — README-only, NOT present

For completeness, the README test-JSON schema (README.md:211-236):
```json
{
  "sessions": { "session1": { "messages": [ {"peer":"alice","content":"..."}, ... ] } },
  "queries": [ {"query":"How is Bob doing?","expected_response":"Good","session":"session1","peer":"alice"} ]
}
```
Flags (README.md:240-244): `--tests-dir` (default `tests/bench/tests`), `--test`, `--honcho-url` (default `http://localhost:8000`), `--anthropic-api-key` (defaults to `LLM_ANTHROPIC_API_KEY`), `--timeout` (default 60 s). Comment on the judge: "# Test judge uses claude 3.5 sonnet" (README.md:193). **Neither the file nor the default `tests/bench/tests` dir exists here.**

---

## 3. Judge-model configuration contract (env vars & API keys)

| Runner | Judge model (hardcoded / env) | Transport / key env var | Source |
|--------|-------------------------------|--------------------------|--------|
| **longmem** | `gpt-4o-2024-08-06` (hardcoded) | OpenAI — `OPENAI_API_KEY` (`create_openai_client()`, default `env_key_name="OPENAI_API_KEY"`, runner_common.py:250,266-272) | longmem_common.py:266 |
| **locomo** | `gpt-4o-mini` (hardcoded) | OpenAI — `OPENAI_API_KEY` | locomo_common.py:401 |
| **beam** | `BEAM_JUDGE_MODEL` env (default `anthropic/claude-sonnet-4.5`) | OpenAI-compatible — `LLM_OPENAI_COMPATIBLE_BASE_URL` + `LLM_OPENAI_COMPATIBLE_API_KEY` | beam.py:134-145 |
| **oolong** | none (rule-based scoring) | n/a | oolong_common.py:494-614 |
| **molecular** | `--model` (default `claude-sonnet-4-20250514`) | `--provider` ⇒ `LLM_ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` | molecular.py:1070-1096 |
| **coverage** | `--model` (default `claude-sonnet-4-20250514`) | same three via `--provider` | coverage.py:1244-1268 |

**Anthropic always comes from `LLM_ANTHROPIC_API_KEY`** (`create_anthropic_client`, runner_common.py:225-244: `env_key = os.getenv("LLM_ANTHROPIC_API_KEY")`, raises if unset). OpenAI default from `OPENAI_API_KEY`; `--api-key` overrides both (runner_common.py:101-110, 238-239).

**Judging note for the Plexus runner:** longmem and locomo **hardcode** `gpt-4o-2024-08-06` / `gpt-4o-mini` strings and call through the OpenAI transport. To score over Plexus, set `OPENAI_API_KEY` to the Plexus bench key **and** `OPENAI_BASE_URL` to the Plexus gateway **only if** honcho's top-level `OPENAI_BASE_URL` (src/config.py:766) is honored by the OpenAI SDK client `create_openai_client` builds. It is **not**: `create_openai_client` reads no base URL unless `--base-url`-style arg is passed (runner_common.py:270-272 builds `AsyncOpenAI(api_key=key, base_url=base_url)` **only** when `base_url` is truthy, and these judge call sites pass none). **Therefore longmem/locomo judges CANNOT be pointed at Plexus without source changes** — they will hit `api.openai.com`. Practical paths:
1. **Leave the judge on the real OpenAI API** (set `OPENAI_API_KEY` to a normal key) — zero source change, matches the paper methodology exactly. The *model-under-test* is what you vary per alias.
2. To fully route the judge through Plexus, patch longmem.py:167 & locomo.py:206 to pass `base_url=os.getenv("LLM_OPENAI_COMPATIBLE_BASE_URL")` to `create_openai_client` (and keep model names mappable to Plexus aliases). This is the only option that keeps ALL cost on Plexus.

Beam is the **only** judge that is already Plexus-ready out of the box (env-driven base URL + key).

---

## 4. Per-role honcho env-var block (pointing the model-under-test at a Plexus alias)

Source-verified against `_vendor/honcho/src/config.py`:
- `ModelOverrideSettings` (107-132) = `api_key`, `api_key_env`, `base_url`, `provider_params`. `overrides` is a field of the model config (229, 275, 393).
- Env vars are parsed with pydantic `env_nested_delimiter="__"` (see docstring at 574-579: "an env var like \`DERIVER_MODEL_CONFIG__THINKING_BUDGET_TOKENS=2048\` … produces {\"MODEL_CONFIG\": {\"THINKING_BUDGET_TOKENS\": 2048}}"). Settings classes are prefixed (`.env_prefix`), so the path is `MODEL_CONFIG__OVERRIDES__<field>`.
- `_resolve_secret(configured.overrides.api_key, configured.overrides.api_key_env)` (546-549) means: if `api_key_env` names a process env var, that var holds the literal key; otherwise use `api_key` directly.

**The exact per-role env block for one alias `ALIAS` (transport `openai`):**

```
# Honcho → Plexus gateway for the MODEL UNDER TEST (subject model)
DERIVER_MODEL_CONFIG__MODEL=ALIAS
DERIVER_MODEL_CONFIG__TRANSPORT=openai
DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=https://plexus.nebulosa-bass.ts.net/v1
DERIVER_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=PLEXUS_BENCH_API_KEY

SUMMARY_MODEL_CONFIG__MODEL=ALIAS
SUMMARY_MODEL_CONFIG__TRANSPORT=openai
SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL=https://plexus.nebulosa-bass.ts.net/v1
SUMMARY_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=PLEXUS_BENCH_API_KEY

DIALECTIC_LEVELS__minimal__MODEL_CONFIG__MODEL=ALIAS      # and low, medium, high, max
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__OVERRIDES__BASE_URL=https://plexus.nebulosa-bass.ts.net/v1
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__OVERRIDES__API_KEY_ENV=PLEXUS_BENCH_API_KEY
# … repeat for low / medium / high / max …

EMBEDDING_MODEL_CONFIG__MODEL=nomic-embed-text          # keep embeddings off alias (cheap/free path)
EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=…            # embed endpoint if proxied, else defaults
# Hard requirement per context: EMBEDDING_VECTOR_DIMENSIONS=768 (nomic), and EMBEDDING_MODEL_CONFIG__* set explicitly.
```

Notes (from the confirmed context + source):
- **Honcho does NOT inherit top-level `MODEL_CONFIG__*` into the per-role blocks.** Each role (`DERIVER_…`, `SUMMARY_…`, `DIALECTIC_LEVELS__{minimal..max}__MODEL_CONFIG__…`) defaults to `gpt-5.4-mini@api.openai.com` unless set explicitly. The runner **must** set every role for every alias. (The `DIALECTIC_LEVELS__<level>__MODEL_CONFIG__*` merge path is config.py:1034, 1092-1111.)
- `_fill_defaults_for_nested_field` (567-614) will back-fill missing `model`/`transport` from defaults on **partial** overrides — but do not rely on it; set `MODEL` + `TRANSPORT` + both `OVERRIDES` explicitly.
- The benchmark target is **the Plexus gateway using ALIAS NAMES as the model id** (e.g. `ModelA`, `provocative-qwen3.6-35b`), with a dedicated bench key exported as `PLEXUS_BENCH_API_KEY`. Point `OVERIDES__API_KEY_ENV=PLEXUS_BENCH_API_KEY` so the literal key never lands in a model-config field.
- `DERIVER_MODEL_CONFIG__*`, `SUMMARY_MODEL_CONFIG__*`, `EMBEDDING_MODEL_CONFIG__*` are the prefixed setting roots used by `src/deriver`, `src/summarizer`, and embeds respectively; the dreamer is fed from `DERIVER`-scoped config (the harness prints the full resolved settings via `print_honcho_config`, harness.py:629-747, so a smoke run can dump the exact model each role resolved to).

---

## 5. Recommended repo layout & GitHub Actions workflow

### 5.1 Repo layout under `honchobench/`

```
honchobench/
├── _vendor/honcho/               # vendored honcho (unchanged)
├── data/dataset/                 # conversations.json (19), labels.json (8)
├── bench/                        # NEW — portable runner (not inside _vendor)
│   ├── run_bench.py              # alias loop + orchestration (see 5.2)
│   ├── enron_adapters.py         # JSON→ each runner's format (§6 adapters)
│   ├── build_harness_env.py      # renders tests/bench/.env from workflow env
│   ├── reports.py                # per-alias JSON + markdown emitters
│   └── smoke_targets.py          # alias opt-in / gating helpers
├── .github/workflows/bench.yml
└── docs/BENCH_RUNNER_SPEC.md     # this document
```

### 5.2 Alias-loop orchestration (pseudocode, `run_bench.py`)

```
for alias in os.environ["BENCH_ALIASES"].split(","):     # e.g. "provocative-qwen3.6-35b,ModelA,ModelB"
    export per-role block for <alias> (see §4)               # on the harness subprocess env
    spawn harness:  python tests/bench/harness.py            # background, capture pid
    wait for http://localhost:8000/docs (poll)
    run suite against http://localhost:8000:
        longmem --test-file data/longmem/longmemeval_oracle.json --json-output reports/<alias>/longmem.json
        locomo  --data-file data/locomo/locomo10.json         --json-output reports/<alias>/locomo.json
        beam    --context-length 100K                         --json-output reports/<alias>/beam.json
        oolong  --variant synth --data-dir data/oolong-synth  --json-output reports/<alias>/oolong.json   # if data present (see §7)
        (molecular/coverage are post-hoc evaluators on honcho TRACES — see §6.4)
    terminate harness; kill docker compose project; wait
finally:
    reports.py → aggregate per-alias JSON + markdown into reports/SUMMARY_<alias>.md
    upload reports/ as a workflow artifact
```

Key structure points:
- **The alias loop wraps a full harness lifecycle** (start → bench → kill) because honcho reads role model config at process start; aliasing per-run means a fresh stack per alias. Cleanup is guaranteed in `finally`.
- **Dataset is ingested once** (checked out / reconstructed into `data/` at the job level), then converted **per-runner** by `enron_adapters.py` (it does not depend on the alias). Only the per-role env block changes between aliases.
- **CI smoke vs paid aliases:** define `BENCH_ALIASES` defaulting to only `provocative-qwen3.6-35b` (the free alias, used as the smoke target). Paid aliases run **only** when `BENCH_RUN_PAID=1` is set in the workflow dispatch/env — `run_bench.py` refuses to include any alias other than the free one unless `BENCH_RUN_PAID=1`. Artifacts/reports are uploaded after every alias; when `BENCH_RUN_PAID != 1`, only the smoke alias is scored.

### 5.3 GitHub Actions workflow sketch (`.github/workflows/bench.yml`)

```yaml
name: bench
on:
  workflow_dispatch:        # manual; optionally input: BENCH_ALIASES, BENCH_RUN_PAID
jobs:
  bench:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - uses: astral-sh/setup-uv@v5
      - name: Sync honcho deps
        run: cd _vendor/honcho && uv sync
      - name: Render bench .env         # harness copies this to project root (§1.2)
        env:
          PLEXUS_BENCH_API_KEY: ${{ secrets.PLEXUS_BENCH_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}          # longmem/locomo judge (§3)
          LLM_OPENAI_COMPATIBLE_API_KEY: ${{ secrets.PLEXUS_BENCH_API_KEY }}
          LLM_OPENAI_COMPATIBLE_BASE_URL: https://plexus.nebulosa-bass.ts.net/v1
          BEAM_JUDGE_MODEL: anthropic/claude-sonnet-4.5
        run: python bench/build_harness_env.py      # writes _vendor/honcho/tests/bench/.env
      - name: Reconstruct datasets
        run: python bench/enron_adapters.py          # builds data/longmem,data/locomo,data/beam data dirs (see §6)
      - name: Run per-alias bench
        env:
          BENCH_ALIASES: ${{ inputs.BENCH_ALIASES || 'provocative-qwen3.6-35b' }}
          BENCH_RUN_PAID: ${{ inputs.BENCH_RUN_PAID || '0' }}
        timeout-minutes: 300
        run: python bench/run_bench.py
      - uses: actions/upload-artifact@v4
        with:
          name: bench-reports
          path: reports/**/*
```

Runner prerequisites: Docker (for the harness DB/Redis), network to `https://plexus.nebulosa-bass.ts.net/v1`, and (`scipy` for beam's Kendall tau; `pyarrow` for oolong).

---

## 6. Enron data → runner mapping

### 6.1 Source data shapes (verified)

- `data/dataset/conversations.json` — **array of 19**, each `{ "conversation_id", "source":"enron", "messages":[{"sender","text"}], "target_peer" }`.
- `data/dataset/labels.json` — **object keyed by conversation_id** (8 labeled threads), each label doc:
  ```json
  {
    "explicit_facts": [ "<atomic claim string>", … ],
    "qa": [ {"q": "…", "a": "…", "evidence": "<source quote>"}, … ],
    "deductive": [ "<reasoned inference>", … ],
    "inductive": [ "<character-trait inference>", … ],
    "short_summary": "…one paragraph…",
    "long_summary": "…longer…"
  }
  ```

### 6.2 Field → runner mapping

| Label field | Feeds into | Runner | Adapter/transform required |
|-------------|-----------|--------|----------------------------|
| `qa[]` (`{q,a,evidence}`) | **gold question/answer pairs** | **longmem** (question + answer + haystack) and/or **locomo** (`qa[]` with `category`+`evidence`) | Map each `qa` → one LongMemEval question (`question= q`, `answer= a`, `haystack_sessions` from the conversation messages, synthetic `question_type`, `answer`=a). For locomo: `question`/`answer`, plus an Enron-equivalent `category` and evidence `dia_id`s referencing the source message. **No mapping to any honcho runner field named `explicit_facts`** |
| `explicit_facts[]` | **gold extraction** (recall target) | **coverage** (as a custom gold set) | coverage's gold facts are normally produced by `CoverageJudge.extract_gold_facts` at runtime. To assert the Enron facts, either (a) run coverage as-is and diff its extracted `gold_facts`; or (b) pre-seed `coverage` gold (needs a small source patch — coverage.py has no "gold from file" option). `explicit_facts` are *not* consumed by any runner today. |
| `deductive[]` | evaluation of honcho's dream/deduction output | **longmem `knowledge-update`/`multi-session` style questions** (closest) or a **custom evaluator over `conclusions`** | No native runner consumes a "deductive inference" label; coerce each entry into a question whose expected answer is the deduction. |
| `inductive[]` | same as `deductive` (no native slot) | none directly; closest = **longmem `single-session-user` / locomo commonsense** | coerce to a question. |
| `short_summary` | summary-quality evaluation | **none of the 6** (no runner scores summaries; the dialectic/`summarization` ability in beam answers *questions*, not summaries) | closest = beam `summarization` probing question constructed from the summary text, or a custom summary judge. |
| `long_summary` | same as `short_summary` | none native | same. |

### 6.3 The real constraint: molecular & coverage need *honcho traces*, not labels

`molecular.py`/`coverage.py` read `trace["output"]["content"]["explicit"]` + `trace["input"]["prompt"]` (runner_common.py:344-389) — i.e. **honcho's own extraction output**, not ground-truth labels. To run them on Enron you must:
1. ingest each Enron conversation into honcho,
2. capture the deriver's per-conversation trace dict (input prompt + output.content.explicit),
3. serialize to JSONL (`data/traces/enron_traces.jsonl`) and pass to both evaluators.

So the Enron **molecular/coverage** scores measure honcho's **fact quality/coverage** under each alias. The labels can be diffed *qualitatively* against `extract_propositions` output, but they are not the evaluator's input.

### 6.4 Adapter functions (`bench/enron_adapters.py`)

- `to_longmem(labels, conversations)` → list of LongMemEval question dicts (see §2.1 schema). One output file `data/longmem/longmemeval_oracle.json`; use `conversation_id` in `question_id` to trace results.
- `to_locomo(labels, conversations)` → list with `sample_id`, `conversation{speaker_a=<target_peer>, speaker_b=<secondary>, session_1…N, session_N_date_time}` and `qa[]`; assign `category` by heuristic (1=single-hop, 3=temporal for date-bearing, etc.) and map `evidence` to a `dia_id`. `data/locomo/locomo10.json` only if a full conversation set exists.
- `to_beam(labels, conversations)` → beam `beam_data/100K/{id}/chat.json` + `probing_questions/probing_questions.json`, with `information_extraction` / `summarization` / `knowledge_update` / `temporal_reasoning` probing questions synthesized from `qa`/summaries, and `rubric` nuggets from `answer`.
- `capture_traces(alias, conversations)` → runs the ingest+deriver path once per conversation, dumps `data/traces/enron_traces.jsonl` for molecular/coverage.
- OOLONG-real: encode each Enron conversation as `dnd/{split}.jsonl` rows with `context_window_text` in `Speaker: …` form and a `question`/`answer` pair — but see §7 (needs a target answer per context, i.e. a label; OOLONG is long-context *aggregation* and ill-matched to email threads).

### 6.5 Ranking / practical per-runner recommendation

The Enron conversations are short (multi-message email threads), so:
- **longmem** (per-question, haystack) — best fit for `qa`/`deductive`/`inductive` as questions.
- **locomo** — good fit if evidence `dia_id`s are assigned; its multi-hop/temporal categories map to `qa`/`deductive`.
- **beam** — possible but poor fit (beam is tuned to 100K-token+ histories); only use with the `1K` context-length subset and a handful of synthesized probing questions.
- **oolong** — poor fit (aggregation over synthetic labeled context); OOLONG-real needs a truthful aggregate answer per context, which email threads mostly lack.
- **molecular/coverage** — best *complement*, but require the trace-capture step (6.3) and evaluate honcho's extraction, not answers.

---

## 7. Data availability table (can the 6 runners run TODAY against local Enron data?)

| Runner | Runs today on local Enron data? | Requires external download? | Notes / closest substitute |
|--------|----------------------------------|-----------------------------|-----------------------------|
| **longmem** | ✅ **Yes** (via adapter) | No (the standard LongMemEval dataset dir `longmemeval_data/` is gitignored and not present, but we synthesize questions from Enron `qa`) | Substitute = Enron-sourced questions. If you want the *official* LongMemEval set, source: `huggingface.co/datasets/xiaowu0162/longmemeval` (longmem.py:31) — gated/HF-auth only for the full set. |
| **locomo** | ✅ **Yes** (via adapter) | No (`locomo_data/locomo10.json` is gitignored/absent; we synthesize) | Substitute = Enron conversations → locomo format. Official set: `snap-research/locomo` (locomo.py:22). |
| **beam** | ⚠️ **Partially** (adapter builds the layout, but results are weak on short threads) | No (beam_data gitignored/absent; we fabricate the dir structure) | Only the `1K` subset is meaningful for 19 short threads. Official data = HuggingFace `Otter-Cloud/beam` family. |
| **oolong** | ⚠️ **Partially** (OOLONG-real adapter encodeable, but poor semantic fit) | Yes for OOLONG-synth: `data/{test,validation}-*.parquet` (oolong_common.py:174-184), source `allenai/oolong` HF parquet — large, often gated. | Substitute = Enron→`dnd/test.jsonl` OOLONG-real rows. Note `--use-get-context` unsupported (oolong.py:535-537). |
| **molecular** | ✅ **Yes** (offsets `--traces` on captured honcho traces) | No (capture traces from your own honcho run) | Judge model needs `LLM_ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`OPENROUTER_API_KEY` + `--model`. |
| **coverage** | ✅ **Yes** (same `--traces` path) | No (capture traces) | Same capture + judge-key requirements; no gold-from-file mode (would need a small patch to reuse `explicit_facts`). |

**Previously-gated/download-404 note:** the bench `.gitignore` explicitly excludes `longmemeval_data`, `locomo_data`, `beam_data`, `oolongeval_data` — none of these official datasets are vendored. LongMemEval (HF `xiaowu0162/longmemeval`) and OOLONG (HF `allenai/oolong`) are gated; LoCoMo lives upstream under `snap-research/locomo`; BEAM lives on HuggingFace. **For a self-contained, ungated CI run, synthesize all runner inputs from the local Enron `conversations.json` + `labels.json`** as described in §6.4 — that is the recommended default. Only the free-alias **smoke** run (which uses the free `provocative-qwen3.6-35b`) should run in CI by default; paid aliases opt in via `BENCH_RUN_PAID=1`.

---

## 8. Concrete per-runner invocation summary (exact commands + required env)

Run these against `http://localhost:8000` (harness up):

```bash
# longmem  (judge: gpt-4o-2024-08-06 via OPENAI_API_KEY)
python -m tests.bench.longmem --test-file data/longmem/longmemeval_oracle.json \
  --base-api-port 8000 --json-output reports/<alias>/longmem.json

# locomo   (judge: gpt-4o-mini via OPENAI_API_KEY)
python -m tests.bench.locomo --data-file data/locomo/locomo10.json \
  --base-api-port 8000 --json-output reports/<alias>/locomo.json

# beam     (judge: env-driven, LLM_OPENAI_COMPATIBLE_* + BEAM_JUDGE_MODEL)
python -m tests.bench.beam --context-length 100K \
  --base-api-port 8000 --json-output reports/<alias>/beam.json \
  # (unshipped: local source patch for --data-dir override, else place data at _vendor/honcho/tests/bench/beam_data)

# oolong   (no external judge)
python -m tests.bench.oolong --variant real --data-dir data/oolong-real \
  --base-api-port 8000 --json-output reports/<alias>/oolong.json   # sub-layout data/oolong-real/dnd/test.jsonl

# molecular / coverage (post-hoc trace evaluators; model-under-test is the JUDGE here, so per-alias you score with --model = alias via an OpenAI-compatible key)
python -m tests.bench.molecular --traces data/traces/enron_traces.jsonl \
  --provider openai --model <alias> --output-dir reports/<alias>/molecular
python -m tests.bench.coverage --traces data/traces/enron_traces.jsonl \
  --provider openai --model <alias> --output-dir reports/<alias>/coverage
```

Required process env (exported on the whole CI job):

```
export PLEXUS_BENCH_API_KEY=…                      # alias-key source
export OPENAI_API_KEY=…                            # longmem/locomo judge (real OpenAI, or set OPENAI_BASE_URL if Plexus can serve it)
export LLM_OPENAI_COMPATIBLE_BASE_URL=https://plexus.nebulosa-bass.ts.net/v1
export LLM_OPENAI_COMPATIBLE_API_KEY=$PLEXUS_BENCH_API_KEY
export BEAM_JUDGE_MODEL=anthropic/claude-sonnet-4.5
export OPENROUTER_API_KEY=…                        # only if molecular/coverage use --provider openrouter
export EMBEDDING_VECTOR_DIMENSIONS=768             # nomic requirement
# + the §4 per-alias role block for the current alias
```

---

## 9. Open items to flag to the parent agent

1. **`run_tests.py` is absent** in this vendored commit — the honest "full suite" is longmem + locomo + beam + oolong + molecular + coverage. If the parent expected `run_tests.py` to be the entry point, that must be reconciled (it may exist in a newer honcho tag, or was never merged).
2. **longmem/locomo judges cannot be pointed at Plexus without a source patch** (hardcoded model + no base-url plumbing through `create_openai_client`), so judge calls go to `api.openai.com` unless patched.
3. **beam needs a local `--data-dir` override** — the path is hardcoded to `tests/bench/beam_data` (beam.py:506-509); the runner either places data there or patches one line.
4. **`tests/bench/.env` must be pre-rendered** in CI (harness copies it into the project root).
5. **molecular/coverage evaluate honcho traces, not label files** — they require the trace-capture step; they do not consume `explicit_facts` as gold today (coverage would need a small patch to accept external gold facts).
6. Official datasets (LongMemEval, LoCoMo, BEAM, OOLONG) are gated/absent; the self-contained CI path is Enron-synthesized runner inputs.
