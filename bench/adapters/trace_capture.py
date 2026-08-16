"""Produce honcho-style **traces** for the molecular / coverage trace evaluators
(``tests/bench/molecular.py``, ``tests/bench/coverage.py``).

These two are post-hoc *trace evaluators*, not runners: they score honcho's own
**deriver extraction output** on a per-conversation trace, not gold answers
(docs/BENCH_RUNNER_SPEC.md §2.5/§2.6, §6.3). The trace data contract (quoted
from source, ``runner_common.py``):

- ``load_traces`` (294-341): a JSONL file (one JSON object/line) is accepted.
- ``extract_propositions`` (344-362): reads
  ``trace["output"]["content"]["explicit"]`` → a list of
  ``{"content": "<claim>"}``; each claim string becomes a proposition.
- ``extract_messages`` (365-389): parses ``trace["input"]["prompt"]`` for a
  ``<messages>...</messages>`` block of ``speaker: text`` lines.
- ``extract_conversation_id`` (405-407): ``trace["conversation_id"]`` or
  ``trace["id"]`` or ``trace_{index:04d}``.

Parser detail we must satisfy (source-verified): each ``<messages>`` line is split
with ``line.split(\" \", 3)`` and the **speaker must land at index 2** (stripped of
a trailing ``:``), with the text at index 3. So the raw lines must be laid out as
``0 <i> <speaker>: <text>`` (two leading tokens, then ``speaker:``, then text).
A bare ``speaker: text`` line yields only 2 tokens and is silently skipped. We
therefore emit the index-prefixed form so every message round-trips through the
parser; this mirrors the field-rich prompts honcho's deriver actually produces
(``src/deriver/prompts.py:81-87`` renders ``Target peer:`` + ``<messages>``).

**What this stand-in does (documented, deliberate):** capturing *live* deriver
output requires ingesting each conversation into a running honcho stack and
saving the deriver's per-conversation trace. To give molecular/coverage valid
local input without a stack, we populate ``output.content.explicit`` with the
conversation's **gold** ``explicit_facts`` from ``labels.json`` — i.e. we stand
the curated gold facts in for the deriver's claimed extraction. This is a proxy
for scoring purposes only. A live-capture path (ingest → read deriver trace)
can swap in later by replacing ``capture_traces()``'s proposition source while
keeping the exact trace schema below.

Output: ``data/traces/enron_traces.jsonl`` (one JSON object per line).

Run: ``python -m bench.adapters.trace_capture``. Idempotent (overwrites).
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

DEFAULT_OUT = ROOT / "data" / "traces" / "enron_traces.jsonl"


def _build_input_prompt(conv: dict, target_peer: str) -> str:
    """Render the input prompt with a ``<messages>`` block the parser recovers.

    Lines use the index-prefixed form ``0 <i> <speaker>: <text>`` so
    ``extract_messages`` puts the sender at index 2 (see module docstring).

    Enron ``text`` fields are multi-line (quoted threads), and the parser reads
    *each physical line* of the ``<messages>`` block as one message — so we
    collapse embedded newlines within a message to spaces to keep exactly one
    logical message per physical line. Without this, continuation lines would be
    mis-parsed as spurious speaker turns.
    """
    lines = []
    for i, m in enumerate(conv.get("messages", []), 1):
        speaker = m.get("sender", "unknown")
        text = m.get("text", "").replace("\r", " ").replace("\n", " ")
        lines.append(f"0 {i} {speaker}: {text}")
    body = "\n".join(lines)
    return (
        "Target peer:\n"
        f"{target_peer}\n\n"
        "Messages to analyze:\n"
        "<messages>\n"
        f"{body}\n"
        "</messages>"
    )


def capture_traces(
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> list[dict]:
    """Build the trace list (one dict per labeled conversation) for the evaluators.

    Each trace carries:
      - ``conversation_id``  → used by ``extract_conversation_id``.
      - ``input.prompt``     → the ``<messages>``-bearing prompt above.
      - ``output.content.explicit`` → ``[{content: <gold fact>}, ...]`` — the
        GOLD explicit_facts standing in for live deriver extraction (proxy).
    """
    conversations, labels = load_enron(conversations_path, labels_path)
    traces: list[dict] = []

    for conv in labeled_conversations(conversations, labels):
        cid = conv["conversation_id"]
        target_peer = conv.get("target_peer", "")
        facts = conv["_labels"].get("explicit_facts", [])

        traces.append(
            {
                "conversation_id": cid,
                "input": {
                    "prompt": _build_input_prompt(conv, target_peer),
                },
                "output": {
                    "content": {
                        "explicit": [{"content": f} for f in facts],
                    }
                },
            }
        )

    return traces


def write_traces(
    out_path: str | Path = DEFAULT_OUT,
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> Path:
    """Write ``data/traces/enron_traces.jsonl`` (one JSON object per line).

    Idempotent (overwrites).
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    traces = capture_traces(conversations_path, labels_path)
    with open(out, "w") as f:
        for t in traces:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    p = write_traces()
    n = len(capture_traces())
    print(f"wrote {n} traces -> {p}")
