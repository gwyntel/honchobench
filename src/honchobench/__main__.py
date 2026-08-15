"""honchobench CLI — `python -m honchobench` or `honchobench`.

Runs each selected role over the dataset with the given models and writes scored
per-role .md + .csv reports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from . import ROLES
from .clients import ModelClient, default_openai_api_key
from .roles import ROLE_RUNNERS
from .scoring import score_result
from .report import write_reports

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("honchobench")

# Default exemplar chat model on the Plexus gateway. Used when neither
# --models nor the HONCHOBENCH_MODELS env var is provided. A matching default
# embedding model (nomic-ai/nomic-embed-text-v1.5) is a gateway-side router
# concern and is documented in .env.example / README; the standalone harness
# does not itself call an embedding endpoint.
DEFAULT_CHAT_MODEL = "LiquidAI/LFM2_5-2_6B"


def load_env_file(path: str):
    if not path or not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip("'\"")
            os.environ.setdefault(k, v)


def resolve_transport(model: str) -> str:
    if "/" not in model:
        return "openai"
    if model.startswith("anthropic/"):
        return "anthropic"
    if model.startswith("gemini/"):
        return "gemini"
    # e.g. model ids with provider prefix; fall back to openai transport.
    return "openai"


def strip_provider_prefix(model: str) -> str:
    """Keep the model id but remember transport separately."""
    return model.split("/", 1)[1] if model.startswith(("anthropic/", "gemini/")) else model


async def run_single(client, conv, role, model_id, labels_map):
    runner = ROLE_RUNNERS[role]
    kwargs = {}
    if role == "dialectic":
        # use first label qa question if available, else generic
        labels = labels_map.get(conv.get("conversation_id")) or {}
        qa = labels.get("qa") or []
        if qa and qa[0].get("q"):
            kwargs["query"] = qa[0]["q"]
    if role == "deduction":
        labels = labels_map.get(conv.get("conversation_id")) or {}
        if labels.get("deductive"):
            kwargs["hints"] = labels["deductive"]
    if role == "induction":
        labels = labels_map.get(conv.get("conversation_id")) or {}
        if labels.get("inductive"):
            kwargs["hints"] = labels["inductive"]
    return await runner(conv, client, model=model_id, **kwargs)


async def run(models, roles, convs, labels_map, out_dir):
    all_results = {r: [] for r in ROLES}
    for model_spec in models:
        transport = resolve_transport(model_spec)
        model_id = strip_provider_prefix(model_spec)
        client = ModelClient(transport=transport, model=model_id)
        # verify transport is configured
        try:
            if transport == "openai":
                _ = client._get_openai()
            elif transport == "anthropic":
                _ = client._get_anthropic()
            else:
                _ = client._get_genai()
        except RuntimeError as exc:
            log.error("Skipping model %s: %s", model_spec, exc)
            continue

        for conv in convs:
            conv_id = conv.get("conversation_id")
            labels = labels_map.get(conv_id)
            for role in roles:
                try:
                    result = await run_single(client, conv, role, model_id, labels_map)
                except Exception as exc:  # noqa: BLE001
                    log.error("[%s][%s] %s failed: %s", model_id, conv_id, role, exc)
                    continue
                result["model"] = model_id
                result["model_scores"] = score_result(result, labels)
                all_results[role].append(result)
                log.info("[%s][%s] %s -> %s", model_id, conv_id, role,
                         _condense(result.get("model_scores") or {}))
        try:
            await client.aclose()
        except Exception:
            pass
    created = write_reports(all_results, out_dir)
    log.info("Wrote reports to %s", out_dir)
    for c in created:
        log.info("  %s", c)
    return all_results, created


def _condense(ms: dict) -> str:
    key = next((k for k in ("f1", "qa_f1") if isinstance(ms.get(k), (int, float))), None)
    if key:
        return f"{key}={ms[key]:.3f}"
    return "ran (no labels)"


def _resolve_models_arg(models_arg: Optional[str]) -> list[str]:
    """Default model list: --models > HONCHOBENCH_MODELS env > exemplar default."""
    spec = models_arg or os.environ.get("HONCHOBENCH_MODELS")
    if spec:
        return [m.strip() for m in spec.split(",") if m.strip()]
    # No explicit --models and no env -> fall back to the exemplar chat model on
    # the Plexus gateway so a bare `make bench` / `docker compose run` works.
    return [DEFAULT_CHAT_MODEL]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="honchobench", description="HonchoBench role runner")
    parser.add_argument("--models", required=False, default=None,
                        help="Comma list of model ids (prefix anthropic/ or gemini/ to pick transport; "
                             "else openai). Defaults to $HONCHOBENCH_MODELS, then the exemplar "
                             f"model ({DEFAULT_CHAT_MODEL}).")
    parser.add_argument("--roles", default="all",
                        help="Comma list of roles: deriver,summary_short,summary_long,dialectic,deduction,induction,card_refresh (default all).")
    parser.add_argument("--dataset", default="data/dataset/conversations.json")
    parser.add_argument("--labels", default="data/dataset/labels.json")
    parser.add_argument("--out", default="reports")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--limit", type=int, default=None,
                        help="Restrict to the first N conversations (for smoke tests).")
    args = parser.parse_args(argv)

    load_env_file(args.env_file)

    if not default_openai_api_key() and not os.environ.get("ANTHROPIC_API_KEY") \
            and not os.environ.get("GEMINI_API_KEY"):
        log.error("No API key found. Set OPENAI_API_KEY/PLEXUS_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY "
                  "(optionally via --env-file).")
        return 2

    if not os.path.exists(args.dataset):
        log.error("Dataset not found: %s", args.dataset)
        return 2

    with open(args.dataset) as f:
        convs = json.load(f)
    if args.limit:
        convs = convs[: args.limit]

    labels_map = {}
    if os.path.exists(args.labels):
        with open(args.labels) as f:
            labels_raw = json.load(f)
        if isinstance(labels_raw, dict):
            labels_map = labels_raw
        elif isinstance(labels_raw, list):
            for item in labels_raw:
                if isinstance(item, dict) and item.get("conversation_id"):
                    labels_map[item["conversation_id"]] = item
    else:
        log.warning("labels.json not found (%s) — running unlabeled; scoring will fill in when labels land.", args.labels)

    models = _resolve_models_arg(args.models)
    log.info("Running models: %s", ", ".join(models) or "(none)")
    roles = ROLES if args.roles == "all" else [r.strip() for r in args.roles.split(",") if r.strip()]
    unknown = [r for r in roles if r not in ROLES]
    if unknown:
        log.error("Unknown roles: %s", unknown)
        return 2

    asyncio.run(run(models, roles, convs, labels_map, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
