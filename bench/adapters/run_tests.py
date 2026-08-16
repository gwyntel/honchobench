"""Adapt the Enron corpus into honcho `run_tests.py` test JSON.

run_tests.py format (from honcho tests/bench/README.md):

    {
      "sessions": {
        "<session_id>": {
          "messages": [ {"peer": "<sender>", "content": "<text>"}, ... ]
        }
      },
      "queries": [
        {
          "query": "<question>",
          "expected_response": "<gold answer>",
          "session": "<session_id>",   // optional
          "peer": "<asking peer>"      // optional
        }
      ]
    }

Enron labels give us `qa: [{q, a, evidence}]` per labeled conversation, and
conversations give `messages: [{sender, text}]` + `target_peer`. Mapping:
  session_id   = conversation_id
  messages     = [{peer: sender, content: text}]
  query        = q
  expected     = a
  session      = conversation_id
  peer         = target_peer (who the conversation is about / who is asked)
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_CONVERSATIONS = Path("data/dataset/conversations.json")
DEFAULT_LABELS = Path("data/dataset/labels.json")


def load_enron(conversations_path=None, labels_path=None) -> tuple[list, dict]:
    conv_path = Path(conversations_path) if conversations_path else DEFAULT_CONVERSATIONS
    lab_path = Path(labels_path) if labels_path else DEFAULT_LABELS
    conversations = json.loads(conv_path.read_text())
    labels = json.loads(lab_path.read_text())
    return conversations, labels


def enron_to_run_tests(
    conversations_path=None,
    labels_path=None,
    *,
    labeled_only: bool = True,
) -> dict:
    """Build ONE combined run_tests.py test object from the Enron corpus.

    Returns a dict with `sessions` (all labeled conversations) and `queries`
    (all qa items mapped to their session + peer).
    """
    conversations, labels = load_enron(conversations_path, labels_path)

    sessions: dict[str, dict] = {}
    queries: list[dict] = []

    for conv in conversations:
        cid = conv["conversation_id"]
        if labeled_only and cid not in labels:
            continue

        sessions[cid] = {
            "messages": [
                {"peer": m["sender"], "content": m["text"]} for m in conv["messages"]
            ]
        }

        if cid in labels:
            for qa in labels[cid].get("qa", []):
                queries.append(
                    {
                        "query": qa["q"],
                        "expected_response": qa["a"],
                        "session": cid,
                        "peer": conv.get("target_peer", ""),
                    }
                )

    return {"sessions": sessions, "queries": queries}


def write_run_tests(out_dir, conversations_path=None, labels_path=None) -> list[Path]:
    """Write per-conversation test files (honcho runs `--tests-dir` over a dir).

    Emits one JSON per labeled conversation under out_dir, so each conversation
    can be scored independently, plus a combined `_all.json`.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    conversations, labels = load_enron(conversations_path, labels_path)
    written: list[Path] = []

    for conv in conversations:
        cid = conv["conversation_id"]
        if cid not in labels:
            continue
        test = {
            "sessions": {
                cid: {
                    "messages": [
                        {"peer": m["sender"], "content": m["text"]}
                        for m in conv["messages"]
                    ]
                }
            },
            "queries": [
                {
                    "query": qa["q"],
                    "expected_response": qa["a"],
                    "session": cid,
                    "peer": conv.get("target_peer", ""),
                }
                for qa in labels[cid].get("qa", [])
            ],
        }
        p = out / f"{cid}.json"
        p.write_text(json.dumps(test, indent=2))
        written.append(p)

    # combined
    combined = enron_to_run_tests(conversations_path, labels_path)
    p_all = out / "_all.json"
    p_all.write_text(json.dumps(combined, indent=2))
    written.append(p_all)

    return written


if __name__ == "__main__":
    files = write_run_tests("data/run_tests")
    print(f"wrote {len(files)} test files to data/run_tests/")
    for f in files:
        print(f"  {f.name}")
