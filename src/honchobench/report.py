"""Report writer: per-role .md + .csv files plus a summary README.

CSV columns: model, conversation_id, metric, value.
MD: per-model aggregate tables + per-conversation detail.
"""

from __future__ import annotations

import csv
import os
import statistics
from collections import defaultdict

from . import ROLES


def _metric_value(metric: dict, key):
    v = metric.get(key)
    if v is None:
        return ""
    if isinstance(v, float):
        if abs(v) < 0.0001:
            return f"{v:.3f}".rstrip("0").rstrip(".")
        return f"{v:.3f}"
    return str(v)


def write_reports(results_by_role, out_dir: str) -> list[str]:
    """Write reports for every role that has results. Returns list of created files."""
    os.makedirs(out_dir, exist_ok=True)
    created = []
    for role in ROLES:
        res = results_by_role.get(role) or []
        if not res:
            continue
        md_path, csv_path = _write_role(role, res, out_dir)
        created.append(md_path)
        created.append(csv_path)
    readme_path = _write_readme(results_by_role, out_dir, created)
    created.append(readme_path)
    return created


def _primary_metric(role: str) -> str:
    return {
        "deriver": "f1",
        "summary_short": "f1",
        "summary_long": "f1",
        "dialectic": "qa_f1",
        "deduction": "f1",
        "induction": "f1",
        "card_refresh": "f1",
    }[role]


def _metric_candidates(role: str) -> list[str]:
    return {
        "deriver": ["f1", "precision", "recall"],
        "summary_short": ["f1", "precision", "recall"],
        "summary_long": ["f1", "precision", "recall"],
        "dialectic": ["qa_f1", "answer_presence", "evidence_coverage"],
        "deduction": ["f1", "precision", "recall"],
        "induction": ["f1", "precision", "recall"],
        "card_refresh": ["f1", "precision", "recall"],
    }[role]


def _write_role(role: str, results: list[dict], out_dir: str):
    # Collect scored metrics per result.
    scored = [r for r in results if r.get("model_scores")]
    primary = _primary_metric(role)
    cands = _metric_candidates(role)

    csv_path = os.path.join(out_dir, f"{role}.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "conversation_id", "metric", "value"])
        for r in results:
            ms = r.get("model_scores") or {}
            for key, val in ms.items():
                if key == "labeled":
                    continue
                if isinstance(val, (int, float)):
                    w.writerow([r["model"], r["conversation_id"], key, val])
            # also record tool diagnostics
            if r.get("tool_calls_made") is not None:
                w.writerow([r["model"], r["conversation_id"], "tool_calls_made", r["tool_calls_made"]])

    # Aggregate per model.
    by_model = defaultdict(list)
    for r in results:
        by_model[r["model"]].append(r)

    md_path = os.path.join(out_dir, f"{role}.md")
    with open(md_path, "w") as f:
        f.write(f"# HonchoBench — {role}\n\n")
        f.write(f"Scoring: matched against labels.json ground truth. Primary metric: **{primary}**.\n\n")

        f.write("## Per-model aggregate\n\n")
        f.write("| model | n_convs | " + " | ".join(cands) + " |\n")
        f.write("|-------|---------|" + "|".join(["---"] * len(cands)) + "|\n")
        for model in sorted(by_model):
            mscores = [r.get("model_scores") for r in by_model[model]]
            vals = {}
            for c in cands:
                col = [m[c] for m in mscores if m and isinstance(m.get(c), (int, float))]
                vals[c] = _metric_value({"f1": statistics.mean(col)}, "f1") if col else ""
            f.write(f"| {model} | {len(by_model[model])} | " + " | ".join(vals[c] for c in cands) + " |\n")

        # labeled vs unlabeled note
        labeled = sum(1 for r in results if (r.get("model_scores") or {}).get("labeled"))
        f.write(f"\nLabeled conversations scored: {labeled}/{len(results)}.\n")

        f.write("\n## Per-conversation detail\n\n")
        f.write("| model | conversation_id | " + " | ".join(cands) + " |\n")
        f.write("|-------|-----------------|" + "|".join(["---"] * len(cands)) + "|\n")
        for r in sorted(results, key=lambda x: (x["model"], x["conversation_id"])):
            ms = r.get("model_scores") or {}
            vals = []
            for c in cands:
                v = ms.get(c)
                if isinstance(v, (int, float)):
                    vals.append(_metric_value({c: v}, c))
                elif v is None:
                    vals.append("no-label")
                else:
                    vals.append(str(v))
            f.write(f"| {r['model']} | {r['conversation_id']} | " + " | ".join(vals) + " |\n")

        # raw-sample section for the first conversation of each model
        f.write("\n## Raw output (first conversation per model)\n")
        seen = set()
        for r in results:
            if r["model"] in seen or r.get("tool_calls_made") is None:
                continue
            seen.add(r["model"])
            f.write(f"\n### {r['model']} — {r['conversation_id']}")
            if r.get("tool_calls_made"):
                f.write(f" (tool calls: {r['tool_calls_made']}, iterations: {r['iterations']})")
            raw = (r.get("raw_output") or "").strip()
            f.write(f"\n```\n{raw[:2000]}\n```\n")
        f.write("\n")
    return md_path, csv_path


def _write_readme(results_by_role, out_dir, created) -> str:
    readme = os.path.join(out_dir, "README.md")
    with open(readme, "w") as f:
        f.write("# HonchoBench reports\n\n")
        f.write("Per-role scored outputs. Each role has a `.md` (human) and `.csv` (machine) report.\n\n")
        f.write("| role | report | conversations scored |\n")
        f.write("|------|--------|--------------------|\n")
        for role in ROLES:
            res = results_by_role.get(role) or []
            if not res:
                continue
            labeled = sum(1 for r in res if (r.get("model_scores") or {}).get("labeled"))
            f.write(f"| {role} | [`{role}.md`]({role}.md) / [`{role}.csv`]({role}.csv) | {labeled}/{len(res)} |\n")
        f.write("\nModels: " + ", ".join(sorted({r.get("model", "") for rr in results_by_role.values() for r in rr})) + "\n")
    return readme
