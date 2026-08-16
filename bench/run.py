#!/usr/bin/env python3
"""HonchoBench orchestrator — benchmark Plexus aliases against honcho's real stack.

Runs honcho's OWN bench suite (longmem, locomo, beam, oolong, molecular,
coverage) against a list of Plexus model aliases. For each alias it starts a
fresh honcho stack (harness: Docker Postgres/Redis + API + deriver), routes
every LLM role through that alias, runs the runners, tears down, and aggregates
per-alias numbers. Portable: identical on a laptop or a GitHub Actions runner.

Env contract:
    HONCHOBENCH_ALIASES      comma-separated aliases (required)
    HONCHOBENCH_BASE_URL     OpenAI-compatible gateway (default: Plexus)
    HONCHOBENCH_API_KEY_ENV  env var holding the key (default: PLEXUS_API_KEY)
    HONCHOBENCH_RUN_PAID     0|1 — gate paid aliases (default 0)
    HONCHOBENCH_FREE_ALIAS   alias allowed when RUN_PAID=0 (default: provocative-qwen3.6-35b)
    HONCHOBENCH_START        harness|manual (default harness)
    HONCHOBENCH_LIMIT        optional per-runner cap for smoke runs
    HONCHOBENCH_OUT          report dir (default reports/)

Judge keys (read from process env, see docs/BENCH_RUNNER_SPEC.md §3):
    OPENAI_API_KEY, LLM_OPENAI_COMPATIBLE_BASE_URL/API_KEY, BEAM_JUDGE_MODEL,
    LONGMEM_JUDGE_MODEL, LOCOMO_JUDGE_MODEL, LLM_ANTHROPIC_API_KEY, OPENROUTER_API_KEY

The model-under-test is the alias (set on every honcho role). The judge models
are separate (routed through Plexus via scripts/patch_honcho.py + the env vars
above). Run `python scripts/patch_honcho.py` once after vendoring honcho.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .build_harness_env import render_harness_dotenv
from .env import honcho_env_for_alias
from .reports import write_results

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "_vendor" / "honcho"
BENCH = VENDOR / "tests" / "bench"

DEFAULT_BASE_URL = "https://plexus.nebulosa-bass.ts.net/v1"
DEFAULT_API_KEY_ENV = "PLEXUS_API_KEY"
DEFAULT_FREE_ALIAS = "provocative-qwen3.6-35b"
DEFAULT_HONCHO_PORT = 8000


def parse_aliases() -> list[str]:
    raw = os.environ.get("HONCHOBENCH_ALIASES", "")
    return [a.strip() for a in raw.split(",") if a.strip()]


def _limit() -> str | None:
    """Return the per-runner cap for smoke runs (None if unset)."""
    n = os.environ.get("HONCHOBENCH_LIMIT", "").strip()
    return n or None


# ---------------------------------------------------------------------------
# Runner definitions. Each is a template of argv fragments; `{...}` is
# interpolated per alias. cwd is always _vendor/honcho; `uv run` supplies deps.
# ---------------------------------------------------------------------------
def _runner_cmd(module: str, *args: str) -> list[str]:
    return ["uv", "run", "python", "-m", f"tests.bench.{module}", *args]


def runner_commands(alias: str, ctx: dict) -> dict[str, list[str]]:
    out = ctx["out_root"] / alias
    out.mkdir(parents=True, exist_ok=True)
    data = ctx["data_root"]
    n = _limit()

    cmds = {
        "longmem": _runner_cmd(
            "longmem",
            "--test-file", str(data / "longmem" / "longmemeval_oracle.json"),
            "--base-api-port", str(ctx["port"]),
            "--json-output", str(out / "longmem.json"),
        ),
        "locomo": _runner_cmd(
            "locomo",
            "--data-file", str(data / "locomo" / "locomo10.json"),
            "--base-api-port", str(ctx["port"]),
            "--json-output", str(out / "locomo.json"),
        ),
        "beam": _runner_cmd(
            "beam",
            "--context-length", "100K",
            "--base-api-port", str(ctx["port"]),
            "--json-output", str(out / "beam.json"),
        ),
        "oolong": _runner_cmd(
            "oolong",
            "--variant", "real",
            "--data-dir", str(data / "oolong-real"),
            "--base-api-port", str(ctx["port"]),
            "--json-output", str(out / "oolong.json"),
        ),
        # molecular/coverage are post-hoc trace evaluators (no honcho needed).
        # The alias is the JUDGE here (--model), scoring fixed gold-fact traces.
        "molecular": _runner_cmd(
            "molecular",
            "--traces", str(data / "traces" / "enron_traces.jsonl"),
            "--provider", "openai",
            "--model", alias,
            "--output-dir", str(out / "molecular"),
        ),
        "coverage": _runner_cmd(
            "coverage",
            "--traces", str(data / "traces" / "enron_traces.jsonl"),
            "--provider", "openai",
            "--model", alias,
            "--output-dir", str(out / "coverage"),
        ),
    }

    # per-runner limit flags (only the flags each runner accepts)
    if n:
        cmds["longmem"] += ["--test-count", n]
        cmds["locomo"] += ["--test-count", n, "--question-count", n]
        cmds["oolong"] += ["--max-examples", n]
        cmds["molecular"] += ["--limit", n]
        cmds["coverage"] += ["--limit", n]

    return cmds


# ---------------------------------------------------------------------------
# Harness lifecycle
# ---------------------------------------------------------------------------
class Harness:
    """Launch + tear down the honcho stack for one alias."""

    def __init__(self, env: dict[str, str], port: int):
        self.env = env
        self.port = port
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        mode = os.environ.get("HONCHOBENCH_START", "harness")
        if mode == "manual":
            # Pre-provisioned DB; start API + deriver directly.
            subprocess.run(
                ["bash", str(ROOT / "start_honcho.sh")],
                cwd=ROOT, env=self.env, check=False,
            )
            self._wait_ready(timeout=120)
            return
        # harness.py: Docker Postgres/Redis + API + deriver, blocks in monitor.
        self.proc = subprocess.Popen(
            ["uv", "run", "python", "tests/bench/harness.py",
             "--api-port", str(self.port)],
            cwd=VENDOR, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self._wait_ready(timeout=600)

    def _wait_ready(self, timeout: int) -> None:
        url = f"http://localhost:{self.port}/docs"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                tail = self.proc.stdout.read()[-2000:] if self.proc.stdout else ""
                raise RuntimeError(f"harness exited early:\n{tail}")
            try:
                urllib.request.urlopen(url, timeout=2)
                return
            except Exception:
                time.sleep(3)
        raise TimeoutError(f"honcho not ready on :{self.port} within {timeout}s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        # manual mode teardown
        subprocess.run(["pkill", "-f", "uvicorn src.main:app"], capture_output=True)
        subprocess.run(["pkill", "-f", "python -m src.deriver"], capture_output=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def _run_cmd(cmd: list[str], env: dict[str, str], cwd: Path) -> dict:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    return {
        "cmd": " ".join(cmd),
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def benchmark_alias(alias: str, ctx: dict) -> dict:
    base_url, key_env = ctx["base_url"], ctx["api_key_env"]
    role_env = honcho_env_for_alias(alias, base_url=base_url, api_key_env=key_env)
    full_env = {
        **os.environ,
        **role_env,
        # beam.py reads BEAM_DATA_DIR (patched) instead of the hardcoded path
        "BEAM_DATA_DIR": str(ctx["data_root"] / "beam"),
    }

    # harness copies tests/bench/.env into the project root — pre-render it.
    render_harness_dotenv(alias, base_url, key_env)

    out_dir: Path = ctx["out_root"] / alias
    out_dir.mkdir(parents=True, exist_ok=True)

    result: dict = {"alias": alias}
    harness = Harness(full_env, ctx["port"])
    try:
        harness.start()
        result["start"] = "ok"
        for name, cmd in runner_commands(alias, ctx).items():
            print(f"  [{alias}] {name} ...", flush=True)
            result[name] = _run_cmd(cmd, full_env, VENDOR)
    except Exception as e:  # noqa: BLE001
        result["start"] = {"error": str(e)}
    finally:
        harness.stop()

    # persist per-alias raw result
    (out_dir / "raw.json").write_text(json.dumps(result, indent=2))
    return result


def gate_aliases(aliases: list[str]) -> list[str]:
    """Refuse paid aliases unless HONCHOBENCH_RUN_PAID=1 (cost guard)."""
    run_paid = os.environ.get("HONCHOBENCH_RUN_PAID", "0") == "1"
    free = os.environ.get("HONCHOBENCH_FREE_ALIAS", DEFAULT_FREE_ALIAS)
    if run_paid:
        return aliases
    allowed = [a for a in aliases if a == free]
    skipped = [a for a in aliases if a != free]
    if skipped:
        print(f"SKIPPING paid aliases (HONCHOBENCH_RUN_PAID=0): {skipped}")
    if not allowed:
        print(f"no free alias in list; nothing to run (free = {free})", file=sys.stderr)
        sys.exit(2)
    return allowed


def main() -> int:
    aliases = parse_aliases()
    if not aliases:
        print("HONCHOBENCH_ALIASES not set — nothing to benchmark.", file=sys.stderr)
        return 2
    aliases = gate_aliases(aliases)

    ctx = {
        "base_url": os.environ.get("HONCHOBENCH_BASE_URL", DEFAULT_BASE_URL),
        "api_key_env": os.environ.get("HONCHOBENCH_API_KEY_ENV", DEFAULT_API_KEY_ENV),
        "port": int(os.environ.get("HONCHOBENCH_PORT", DEFAULT_HONCHO_PORT)),
        "out_root": Path(os.environ.get("HONCHOBENCH_OUT", "reports")),
        "data_root": ROOT / "data",
    }

    print(f"HonchoBench {datetime.now(timezone.utc).isoformat()} — aliases: {aliases}")

    results: dict[str, dict] = {}
    for alias in aliases:
        print(f"\n=== alias: {alias} ===")
        results[alias] = benchmark_alias(alias, ctx)

    write_results(results, ctx["out_root"])
    print(f"\nDone. Reports in {ctx['out_root']}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
