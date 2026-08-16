"""Adapt the Enron corpus into the LongMemEval question list consumed by honcho's
``tests/bench/longmem.py`` runner.

Runner data contract (docs/BENCH_RUNNER_SPEC.md §2.1, quoted from source,
``longmem_common.py:load_test_file`` 126-136) — the file is a **list of question
dicts**:

    {
      "question_id": "q123",             // "_abs" suffix → abstention branch
      "question_type": "single-session-user|single-session-assistant|multi-session|temporal-reasoning|knowledge-update|single-session-preference",
      "question": "...",
      "answer": "expected answer or rubric string",
      "haystack_sessions": [ [ {"role":"user|assistant","content":"..."}, ... ], ... ],
      "haystack_session_ids": ["...", ...],
      "haystack_dates": ["2023/05/20 (Sat) 02:21", ...],
      "question_date": "..."              // optional; prefixed "[date] " onto question
    }

Every field the runner reads is produced here (verified against longmem.py
:189-193, :249-254, :428-430 and longmem_common.py:97-123). ``haystack_sessions``
holds one list per conversation session; each session is a list of
``{"role": "user"|"assistant", "content": ...}`` pairs.

Enron mapping:
- ``haystack_sessions`` = the conversation's ``messages`` mapped to roles: the
  ``target_peer`` is the **user**, every other sender is the **assistant**
  (honcho's LongMemEval ingestion creates exactly a ``user`` and an
  ``assistant`` peer, longmem.py:198-199).
- ``answer`` = the gold answer (``qa[i].a``, or a coerced deduction/inference).
- ``question_type`` synthesized by heuristic:
    * each ``qa`` item → ``single-session-user`` unless its ``evidence`` echos a
      quote spanning the whole thread, in which case ``multi-session`` (we use
      the conversation's single session; this keeps the type *honest* while
      only ever feeding the one haystack session the thread maps to);
    * ``deductive[i]`` → ``knowledge-update`` (closest to honcho's
      dream/deduction slot, section §6.2);
    * ``inductive[i]`` → ``single-session-preference`` (character-trait
      inference).
- ``haystack_dates`` = a synthetic, stable LongMemEval date string in the
  parser's exact format ``YYYY/MM/DD (Day) HH:MM`` (longmem_common.py:97-123),
  derived deterministically from the ``conversation_id`` so re-runs are stable.
- ``question_id`` embeds the ``conversation_id`` for per-thread traceability.

Output: ``data/longmem/longmemeval_oracle.json`` (a JSON list).

Run: ``python -m bench.adapters.longmem``. Idempotent (overwrites).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ._common import (
    DEFAULT_CONVERSATIONS,
    DEFAULT_LABELS,
    ROOT,
    labeled_conversations,
    load_enron,
)

DEFAULT_OUT = ROOT / "data" / "longmem" / "longmemeval_oracle.json"

# Valid question_type values understood by the runner (longmem.py + spec §2.1).
QUESTION_TYPES = (
    "single-session-user",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
    "single-session-preference",
)

# Weekday name used in the synthetic haystack date; the parser only needs the
# "(Sat)" slot to split on (longmem_common.py:112-117), so we fix one letter pair.
_DATE_DAY = "Sat"


def _stable_date(conversation_id: str, slot: int = 0) -> str:
    """Deterministic synthetic LongMemEval date ``YYYY/MM/DD (Day) HH:MM``.

    Parsed by ``longmem_common.parse_longmemeval_date`` with
    ``%Y/%m/%d %H:%M``. Month/day/hour are hashed from the id so the derived
    ordering is stable across runs but non-trivial; day is capped at 28 so every
    output is a real calendar date.
    """
    h = hashlib.sha256(f"{conversation_id}:{slot}".encode()).hexdigest()
    month = 1 + (int(h[:2], 16) % 12)
    day = 1 + (int(h[2:6], 16) % 28)
    hour = int(h[8:12], 16) % 24
    minute = int(h[12:14], 16) % 60
    return f"2001/{month:02d}/{day:02d} ({_DATE_DAY}) {hour:02d}:{minute:02d}"


def _messages_to_haystack(conv: dict, target_peer: str) -> list[dict]:
    """Map Enron messages to [{role, content}] with target_peer as 'user'."""
    return [
        {
            "role": "user" if m.get("sender") == target_peer else "assistant",
            "content": m.get("text", ""),
        }
        for m in conv.get("messages", [])
    ]


def _question_type_for(kind: str, evidence: str) -> str:
    """Heuristic question_type (see module docstring for rationale).

    ``kind`` is the short label passed by ``_question`` ("qa" / "ded" / "ind");
    we also accept the full names for robustness.
    """
    if kind in ("ded", "deductive"):
        return "knowledge-update"
    if kind in ("ind", "inductive"):
        return "single-session-preference"
    # qa: single-session-user unless evidence appears to span the whole thread.
    if evidence and len(evidence) > 80 and "\n" in evidence:
        return "multi-session"
    return "single-session-user"


def _question(
    conversation_id: str,
    kind: str,
    idx: int,
    question: str,
    answer: str,
    evidence: str,
    haystack: list[dict],
) -> dict:
    """Build one LongMemEval question dict from a labeled item."""
    return {
        "question_id": f"{conversation_id}__{kind}{idx}",
        "question_type": _question_type_for(kind, evidence),
        "question": question,
        "answer": answer,
        "haystack_sessions": [haystack],
        "haystack_session_ids": [conversation_id],
        "haystack_dates": [_stable_date(conversation_id)],
    }


def enron_to_longmem(
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> list[dict]:
    """Transform Enron qa + deductive + inductive into a LongMemEval question list.

    Returns a JSON-list-ready sequence of question dicts (one per labeled
    question / deduction / inference) per §2.1.
    """
    conversations, labels = load_enron(conversations_path, labels_path)
    questions: list[dict] = []

    for conv in labeled_conversations(conversations, labels):
        cid = conv["conversation_id"]
        target_peer = conv.get("target_peer", "")
        haystack = _messages_to_haystack(conv, target_peer)
        doc = conv["_labels"]

        for i, qa in enumerate(doc.get("qa", [])):
            questions.append(
                _question(
                    cid, "qa", i, qa["q"], qa["a"], qa.get("evidence", ""), haystack
                )
            )
        for i, ded in enumerate(doc.get("deductive", [])):
            questions.append(
                _question(cid, "ded", i, ded, ded, "", haystack)
            )
        for i, ind in enumerate(doc.get("inductive", [])):
            questions.append(
                _question(cid, "ind", i, ind, ind, "", haystack)
            )

    return questions


def write_longmem(
    out_path: str | Path = DEFAULT_OUT,
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> Path:
    """Write ``data/longmem/longmemeval_oracle.json`` (a JSON list). Idempotent."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    questions = enron_to_longmem(conversations_path, labels_path)
    out.write_text(json.dumps(questions, indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    p = write_longmem()
    n = len(enron_to_longmem())
    print(f"wrote {n} LongMemEval questions -> {p}")
