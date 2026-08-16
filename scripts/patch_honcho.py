#!/usr/bin/env python3
"""Idempotently patch vendored honcho to make every LLM-judge Plexus-routable.

Honcho's bench judges are hardcoded to specific models and (for longmem/locomo/
molecular/coverage) build their OpenAI client without a base_url, so they hit
api.openai.com regardless of the gateway. This script makes the judge transport
env-driven so the WHOLE suite can run against Plexus with one key. See
docs/BENCH_RUNNER_SPEC.md §3 for the source contract.

Patches applied (all idempotent — safe to re-run):
  1. runner_common.py `create_openai_client` — default `base_url` to
     `LLM_OPENAI_COMPATIBLE_BASE_URL` when not explicitly passed. This single
     change covers longmem, locomo, molecular (--provider openai), coverage.
  2. longmem_common.py judge model → `os.getenv("LONGMEM_JUDGE_MODEL", "gpt-4o-2024-08-06")`.
  3. locomo_common.py judge model → `os.getenv("LOCOMO_JUDGE_MODEL", "gpt-4o-mini")`.

Usage: python scripts/patch_honcho.py [path/to/honcho]   (default _vendor/honcho)
"""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH = Path(__file__).resolve().parent.parent / "_vendor" / "honcho" / "tests" / "bench"

# (relative_path, old_string, new_string)
PATCHES = [
    (
        "runner_common.py",
        # exact source block
        '    key = api_key or os.getenv(env_key_name)\n'
        '    if not key:\n'
        '        raise ValueError(f"{env_key_name} is not set")\n'
        '\n'
        '    if base_url:',
        '    key = api_key or os.getenv(env_key_name)\n'
        '    if not key:\n'
        '        raise ValueError(f"{env_key_name} is not set")\n'
        '\n'
        '    if base_url is None:\n'
        '        base_url = os.getenv("LLM_OPENAI_COMPATIBLE_BASE_URL")\n'
        '\n'
        '    if base_url:',
    ),
    (
        "longmem_common.py",
        '            model="gpt-4o-2024-08-06",',
        '            model=os.getenv("LONGMEM_JUDGE_MODEL", "gpt-4o-2024-08-06"),',
    ),
    (
        "locomo_common.py",
        '            model="gpt-4o-mini",',
        '            model=os.getenv("LOCOMO_JUDGE_MODEL", "gpt-4o-mini"),',
    ),
    (
        "beam.py",
        '    data_dir = Path(__file__).parent / "beam_data"',
        '    data_dir = Path(os.environ.get("BEAM_DATA_DIR", str(Path(__file__).parent / "beam_data")))',
    ),
]


def apply(bench_dir: Path) -> list[str]:
    applied = []
    for rel, old, new in PATCHES:
        path = bench_dir / rel
        text = path.read_text()
        if new in text:
            continue  # already patched
        if old not in text:
            print(f"  SKIP {rel}: target block not found (already patched differently?)")
            continue
        path.write_text(text.replace(old, new, 1))
        applied.append(rel)
    return applied


def main() -> int:
    bench_dir = Path(sys.argv[1]) / "tests" / "bench" if len(sys.argv) > 1 else _BENCH
    if not bench_dir.exists():
        print(f"error: bench dir not found: {bench_dir}", file=sys.stderr)
        return 1
    applied = apply(bench_dir)
    if applied:
        print(f"patched {len(applied)} file(s): {', '.join(applied)}")
    else:
        print("no changes (already patched or blocks absent)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
