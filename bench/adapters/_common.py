"""Shared helpers for the Enron -> honcho runner dataset adapters.

Keeps the per-runner adapter modules focused on their schema by providing a
single place to load the Enron source corpus and convert it into the common
"conversation" shape that every adapter builds on.

Source data shapes (docs/BENCH_RUNNER_SPEC.md §6.1):

- ``data/dataset/conversations.json`` — array of 19, each
  ``{ conversation_id, source, messages: [{ sender, text }], target_peer }``.
- ``data/dataset/labels.json`` — object keyed by conversation_id (8 labeled
  threads); each label doc:
  ``{ explicit_facts[], qa[{q,a,evidence}], deductive[], inductive[],
       short_summary, long_summary }``.

These helpers are pure local transformations — no network, no secrets, no
spending tokens. Every adapter is idempotent: running it again overwrites the
output files cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path

# Repo root = parent of bench/adapters. Keeps paths correct no matter the CWD.
ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_CONVERSATIONS = ROOT / "data" / "dataset" / "conversations.json"
DEFAULT_LABELS = ROOT / "data" / "dataset" / "labels.json"

# The label document keys, mirroring §6.1. Kept in one place for clarity.
QA_FIELDS = ("q", "a", "evidence")
LABEL_KEYS = ("explicit_facts", "qa", "deductive", "inductive",
              "short_summary", "long_summary")


def load_enron(
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """Load conversations (list) and labels (dict keyed by conversation_id).

    Returns ``(conversations, labels)``. ``conversations`` is the full 19-item
    list; ``labels`` is the object keyed by conversation_id (only labeled
    threads, 8 items). Every label key is present on every label doc.
    """
    conv_path = Path(conversations_path) if conversations_path else DEFAULT_CONVERSATIONS
    lab_path = Path(labels_path) if labels_path else DEFAULT_LABELS

    conversations = json.loads(conv_path.read_text())
    labels = json.loads(lab_path.read_text())
    if not isinstance(conversations, list):
        raise ValueError(f"{conv_path} must contain a JSON array of conversations")
    if not isinstance(labels, dict):
        raise ValueError(f"{lab_path} must contain a JSON object keyed by conversation_id")

    for cid, doc in labels.items():
        if not isinstance(doc, dict):
            raise ValueError(f"label {cid!r} must be an object")
        for k in LABEL_KEYS:
            if k not in doc:
                raise ValueError(f"label {cid!r} missing required key {k!r}")

    return conversations, labels


def labeled_conversations(
    conversations: list[dict], labels: dict[str, dict]
) -> list[dict]:
    """Return only conversations that have a label document, in corpus order.

    Adapters generally operate over the *labeled* subset (they need a
    question/answer/evidence to build runner rows). This preserves corpus order
    and attaches the label doc as ``conv["_labels"]`` (never written to output).
    """
    result: list[dict] = []
    for conv in conversations:
        cid = conv["conversation_id"]
        if cid in labels:
            result.append({**conv, "_labels": labels[cid]})
    return result


def most_frequent_other_sender(
    conv: dict, target_peer: str, other: str | None = None
) -> str:
    """Pick speaker_b: the most frequent sender other than ``target_peer``.

    Returns the highest-frequency distinct sender not equal to ``target_peer``.
    Falls back to any current ``other``, else ``target_peer`` itself (a
    one-speaker thread collapses gracefully).
    """
    counts: dict[str, int] = {}
    for m in conv.get("messages", []):
        sender = m.get("sender", "")
        if sender == target_peer:
            continue
        counts[sender] = counts.get(sender, 0) + 1
    if not counts:
        return other or target_peer
    # deterministic: most frequent, tie-broken by lexicographically smallest
    best = max(sorted(counts.keys()), key=lambda s: counts[s])
    return best
