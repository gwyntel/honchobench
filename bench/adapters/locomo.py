"""Adapt the Enron corpus into the LoCoMo conversation + QA format consumed by
honcho's ``tests/bench/locomo.py`` runner.

Runner data contract (docs/BENCH_RUNNER_SPEC.md §2.2, quoted from source,
``locomo_common.py:100-111`` + :143-171). *Required shape of a conversation
item*:

    {
      "sample_id": "sample_0",
      "conversation": {
        "speaker_a": "Caroline",
        "speaker_b": "Melanie",
        "session_1": [ {"speaker":"Caroline","text":"...","dia_id":"D1:3","blip_caption":null,"img_url":[],"query":null}, ... ],
        "session_1_date_time": "1:56 pm on 8 May, 2023",   // "%I:%M %p %d %B, %Y"
        "session_2": [...], "session_2_date_time": "...", ...    // any number
      },
      "qa": [
        {"question":"...","answer":"...","category":1,"evidence":["D1:3","D2:8"]}
      ]
    }

Additional runner behavior we must satisfy (verified in locomo.py / locomo_common.py):
- ``category`` 1-5 maps to "single_hop,multi_hop,temporal,commonsense,adversarial";
  category **5 is dropped by default** (exclude_adversarial=True, locomo.py:356-360),
  so we never emit category 5.
- ``evidence`` ``dia_id`` refs resolve to ``msg["dia_id"]`` to hand the judge the
  evidence block (locomo_common.py:209-244).
- ``ingest_messages`` only ingests messages whose ``speaker`` is *exactly*
  ``speaker_a`` or ``speaker_b`` (locomo.py:294-305) — so we map every Enron
  message onto exactly one of the two speakers (see below).

Enron mapping:
- ``speaker_a`` = ``target_peer`` (the subject of the thread).
- ``speaker_b`` = the most frequent other sender (spec §6.4). Because Enron
  threads have many senders and LoCoMo only models two interlocutors, **every
  message from a sender other than ``target_peer`` is re-labeled ``speaker_b``**
  (the \"other\"/assistant role of the thread). This keeps the full message text
  ingested on the two peers LoCoMo creates; it is a deliberate data-alignment
  decision, documented here rather than silently dropping content.
- ``session_1`` = all of a conversation's messages (single session). ``dia_id``
  assigned ``D1:1, D1:2, ...`` sequentially in message order.
- ``qa`` = labels.qa mapped with:
    * ``category`` heuristic: ``1`` single-hop by default; ``3`` temporal if the
      question or answer/evidence mentions a calendar date or a time phrase;
      ``2`` multi-pop if the evidence quote spans more than one source message
      is unknowable from the labels, so we default single-hop (1) and only bump
      to 3 on date evidence. (Category 5 never produced.)
    * ``evidence`` = the ``dia_id``(s) of the source message(s) we can locate:
      if the labels' ``evidence`` quote appears in exactly one message, use that
      ``dia_id``; otherwise empty list (the judge tolerates missing evidence).
- ``session_1_date_time`` = synthetic, stable LoCoMo date in the parser format
  ``"%I:%M %p %d %B, %Y"``.

Output: ``data/locomo/locomo10.json`` (a JSON list of conversation items).

Run: ``python -m bench.adapters.locomo``. Idempotent (overwrites).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ._common import (
    DEFAULT_CONVERSATIONS,
    DEFAULT_LABELS,
    ROOT,
    labeled_conversations,
    load_enron,
    most_frequent_other_sender,
)

DEFAULT_OUT = ROOT / "data" / "locomo" / "locomo10.json"

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Heuristic markers for a "date-bearing" question → temporal category (3).
_DATE_RE = re.compile(
    r"\b(19|20)\d{2}\b"
    r"|\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b"
    r"|(\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\.?|\d{1,2}/\d{1,2})",
    re.IGNORECASE,
)


def _stable_locomo_date(conversation_id: str) -> str:
    """Deterministic synthetic date parseable by ``parse_locomo_date``
    (\"%I:%M %p %d %B, %Y\", e.g. \"1:56 pm on 8 May, 2023\")."""
    h = hashlib.sha256(conversation_id.encode()).hexdigest()
    month = int(h[:2], 16) % 12
    day = 1 + (int(h[2:6], 16) % 28)
    hour12 = 1 + (int(h[6:8], 16) % 12)
    minute = int(h[8:10], 16) % 60
    ampm = "am" if (int(h[10:11], 16) % 2) == 0 else "pm"
    return f"{hour12}:{minute:02d} {ampm} on {day} {_MONTHS[month]}, 2001"


def _locate_evidence_dia_ids(
    messages: list[dict], evidence: str
) -> list[str]:
    """Return the dia_ids of source messages that best contain the evidence quote.

    The label ``evidence`` is a quote pulled from (usually) one source message.
    We score each message by how many of the evidence's significant tokens appear
    in it, keep those above a threshold, and return their ``dia_id``s (multi-hop
    when several messages corroborate the quote). Returns [] if nothing overlaps.
    """
    if not evidence:
        return []
    ev_tokens = [t.lower() for t in evidence.split() if len(t) > 3]
    if not ev_tokens:
        return []
    ev_set = set(ev_tokens)
    scored: list[tuple[int, str]] = []
    for m in messages:
        msg_tokens = {t.lower() for t in m["text"].split() if len(t) > 3}
        overlap = len(ev_set & msg_tokens)
        if overlap:
            scored.append((overlap, m["dia_id"]))
    if not scored:
        return []
    best = max(o for o, _ in scored)
    # Keep messages within 2 tokens of the best overlap (the corroborating ones).
    return [d for o, d in scored if o >= best - 2]


def _category_for(q: str, a: str, evidence: str) -> int:
    """Heuristic locomo category: 3=temporal on date markers, else 1=single_hop."""
    blob = f"{q} {a} {evidence}"
    if _DATE_RE.search(blob):
        return 3
    return 1


def _build_conversation(conv: dict, cid: str) -> dict:
    """Build the LoCoMo ``conversation`` block for one Enron thread."""
    target_peer = conv.get("target_peer", "")
    speaker_b = most_frequent_other_sender(conv, target_peer)
    speaker_a = target_peer or "user"

    messages: list[dict] = []
    for i, m in enumerate(conv.get("messages", []), 1):
        speaker = m.get("sender", "")
        role_speaker = speaker_a if speaker == target_peer else speaker_b  # the "other" role
        messages.append(
            {
                "speaker": role_speaker,
                "text": m.get("text", ""),
                "dia_id": f"D1:{i}",
                "blip_caption": None,
                "img_url": [],
                "query": None,
            }
        )

    return {
        "speaker_a": speaker_a,
        "speaker_b": speaker_b,
        "session_1": messages,
        "session_1_date_time": _stable_locomo_date(cid),
    }


def enron_to_locomo(
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> list[dict]:
    """Transform Enron conversations + labels into the LoCoMo JSON list.

    Returns a list of conversation items (sample_id / conversation / qa) per
    §2.2. ``labels.qa`` feeds ``qa[]``; the gold answers become ``answer``.
    """
    conversations, labels = load_enron(conversations_path, labels_path)
    items: list[dict] = []

    for idx, conv in enumerate(labeled_conversations(conversations, labels)):
        cid = conv["conversation_id"]
        conversation = _build_conversation(conv, cid)
        doc = conv["_labels"]

        qa: list[dict] = []
        for qa_item in doc.get("qa", []):
            q = qa_item["q"]
            a = qa_item["a"]
            evidence = qa_item.get("evidence", "")
            evidence_ids = _locate_evidence_dia_ids(
                conversation["session_1"], evidence
            )
            qa.append(
                {
                    "question": q,
                    "answer": a,
                    "category": _category_for(q, a, evidence),
                    "evidence": evidence_ids,
                }
            )

        items.append(
            {
                "sample_id": f"sample_{idx}",
                "conversation": conversation,
                "qa": qa,
            }
        )

    return items


def write_locomo(
    out_path: str | Path = DEFAULT_OUT,
    conversations_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> Path:
    """Write ``data/locomo/locomo10.json``. Idempotent (overwrites)."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    items = enron_to_locomo(conversations_path, labels_path)
    out.write_text(json.dumps(items, indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    p = write_locomo()
    n = len(enron_to_locomo())
    print(f"wrote {n} LoCoMo conversation items -> {p}")
