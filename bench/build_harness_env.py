"""Render `_vendor/honcho/tests/bench/.env` for the harness.

The harness (harness.py:831-834) copies `tests/bench/.env` into the project root
and throws FileNotFoundError if it is absent, so it must exist before launch.
This module writes the per-alias role block (from bench.env) into that file.
API keys are NOT written here — they live in the process env, where honcho's
`*_OVERRIDES__API_KEY_ENV` and the runner scripts' `os.getenv` both resolve them.
"""
from __future__ import annotations

import os
from pathlib import Path

from .env import honcho_env_for_alias

BENCH_DIR = Path(__file__).resolve().parent.parent / "_vendor" / "honcho" / "tests" / "bench"

# Judge / key env vars the runner subprocesses read directly via os.getenv.
# They are surfaced to children from the process env, not the .env file.
JUDGE_ENV_KEYS = [
    "PLEXUS_API_KEY",                 # alias key source (OVERRIDES__API_KEY_ENV)
    "OPENAI_API_KEY",                 # longmem/locomo/molecular/coverage judge
    "LLM_OPENAI_COMPATIBLE_API_KEY",  # beam judge
    "LLM_OPENAI_COMPATIBLE_BASE_URL", # beam + patched judge transport
    "BEAM_JUDGE_MODEL",
    "LONGMEM_JUDGE_MODEL",
    "LOCOMO_JUDGE_MODEL",
    "LLM_ANTHROPIC_API_KEY",          # molecular/coverage --provider anthropic
    "OPENROUTER_API_KEY",
]


def render_harness_dotenv(
    alias: str,
    base_url: str,
    api_key_env: str,
    dest: Path | None = None,
) -> Path:
    """Write the role block for `alias` to tests/bench/.env (no secrets)."""
    env = honcho_env_for_alias(alias, base_url=base_url, api_key_env=api_key_env)
    dest = Path(dest) if dest else BENCH_DIR / ".env"
    dest.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    return dest


def judge_env_from_process() -> dict[str, str]:
    """Collect any judge/key env vars present on the current process."""
    return {k: os.environ[k] for k in JUDGE_ENV_KEYS if k in os.environ}
