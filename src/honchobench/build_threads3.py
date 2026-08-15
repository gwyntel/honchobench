#!/usr/bin/env python3
"""Reconstruct threads - fix self-pairs, produce curated candidate list."""
import json
import re
from collections import defaultdict
from pathlib import Path

RAW = Path("/home/hermes/projects/honchobench/data/raw")

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

BANNED = {"postmaster@enron.com","mailer-daemon@enron.com","enron.announcements@enron.com",
          "outlook-migration-team@enron.com","arsystem@mailman.enron.com","no.address@enron.com",
          "owner-eveningmba@haas.berkeley.edu","exchangeinfo@nymex.com","forexnews.com"}
MONITOR = re.compile(r"(breaking news|tropical weather|hurricane forecast|enron mentions|delivery status|concur expense|synchronization log|travelocity|enouch newsletter|storm-advisory|weather)", re.I)

with open(RAW / "working/index.json") as f:
    emails = json.load(f)


def primary_other(e, owner):
    frm = e["from_addr"]
    tos = e["to_addrs"]
    if not frm:
        return None
    # Build participant set
    if owner:
        if frm == owner:
            # owner is sender
            others = [t for t in tos if t != owner]
            if len(others) == 1:
                return others[0]
            return None
        elif owner in tos:
            rest = [t for t in tos if t != owner]
            if len(rest) == 0:
                return frm
            if len(rest) == 1:
                return rest[0]
            return None
        else:
            # owner not a participant in this copy; only clean 2-party
            if len(tos) == 1 and tos[0] != frm:
                return tos[0]
            return None
    else:
        if len(tos) == 1 and tos[0] != frm:
            return tos[0]
        return None


groups = defaultdict(list)
for e in emails:
    origin = e["origin"]
    owner = OWNER.get(origin)
    if not owner:
        continue
    frm = e["from_addr"]
    if frm in BANNED or any(b in (frm or "") for b in ["forexnews.com", "@nymex.com", "@haas.berkeley.edu"]):
        continue
    if not e["body"] or len(e["body"]) < 40:
        continue
    if MONITOR.search(e["subject_norm"] or ""):
        continue
    if e["ts"] is None:
        continue
    other = primary_other(e, owner)
    if not other or other == owner:
        continue
    if frm == other:  # self-loop weird
        continue
    groups[(origin, owner, other)].append(e)

threads = []
for (origin, owner, other), es in groups.items():
    # cluster by subject
    by_subj = defaultdict(list)
    for e in es:
        by_subj[e["subject_norm"] or ""].append(e)
    for skey, subj_es in by_subj.items():
        subj_es = [e for e in subj_es if e["ts"]]
        if len(subj_es) < 2:
            continue
        froms = set(e["from_addr"] for e in subj_es)
        subst = sum(1 for e in subj_es if len(e["body"]) > 80)
        if subst < 1:
            continue
        subj_es.sort(key=lambda e: e["ts"])
        threads.append({
            "origin": origin, "owner": owner, "other": other,
            "subject_key": skey, "subject_raw": subj_es[0]["subject"],
            "emails": subj_es, "count": len(subj_es),
            "n_from": len(froms), "substantive": subst,
            "span_days": (subj_es[-1]["ts"] - subj_es[0]["ts"]) / 86400,
        })

def score(t):
    s = 0
    if t["n_from"] > 1: s += 40
    s += min(t["count"], 25) * 1.2
    s += min(t["substantive"], 25)
    s += min(t["span_days"]/15, 30)
    # penalize self/trivial subjects
    if not t["subject_key"] or t["subject_key"].strip() in ("re:","", "none"):
        s -= 5
    return s

for t in threads:
    t["score"] = score(t)
threads.sort(key=lambda t: -t["score"])

# persist candidate details (without heavy bodies) for review
with open(RAW / "working/threads_meta.json", "w") as f:
    json.dump([{**{k: v for k, v in t.items() if k != "emails"},
                "sample_sender": t["emails"][0]["from_display"] or t["emails"][0]["from_addr"],
                "sample_body": t["emails"][0]["body"][:200]} for t in threads], f, indent=1)

print(f"Total candidate threads: {len(threads)}")
for t in threads[:100]:
    s = t["subject_raw"] or "NONE"
    print(f"sc={t['score']:5.1f} n={t['count']:3d}{'B' if t['n_from']>1 else 'o':1s} subst={t['substantive']:3d} sp={t['span_days']:6.0f}d "
          f"{t['origin']:<11}{t['owner'][:22]:<23}>{t['other'][:36]:<37} {s[:38]}")

# save full threads to a pickle-ish json for final assembly (emails stripped)
with open(RAW / "working/threads_full.json", "w") as f:
    json.dump(threads, f)
