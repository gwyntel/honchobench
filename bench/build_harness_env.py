"""Render the honcho runtime `.env` for a given alias.

Honcho's config loads `.env` with `load_dotenv(override=True)` (src/config.py:23
and src/deriver/queue_manager.py:54), so the **.env file wins over process env**.
We therefore MERGE the alias into the .env file: swap every role's
`*_MODEL_CONFIG__MODEL` to the alias (embeddings stay on nomic) and point all
`*__OVERRIDES__BASE_URL` / `*__OVERRIDES__API_KEY_ENV` at the gateway, while
preserving DB/cache/auth and any other keys.

Two files are written:
  1. `_vendor/honcho/.env`          — the file load_dotenv actually reads (manual mode).
  2. `_vendor/honcho/tests/bench/.env` — harness.py copies this to the root .env.
"""
from __future__ import annotations

import os
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent.parent / "_vendor" / "honcho" / "tests" / "bench"
VENDOR = BENCH_DIR.parent.parent
TEMPLATE = Path(__file__).resolve().parent.parent / ".env.example"

# Judge / key env vars the runner subprocesses read directly via os.getenv.
# Surfaced to children from the process env, never written into the .env file.
JUDGE_ENV_KEYS = [
    "PLEXUS_API_KEY",                 # alias key source (OVERRIDES__API_KEY_ENV)
    "OPENAI_API_KEY",                 # longmem/locomo/molecular/coverage judges
    "LLM_OPENAI_COMPATIBLE_API_KEY",  # beam judge
    "LLM_OPENAI_COMPATIBLE_BASE_URL", # beam + patched OpenAI/anthropic transport
    "BEAM_JUDGE_MODEL",
    "LONGMEM_JUDGE_MODEL",
    "LOCOMO_JUDGE_MODEL",
    "LLM_ANTHROPIC_API_KEY",          # longmem/locomo anthropic judge (get_context)
    "LLM_ANTHROPIC_BASE_URL",         # Plexus serves /v1/messages
    "ANTHROPIC_JUDGE_MODEL",
    "OPENROUTER_API_KEY",
]


def _swap_model_lines(text: str, alias: str, base_url: str, api_key_env: str) -> str:
    """Swap the model / base_url / key_env values, preserving everything else."""
    out: list[str] = []
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            out.append(line)
            continue
        key, _, _val = line.partition("=")
        key = key.strip()
        if key == "EMBEDDING_MODEL_CONFIG__MODEL":
            out.append(line)  # embeddings stay on nomic
        elif key.endswith("MODEL_CONFIG__MODEL"):
            out.append(f"{key}={alias}")
        elif key.endswith("__OVERRIDES__BASE_URL"):
            out.append(f"{key}={base_url}")
        elif key.endswith("__OVERRIDES__API_KEY_ENV"):
            out.append(f"{key}={api_key_env}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def render_harness_dotenv(
    alias: str,
    base_url: str,
    api_key_env: str,
) -> list[Path]:
    """Merge `alias` into the .env file(s) honcho's load_dotenv(override=True) reads.

    Uses the existing `_vendor/honcho/.env` when present (preserves any manual
    DB/cache/auth keys); otherwise seeds from `.env.example`. Returns the paths
    written.
    """
    current = VENDOR / ".env"
    if current.exists():
        text = current.read_text()
    elif TEMPLATE.exists():
        text = TEMPLATE.read_text()
    else:
        raise FileNotFoundError("no .env or .env.example to seed from")

    merged = _swap_model_lines(text, alias, base_url, api_key_env)

    targets = [current, BENCH_DIR / ".env"]
    for dest in targets:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(merged)
    return targets


def judge_env_from_process() -> dict[str, str]:
    """Collect any judge/key env vars present on the current process."""
    return {k: os.environ[k] for k in JUDGE_ENV_KEYS if k in os.environ}
