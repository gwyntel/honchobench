#!/usr/bin/env python3
"""Finalize conversations.json + manifest.json from the subagent's curated conv_final.json."""
import json, hashlib
from pathlib import Path
from datetime import datetime, timezone

SRC = Path("/home/hermes/projects/honchobench/data/raw/working/conv_final.json")
OUT = Path("/home/hermes/projects/honchobench/data/dataset")

convos = json.load(open(SRC))
# Strip internal helper fields; keep only schema keys
keep = {"conversation_id", "source", "messages", "target_peer"}
clean = []
for c in convos:
    clean.append({
        "conversation_id": c["conversation_id"],
        "source": c["source"],
        "messages": c["messages"],
        "target_peer": c["target_peer"],
    })

# Deterministic ordering by conversation_id
clean.sort(key=lambda c: c["conversation_id"])
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "conversations.json").write_text(json.dumps(clean, indent=2))

# Build manifest
total_msgs = sum(len(c["messages"]) for c in clean)
senders = sorted({m["sender"] for c in clean for m in c["messages"]})
by_target = {}
for c in clean:
    by_target.setdefault(c["target_peer"], 0)
    by_target[c["target_peer"]] += 1

manifest = {
    "dataset": "honchobench-enron-slice",
    "description": "Info-dense slice of the CMU Enron email corpus reconstructed into conversations for benchmarking honcho's LLM roles.",
    "source": {
        "name": "CMU Enron Email Corpus (enron_mail_20150507)",
        "url": "https://www.cs.cmu.edu/~./enron/enron_mail_20150507.tar.gz",
        "tarball_sha1": hashlib.sha1(Path("/home/hermes/projects/honchobench/data/raw/enron_mail_20150507.tar.gz").read_bytes()).hexdigest(),
        "tarball_size_bytes": Path("/home/hermes/projects/honchobench/data/raw/enron_mail_20150507.tar.gz").stat().st_size,
    },
    "built_at": datetime.now(timezone.utc).isoformat(),
    "selection_rationale": "8-12 maildirs of senior Enron executives and their assistants chosen for dense, real business relationships (Skilling, Kean, Shackleton, Kitchen, Taylor, Dasovich, Jones, Lay). Threads reconstructed by Message-ID/In-Reply-To/References where present, else by (sender,recipient,subject). Quoting boilerplate stripped; content-level dedup applied.",
    "stats": {
        "conversations": len(clean),
        "messages": total_msgs,
        "unique_senders": len(senders),
        "conversations_by_target_peer": by_target,
    },
    "schema": "see CONVENTIONS.md",
}

(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))

# Validate schema
bad = []
for c in clean:
    if not all(k in c for k in ("conversation_id", "source", "messages", "target_peer")):
        bad.append(c.get("conversation_id", "?"))
    if c["source"] != "enron":
        bad.append(c["conversation_id"])
    for m in c["messages"]:
        if not all(k in m for k in ("sender", "text")):
            bad.append(c["conversation_id"])
print("wrote conversations.json:", len(clean), "convos,", total_msgs, "messages")
print("manifest.json written")
print("schema violations:", bad if bad else "none")
