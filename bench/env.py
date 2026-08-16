"""Render honcho's per-role model config env vars for a given alias.

Honcho (Pydantic settings) resolves each role's model through its OWN
`*_MODEL_CONFIG__*` block. It does NOT inherit a single top-level value —
every role defaults to gpt-5.4-mini@api.openai.com unless set explicitly.
This module builds the full per-role block, pointing every LLM role at the
alias under test while keeping the embedding model fixed on nomic.

Reference (honcho src/config.py, ModelOverrideSettings):
    overrides.api_key, overrides.api_key_env, overrides.base_url
The actual key is read at runtime from the process env var named by
`*_OVERRIDES__API_KEY_ENV` — never hardcoded.
"""

from __future__ import annotations

# Embedding model is a constant across aliases: you benchmark the LLM roles,
# not the retriever. nomic-embed-text-v1.5 @ 768 dims.
EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
EMBEDDING_DIM = 768

# The honcho LLM roles that switch to the alias under test.
_LLM_ROLE_PREFIXES = [
    "",                     # MODEL_CONFIG__* (default role fallback)
    "DERIVER_",             # deriver (memory formation)
    "SUMMARY_",             # summarizer (session summaries)
]

# Dialectic agent has a separate config per reasoning level.
_DIALECTIC_LEVELS = ["minimal", "low", "medium", "high", "max"]


def _llm_block(prefix: str, model: str, base_url: str, api_key_env: str) -> dict[str, str]:
    """Emit the MODEL_CONFIG__* block for one role prefix."""
    p = f"{prefix}MODEL_CONFIG__"
    return {
        f"{p}TRANSPORT": "openai",
        f"{p}MODEL": model,
        f"{p}OVERRIDES__BASE_URL": base_url,
        f"{p}OVERRIDES__API_KEY_ENV": api_key_env,
    }


def honcho_env_for_alias(
    alias: str,
    base_url: str = "https://plexus.nebulosa-bass.ts.net/v1",
    api_key_env: str = "PLEXUS_API_KEY",
    *,
    include_embeddings: bool = True,
    include_scheduling: bool = True,
) -> dict[str, str]:
    """Return the env-var dict to drive honcho against `alias`.

    Args:
        alias: Plexus alias (or raw model id) to benchmark.
        base_url: OpenAI-compatible gateway base URL.
        api_key_env: name of the process env var holding the actual API key.
    """
    env: dict[str, str] = {}

    # LLM roles -> alias under test
    for prefix in _LLM_ROLE_PREFIXES:
        env.update(_llm_block(prefix, alias, base_url, api_key_env))
    for level in _DIALECTIC_LEVELS:
        env.update(_llm_block(f"DIALECTIC_LEVELS__{level}__", alias, base_url, api_key_env))

    if include_embeddings:
        env.update(
            {
                "EMBEDDING_MODEL_CONFIG__TRANSPORT": "openai",
                "EMBEDDING_MODEL_CONFIG__MODEL": EMBEDDING_MODEL,
                "EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL": base_url,
                "EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY_ENV": api_key_env,
                "EMBEDDING_VECTOR_DIMENSIONS": str(EMBEDDING_DIM),
            }
        )

    if include_scheduling:
        # Drain the deriver queue immediately — no batching/token gate.
        env.update(
            {
                "DERIVER_FLUSH_ENABLED": "true",
                "DERIVER_REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS": "0",
                "DERIVER_REPRESENTATION_BATCH_MAX_AGE_SECONDS": "0",
            }
        )

    return env


def write_honcho_dotenv(alias: str, dest_path: str, **kwargs) -> None:
    """Render the env dict to a KEY=VALUE .env file (for `--env-file` usage)."""
    env = honcho_env_for_alias(alias, **kwargs)
    with open(dest_path, "w") as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")
