#!/usr/bin/env python3
"""Dump clean message content for specific threads for manual curation."""
import json, re
from pathlib import Path
RAW = Path("/home/hermes/projects/honchobench/data/raw")
threads = json.load(open(RAW / "working/threads_full.json"))

def clean_body(body):
    if not body: return ""
    kept = []
    for ln in body.split("\n"):
        s = ln.rstrip("\n").rstrip()
        if not s.strip():
            if kept and kept[-1]=="": continue
            kept.append(""); continue
        if re.match(r"^>{1,3}\s{0,3}(from|sent|to|cc|subject|date):", s, re.I): continue
        if re.match(r"^-{4,}\s*(original message|forwarded by)", s, re.I): continue
        kept.append(s)
    text = "\n".join(kept)
    i = re.search(r"----- ?Original Message ?-----", text)
    if i: text = text[:i.start()]
    text = re.sub(r"\n{3,}","\n\n",text).strip()
    return text

# filters: origin, other, subject pattern
FILTERS = [
    ("SKILLING-J", "stanmarek@marekbros.com", None),
    ("SKILLING-J", "eharris@insightpartners.com", None),
    ("SKILLING-J", "joannie.williamson@enron.com", "Follow-Up - Brent Larsen"),
    ("KEAN-S", "richard.shapiro@enron.com", "Lay/Skilling Talking Points"),
    ("KEAN-S", "gavin.dillingham@enron.com", None),
    ("KITCHEN-L", "john.lavorato@enron.com", None),
    ("KEAN-S", "jeff.dasovich@enron.com", None),
    ("SHACKLETON-S", "carol.clair@enron.com", "Cash Collateral"),
    ("SHACKLETON-S", "susan.bailey@enron.com", None),
]

def dump_thread(t, limit=12):
    print("="*100)
    print(f"ORIGIN={t['origin']}  OTHER={t['other']}")
    print(f"SUBJECT={t['subject_raw']!r}  count={t['count']} n_from={t['n_from']}")
    print(t["emails"][0]["path"])
    print("-"*100)
    shown = 0
    for e in t["emails"]:
        if shown >= limit: 
            print(f"  ... ({t['count']-shown} more)")
            break
        body = clean_body(e["body"])
        ts = e["ts"]
        from datetime import datetime
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else e["date_str"]
        print(f"\n--- [{dt}] FROM {e['from_addr']}")
        print(body[:900])
        shown += 1

for origin, other, subj in FILTERS:
    for t in threads:
        if t["origin"]==origin and (t["other"]==other or (other=="joannie.williamson@enron.com" and other in t["other"])):
            if subj is None or subj.lower() in (t["subject_raw"] or "").lower() or subj.lower() in t["subject_key"]:
                dump_thread(t)
                break
