"""Aggregate per-alias benchmark results into JSON + markdown reports.

Reads the dict-of-dicts produced by bench.run, plus any runner JSON files on
disk, and emits:
  - reports/results.json   (machine-readable rollup)
  - reports/results.md     (human-readable summary)
  - reports/<alias>/SUMMARY.md
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _extract_metrics(alias_result: dict) -> dict[str, str]:
    """Pull the headline number out of each runner's result blob.

    Runner JSON schemas differ (longmem success_rate, beam per-ability,
    molecular averages, etc.). We keep it generic: surface exit_code + any
    obvious scalar metrics, and defer the precise parsing to the runner report.
    """
    metrics: dict[str, str] = {}
    for runner_name, r in alias_result.items():
        if runner_name in ("alias", "start"):
            continue
        if isinstance(r, dict):
            metrics[runner_name] = f"exit={r.get('exit_code', '?')}"
        else:
            metrics[runner_name] = str(r)
    return metrics


def write_results(results: dict[str, dict], out_root: Path) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "results.json").write_text(json.dumps(results, indent=2))

    lines = [
        "# HonchoBench results",
        "",
        f"generated: {datetime.now(timezone.utc).isoformat()}",
        f"aliases: {', '.join(results.keys())}",
        "",
    ]
    for alias, r in results.items():
        lines.append(f"## {alias}")
        for runner_name, rr in r.items():
            if runner_name in ("alias", "start"):
                continue
            if isinstance(rr, dict):
                lines.append(f"- **{runner_name}**: exit={rr.get('exit_code', '?')}")
            else:
                lines.append(f"- **{runner_name}**: {rr}")
        lines.append("")

    (out_root / "results.md").write_text("\n".join(lines))

    # per-alias summary
    for alias, r in results.items():
        alias_dir = out_root / alias
        alias_dir.mkdir(parents=True, exist_ok=True)
        (alias_dir / "SUMMARY.md").write_text(
            f"# {alias}\n\n" + "\n".join(f"- {k}: {v}" for k, v in _extract_metrics(r).items())
        )
