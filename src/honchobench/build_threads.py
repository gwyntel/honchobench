#!/usr/bin/env python3
"""Reconstruct email threads from the extracted Enron index and assemble conversations.json."""
import json
import re
from collections import defaultdict
from pathlib import Path

RAW = Path("/home/hermes/projects/honchobench/data/raw")
OUT = Path("/home/hermes/projects/honchobench/data/dataset")

OWNER = {
    "LAY-K": "kenneth.lay@enron.com",
    "SHACKLETON-S": "sara.shackleton@enron.com",
    "SKILLING-J": "jeff.skilling@enron.com",
    "KITCHEN-L": "louise.kitchen@enron.com",
    "WHALLEY-G": "greg.whalley@enron.com",
    "WHALLEY-L": "greg.whalley@enron.com",
    "KEAN-S": "steven.kean@enron.com",
    "DASOVICH-J": "jeff.dasovich@enron.com",
    "LOKAY-M": "mark.lokay@enron.com",
    "TAYLOR-M": "mark.taylor@enron.com",
    "JONES-T": "tana.jones@enron.com",
}

with open(RAW / "working/index.json") as f:
    emails = json.load(f)

# Emails have keys: path, origin, from_addr, from_display, to_addrs, subject,
# subject_norm, date_str, ts, message_id, body, maildir, origin_email


def normalize_addr(a):
    a = (a or "").lower().strip()
    # strip display-name-wrapped forms, keep stable email
    return a


def clean_body(body):
    if not body:
        return ""
    # Work line by line, drop pure quoting boilerplate
    lines = body.split("\n")
    kept = []
    for ln in lines:
        s = ln.rstrip("\n").rstrip()
        if not s.strip():
            if kept and kept[-1] == "":
                continue
            kept.append("")
            continue
        # Skip lines that are pure quoted forwarding headers/edges
        if re.match(r"^>+\s{0,3}(from|sent|to|cc|subject|date):", s, re.I):
            continue
        if re.match(r"^-----+\s*(original message|forwarded message|reply message)", s, re.I):
            continue
        kept.append(s)
    text = "\n".join(kept)

    # Cut trailing quoted reply chains: drop everything from a repeated
    # "On <date>, <name> wrote:" or similar that is at the end
    # Remove leading "-----Original Message-----" and below
    idx = re.search(r"----- ?Original Message ?-----", text)
    if idx:
        text = text[: idx.start()]
    idx = re.search(r"\n-+ ?Forwarded by", text)
    if idx:
        text = text[: idx.start()]

    # Collapse whitespace, drop trailing blanks
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def printable_sender(addr):
    if not addr:
        return "unknown"
    return addr


# --- Build thread keys ---
# Group by a stable conversation fingerprint:
#   (primary_owner, from_addr(sorted), to_addrs_tuple, subject_norm)
# Then merge adjacent ones.

def thread_fingerprint(e):
    origin = e["origin"]
    owner = OWNER.get(origin)
    frm = e["from_addr"]
    tos = tuple(sorted(set(e["to_addrs"])))
    subj = e["subject_norm"]
    return (origin, owner, frm, tos, subj)


groups = defaultdict(list)
for e in emails:
    key = thread_fingerprint(e)
    groups[key].append(e)

# Only keep groups (potential threads) with >= 2 emails and some body substance
candidate_threads = []
for key, es in groups.items():
    es = [e for e in es if e["ts"]]
    if len(es) < 2:
        continue
    # require distinct from addresses (real exchange) or at least both sides present
    distinct_from = set(e["from_addr"] for e in es)
    # substance filter: at least one email body > 60 chars
    if not any(len(e["body"]) > 60 for e in es):
        continue
    es.sort(key=lambda e: e["ts"])
    span = es[-1]["ts"] - es[0]["ts"] if len(es) > 1 else 0
    origin = key[0]
    owner = key[1]
    # chose target peer: prefer the maildir owner if it appears as a participant; else the most frequent participant
    participants = defaultdict(int)
    for e in es:
        if e["from_addr"]:
            participants[e["from_addr"]] += 1
        for t in e["to_addrs"]:
            participants[t] += 1
    if not participants:
        continue
    if owner and owner in participants:
        target_peer = owner
    else:
        target_peer = max(participants, key=participants.get)
    candidate_threads.append({
        "owner": owner,
        "origin": origin,
        "from": key[2],
        "tos": list(key[3]),
        "subject": key[4],
        "subject_raw": es[0]["subject"],
        "emails": es,
        "count": len(es),
        "span_days": span / 86400,
        "n_from": len(distinct_from),
        "target_peer": target_peer,
    })

candidate_threads.sort(key=lambda t: (-t["count"], -t["span_days"]))
print(f"Total candidate groups (>=2 emails): {len(candidate_threads)}")

# Show the top candidates for review
for t in candidate_threads[:60]:
    subj = (t["subject_raw"] or "NONE")[:50]
    print(f"count={t['count']:3d} n_from={t['n_from']} span={t['span_days']:6.1f}d "
          f"origin={t['origin']:<12} target={t['target_peer']:<32} from={t['from'] or 'None':<38} subj={subj}")

# Save candidates for later curation
with open(RAW / "working/candidates.json", "w") as f:
    json.dump(candidate_threads, f, indent=1)
