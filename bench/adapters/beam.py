"""Adapt the Enron corpus into the BEAM ``beam_data`` layout consumed by honcho's
``tests/bench/beam.py`` runner.

Runner data contract (docs/BENCH_RUNNER_SPEC.md §2.3, quoted from source,
``beam_common.py:78-121``):

    beam_data/{context_length}/{conversation_id}/
        chat.json                       # {"turns": [[ {role, content}, ... ]]} list-of-batches
        probing_questions/probing_questions.json   # {ability_name: [ {question, rubric:[...], answer|ideal_response|ideal_answer}, ... ]}

Additional runner behavior we must satisfy (verified in source):
- ``conversation_id`` must be a **digit-only** subdir name
  (``list_conversations`` filters ``d.name.isdigit()``, beam.py:118-121).
- ``chat.json`` is a JSON **list** of batches; each batch is a dict whose
  ``turns`` is a list of turn-groups, each group a list of ``{role, content}``
  (beam_common.py:138-163). We emit a single batch whose ``turns`` holds one
  group of all messages.
- `Abilities` = the keys of ``probing_questions.json``, one of the 10
  (beam.py:1-25). Question dicts are read via ``q_data["question"]``,
  ``q_data.get("rubric", [])``, and answer via ``q_data.get("answer")
  or q_data.get("ideal_response") or q_data.get("ideal_answer")``
  (beam.py:270-276).

BEAM is a poor fit for short email threads (it is tuned to 100K-token+
histories, spec §6.5), so this is a deliberately partial/best-effort adapter:
it builds the exact directory layout with valid files so the runner loads and
runs without crashing. We synthesize a handful of probing questions for three
low-bar abilities (``information_extraction``, ``summarization``,
``knowledge_update``) from the labels' qa + summaries, with ``rubric`` nuggets
derived from the gold answer.

Enron mapping:
- one digit subdir ``100K/{n}/`` per labeled conversation (stable index).
- ``chat.json`` turns = each message as ``{"role": user|assistant}`` where the
  ``target_peer`` is ``user`` and every other sender is ``assistant`` — matching
  the role-mapping used everywhere else and the runner's ``role``/``content`` read.
- probing questions:
    * ``information_extraction`` ← labels.qa (answer = gold answer, rubric = gold).
    * ``summarization`` ← labels.short_summary (one question + the summary as answer).
    * ``knowledge_update`` ← the labels' explicit_facts surfaced as a
      knowledge-accepting question (rubric = the facts).

Output: ``data/beam/100K/{n}/...`` for each labeled conversation.

Run: ``python -m bench.adapters.beam``. Idempotent (overwrites the whole
``data/beam`` tree each run).
"""

from __future__ import annotations

import json
from pathlib import Path

from ._common import (
    DEFAULT_CONVERSATIONS,
    DEFAULT_LABELS,
    ROOT,
    labeled_conversations,
    load_enron,
)

DEFAULT_DATA_DIR = ROOT / "data" / "beam"
CONTEXT_LENGTH = "100K"

# Abilities we synthesize. (Full list in beam.py:1-25; we use a low-bar subset
# because these are short email threads.)
SYNTH_ABILITIES = (
    "information_extraction",
    "summarization",
    "knowledge_update",
)


def _role(conv: dict, sender: str) -> str:
    """Map an Enron sender to a BEAM role: target_peer→user, else assistant."""
    return "user" if sender == conv.get("target_peer", "") else "assistant"


def enron_to_beam(
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> dict[int, dict[str, object]]:
    """Build the full beam_data dict keyed by digit conversation id.

    Returns ``{ digit_id: {"chat": <list-of-batches>, "questions": {...}} }``.
    """
    conversations, labels = load_enron(conversations_path, labels_path)
    out: dict[int, dict[str, object]] = {}

    for n, conv in enumerate(labeled_conversations(conversations, labels), start=1):
        doc = conv["_labels"]

        # chat.json: single batch, single turn-group of all messages.
        turns = [
            {
                "role": _role(conv, m.get("sender", "")),
                "content": m.get("text", ""),
            }
            for m in conv.get("messages", [])
        ]
        chat = [{"turns": [turns]}]

        # probing questions -------------------------------------------------
        questions: dict[str, list[dict]] = {
            "information_extraction": [],
            "summarization": [],
            "knowledge_update": [],
        }

        # information_extraction from qa gold pairs.
        for qa in doc.get("qa", []):
            questions["information_extraction"].append(
                {
                    "question": qa["q"],
                    "rubric": [qa["a"]],
                    "answer": qa["a"],
                }
            )

        # summarization from short_summary.
        summary = doc.get("short_summary", "")
        if summary:
            questions["summarization"].append(
                {
                    "question": (
                        "Summarize the key facts, decisions, and outlook of "
                        "this business correspondence in a concise paragraph."
                    ),
                    "rubric": [summary],
                    "answer": summary,
                }
            )

        # knowledge_update from explicit_facts.
        facts = doc.get("explicit_facts", [])
        if facts:
            questions["knowledge_update"].append(
                {
                    "question": (
                        "Given the conversation, list the concrete facts about "
                        "the correspondent's role, actions, and stated positions."
                    ),
                    "rubric": facts,
                    "answer": " | ".join(facts),
                }
            )

        # Drop empty ability groups (runner iterates whatever is there).
        questions = {k: v for k, v in questions.items() if v}

        out[n] = {"chat": chat, "questions": questions}

    return out


def write_beam(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> list[Path]:
    """Write ``data/beam/100K/{n}/chat.json`` + probing_questions per thread.

    Idempotent-safe: the whole ``data/beam`` tree is rebuilt on each run so
    stale per-thread dirs are cleared.
    """
    root = Path(data_dir)
    base = root / CONTEXT_LENGTH
    if base.exists():
        import shutil

        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    by_id = enron_to_beam(conversations_path, labels_path)
    written: list[Path] = []
    for n, block in sorted(by_id.items()):
        conv_dir = base / str(n)
        pq_dir = conv_dir / "probing_questions"
        conv_dir.mkdir(parents=True, exist_ok=True)
        pq_dir.mkdir(parents=True, exist_ok=True)

        chat = conv_dir / "chat.json"
        chat.write_text(json.dumps(block["chat"], indent=2, ensure_ascii=False))
        written.append(chat)

        q = pq_dir / "probing_questions.json"
        q.write_text(json.dumps(block["questions"], indent=2, ensure_ascii=False))
        written.append(q)

    return written


if __name__ == "__main__":
    files = write_beam()
    print(f"wrote beam data -> {DEFAULT_DATA_DIR}")
    for f in files:
        print(f"  {f.relative_to(ROOT)}")
