"""Adapt the Enron corpus into an OOLONG-**real** ``dnd/{split}.jsonl`` dataset
consumed by honcho's ``tests/bench/oolong.py`` runner (``--variant real``).

Runner data contract (docs/BENCH_RUNNER_SPEC.md §2.4, quoted from source,
``oolong_common.py:257-265``): real variant data lives at
``data_dir/dnd/{split}.jsonl``. Field surface the runner reads (oolong.py:270-289):

- ``id``                    → ``question_id``
- ``context_window_id``     → grouped/summarized under (part of result dict)
- ``question``              → asked of honcho
- ``answer``                → gold answer, parsed by ``parse_real_answer``
- ``question_type``         → used as ``task_group`` for the real variant
- ``context_window_text``   → parsed by ``parse_real_context_messages`` which
  splits on ``Speaker: text`` lines (oolong_common.py:363-425). Other fields
  (``context_len``, ``answer_type``, ``dataset``) are read defensively via
  ``get`` with defaults; ``context_len`` is recomputed if absent.

Because OOLONG-real is a long-context *aggregation* task that needs a truthful
aggregate answer per context — which short email threads mostly lack
(spec §6.4/§7) — this is a best-effort adapter. It encodes each labeled
conversation as one JSONL row whose context text is the thread rendered as
``Speaker: text`` lines and whose question/answer come from ``labels.qa[0]`` (a
concrete ask the thread actually supports). Conversations without any ``qa``
cannot produce a truthful answer and are skipped.

Output: ``data/oolong-real/dnd/test.jsonl``.

Run: ``python -m bench.adapters.oolong``. Idempotent (overwrites).
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

DEFAULT_OUT = ROOT / "data" / "oolong-real" / "dnd" / "test.jsonl"


def enron_to_oolong_real(
    split: str = "test",
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> list[dict]:
    """Build OOLONG-real JSONL row dicts from the Enron corpus.

    Returns a list of row dicts (each serialized to one JSON line). A row is
    produced only when the labeled conversation has at least one ``qa`` pair;
    the row's question is ``qa[0].q`` and answer is ``qa[0].a``.
    ``context_window_text`` renders every message as ``Speaker: text`` lines so
    ``parse_real_context_messages`` recovers the thread.

    ``context_len`` is supplied as a best-effort int meter (characters) so the
    ``--min-context-len/--max-context-len`` gates behave predictably; the runner
    recomputes it if it dislikes the value, so this is advisory only.
    """
    conversations, labels = load_enron(conversations_path, labels_path)
    rows: list[dict] = []

    for idx, conv in enumerate(labeled_conversations(conversations, labels)):
        cid = conv["conversation_id"]
        doc = conv["_labels"]
        qa_list = doc.get("qa", [])
        if not qa_list:
            continue  # no truthful gold question/answer for this thread

        qa0 = qa_list[0]
        context_lines = [
            f"{m.get('sender', 'unknown')}: {m.get('text', '')}"
            for m in conv.get("messages", [])
        ]
        context_text = "\n".join(context_lines)

        rows.append(
            {
                "id": cid,
                "context_window_id": f"{cid}__window",
                "context_len": len(context_text),
                "question": qa0["q"],
                "answer": qa0["a"],
                "question_type": "information_extraction",
                "answer_type": "varied",
                "dataset": "enron-honchobench",
                "context_window_text": context_text,
            }
        )

    return rows


def write_oolong_real(
    out_path: str | Path = DEFAULT_OUT,
    split: str = "test",
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> Path:
    """Write ``data/oolong-real/dnd/{split}.jsonl`` (one JSON object per line).

    Idempotent (overwrites).
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = enron_to_oolong_real(split, conversations_path, labels_path)
    with open(out, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    p = write_oolong_real()
    n = len(enron_to_oolong_real())
    print(f"wrote {n} OOLONG-real rows -> {p}")
