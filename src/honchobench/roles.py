"""Per-role run functions.

Each builds the exact prompt per ROLE_SPEC.md, calls the client (single-shot for
the deriver + summaries, tool-loop for the agentic roles) and returns a structured
result dict.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from typing import Optional

from . import prompts
from .store import (
    InMemoryStore,
    DIALECTIC_TOOLS,
    DEDUCTION_SPECIALIST_TOOLS,
    INDUCTION_SPECIALIST_TOOLS,
    CARD_REFRESH_SPECIALIST_TOOLS,
)
from .tool_loop import run_tool_loop

log = logging.getLogger("honchobench.roles")


def _format_messages(conv, include_timestamp: bool, window: Optional[int] = None) -> str:
    msgs = conv.get("messages", [])
    if window is not None:
        msgs = msgs[-window:]
    out = []
    start = _dt.datetime(2026, 1, 1, 9, 0, 0)
    for i, m in enumerate(msgs):
        peer = m.get("sender", "")
        content = m.get("text", "")
        if include_timestamp:
            ts = (start + _dt.timedelta(minutes=4 * i)).strftime("%Y-%m-%d %H:%M:%S")
            out.append(f"{ts} {peer}: {content}")
        else:
            out.append(f"{peer}: {content}")
    return "\n".join(out)


def _extract_peer_id(conv) -> str:
    return conv.get("target_peer") or conv.get("conversation_id", "unknown")


def _parse_json_robust(text: str):
    """Try to parse structured JSON from model output, tolerating code fences / prose."""
    if not text:
        return None
    text = text.strip()
    # Strip markdown code fences.
    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # find first { ... } block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


# ---------------------------------------------------------------------------
# Single-shot roles
# ---------------------------------------------------------------------------
async def run_deriver(conv, client, model: str) -> dict:
    peer = _extract_peer_id(conv)
    formatted = _format_messages(conv, include_timestamp=True)
    prompt = prompts.minimal_deriver_prompt(peer, messages=formatted)
    resp = await client.call(
        model=model, messages=[{"role": "user", "content": prompt}],
        max_tokens=2500, json_mode=True, temperature=0.0,
    )
    text = resp.get("content", "")
    parsed = _parse_json_robust(text)
    explicit = []
    if parsed and isinstance(parsed, dict):
        # Primary schema key is `explicit`; tolerate a few common model variants
        # (`observations`, single object, dict values, or a bare list) so real
        # model outputs are scored rather than discarded on a key mismatch.
        raw = parsed.get("explicit", parsed)
        if raw is None:
            raw = []
        if isinstance(raw, dict):
            raw = raw.get("content", raw.get("explicit", raw.get("observations", [])))
        if isinstance(raw, (list, tuple)):
            for e in raw:
                if isinstance(e, dict):
                    c = e.get("content")
                    if c:
                        explicit.append(c)
                elif isinstance(e, str) and e.strip():
                    explicit.append(e)
        elif isinstance(raw, str) and raw.strip():
            explicit.append(raw)
    return {
        "role": "deriver",
        "conversation_id": conv.get("conversation_id", ""),
        "model": model,
        "raw_output": text,
        "parsed_output": {"explicit": explicit},
        "tool_calls_made": 0,
        "iterations": 1,
    }


async def _run_summary(conv, client, model: str, short: bool) -> dict:
    role = "summary_short" if short else "summary_long"
    formatted = _format_messages(conv, include_timestamp=False, window=(
        prompts.MESSAGES_PER_SHORT_SUMMARY if short else prompts.MESSAGES_PER_LONG_SUMMARY
    ))
    max_tokens = prompts.SHORT_SUMMARY_MAX_TOKENS if short else prompts.LONG_SUMMARY_MAX_TOKENS
    if short:
        output_words = int(min(1000, max_tokens) * 0.75)
    else:
        output_words = int(4000 * 0.75)
    prev = prompts.NO_PREVIOUS_SUMMARY
    if short:
        prompt = prompts.short_summary_prompt(formatted, output_words, prev)
    else:
        prompt = prompts.long_summary_prompt(formatted, output_words, prev)
    resp = await client.call(
        model=model, messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens, temperature=0.0,
    )
    text = resp.get("content", "").strip()
    return {
        "role": role,
        "conversation_id": conv.get("conversation_id", ""),
        "model": model,
        "raw_output": text,
        "parsed_output": {"summary": text},
        "tool_calls_made": 0,
        "iterations": 1,
    }


async def run_summary_short(conv, client, model: str) -> dict:
    return await _run_summary(conv, client, model, short=True)


async def run_summary_long(conv, client, model: str) -> dict:
    return await _run_summary(conv, client, model, short=False)


# ---------------------------------------------------------------------------
# Agentic roles
# ---------------------------------------------------------------------------
async def _run_agentic(conv, client, model, system_prompt, user_prompt, tools,
                       max_tool_iterations, role, extractor) -> dict:
    store = InMemoryStore(conv)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    loop = await run_tool_loop(
        client, messages, tools, store,
        model=model, max_tool_iterations=max_tool_iterations,
        max_tokens=8192, tool_choice=None,
    )
    parsed = extractor(store, loop)
    return {
        "role": role,
        "conversation_id": conv.get("conversation_id", ""),
        "model": model,
        "raw_output": loop.get("final_content", ""),
        "parsed_output": parsed,
        "tool_calls_made": loop.get("tool_calls_made", 0),
        "iterations": loop.get("iterations", 0),
        "done_token": loop.get("done_token", False),
        "truncated": loop.get("truncated", False),
    }


async def run_dialectic(conv, client, model: str, query: Optional[str] = None) -> dict:
    observed = _extract_peer_id(conv)
    observer = "user"
    system_prompt = prompts.dialectic_agent_system_prompt(
        observer, observed, observer_peer_card=None, observed_peer_card=None
    )
    q = query or f"What do we know about {observed}?"
    user_prompt = prompts.dialectic_user_prompt(q, prefetched_observations="")

    def extract(store, loop):
        return {"answer": loop.get("final_content", "")}

    return await _run_agentic(conv, client, model, system_prompt, user_prompt,
                              DIALECTIC_TOOLS, 5, "dialectic", extract)


async def run_deduction(conv, client, model: str, hints=None, peer_card=None) -> dict:
    observed = _extract_peer_id(conv)
    store_for_card = InMemoryStore(conv)
    sys = prompts.deduction_system_prompt(observed, peer_card_enabled=True)
    usr = prompts.deduction_user_prompt(observed, hints=hints, peer_card=peer_card)

    def extract(store, loop):
        created = [o["content"] for o in store.observations if o["level"] == "deductive"]
        return {
            "observations": created,
            "peer_card": list(store.peer_card),
            "observation_count": len(created),
        }

    return await _run_agentic(conv, client, model, sys, usr,
                              DEDUCTION_SPECIALIST_TOOLS, 12, "deduction", extract)


async def run_induction(conv, client, model: str, hints=None, peer_card=None) -> dict:
    observed = _extract_peer_id(conv)
    sys = prompts.induction_system_prompt(observed)
    usr = prompts.induction_user_prompt(observed, hints=hints)

    def extract(store, loop):
        created = [
            {"content": o["content"], "pattern_type": o.get("pattern_type"),
             "confidence": o.get("confidence"), "source_ids": o.get("source_ids", [])}
            for o in store.observations if o["level"] == "inductive"
        ]
        return {"observations": created, "observation_count": len(created)}

    return await _run_agentic(conv, client, model, sys, usr,
                              INDUCTION_SPECIALIST_TOOLS, 10, "induction", extract)


async def run_card_refresh(conv, client, model: str, rebuild: bool = False,
                           peer_card=None) -> dict:
    observed = _extract_peer_id(conv)
    store_for_card = InMemoryStore(conv)
    sys = prompts.card_refresh_system_prompt(observed, rebuild=rebuild)
    # For rebuild mode peer_card is None (not injected); for refresh, existing card.
    usr = prompts.card_refresh_user_prompt(observed, hints=None, peer_card=(None if rebuild else peer_card))

    def extract(store, loop):
        return {"peer_card": list(store.peer_card), "card_entries": len(store.peer_card)}

    return await _run_agentic(conv, client, model, sys, usr,
                              CARD_REFRESH_SPECIALIST_TOOLS, 6, "card_refresh", extract)


ROLE_RUNNERS = {
    "deriver": run_deriver,
    "summary_short": run_summary_short,
    "summary_long": run_summary_long,
    "dialectic": run_dialectic,
    "deduction": run_deduction,
    "induction": run_induction,
    "card_refresh": run_card_refresh,
}
