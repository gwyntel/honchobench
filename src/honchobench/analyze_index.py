#!/usr/bin/env python3
"""Analyze the index to find good 2-party threads for building conversations."""
import json
import re
from collections import defaultdict, Counter
from pathlib import Path

RAW = Path("/home/hermes/projects/honchobench/data/raw")
with open(RAW / "working/index.json") as f:
    emails = json.load(f)

# Owner email per maildir origin
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

# How many emails have from_addr?
has_from = [e for e in emails if e["from_addr"]]
print(f"emails with from_addr: {len(has_from)} / {len(emails)}")
has_ts = [e for e in emails if e["ts"]]
print(f"emails with parseable date: {len(has_ts)}")
has_to = [e for e in emails if e["to_addrs"]]
print(f"emails with to_addrs: {len(has_to)}")

# body length stats
lens = [len(e["body"]) for e in emails]
import statistics
print(f"body len: mean={statistics.mean(lens):.0f} median={statistics.median(lens):.0f}")
print(f"body len >100 chars: {sum(1 for l in lens if l>100)}")

# For each maildir, top senders
print("\n=== Top senders by maildir ===")
by_md = defaultdict(list)
for e in emails:
    by_md[e["origin"]].append(e)
for origin, ems in sorted(by_md.items()):
    cnt = Counter(e["from_addr"] for e in ems if e["from_addr"])
    top = cnt.most_common(8)
    print(f"\n{origin} ({len(ems)} emails), owner={OWNER.get(origin)}")
    for addr, c in top:
        print(f"    {addr}: {c}")
