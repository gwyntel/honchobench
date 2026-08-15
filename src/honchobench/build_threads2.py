#!/usr/bin/env python3
"""Reconstruct 2-party email threads and assemble conversations.json."""
import json
import re
from collections import defaultdict, Counter
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

BANNED_SENDERS = {"postmaster@enron.com", "mailer-daemon@enron.com", "enron.announcements@enron.com",
                  "outlook-migration-team@enron.com", "arsystem@mailman.enron.com", "no.address@enron.com"}
# Newsletter/monitoring senders to exclude (not conversational business)
MONITOR_SUBJECT = re.compile(r"(breaking news|tropical weather|hurricane forecast|enron mentions|delivery status|concur expense|synchronization log)", re.I)

with open(RAW / "working/index.json") as f:
    emails = json.load(f)


def clean_body(body):
    if not body:
        return ""
    lines = body.split("\n")
    kept = []
    for ln in lines:
        s = ln.rstrip("\n").rstrip()
        if not s.strip():
            if kept and kept[-1] == "":
                continue
            kept.append("")
            continue
        if re.match(r"^>+\s{0,3}(from|sent|to|cc|subject|date):", s, re.I):
            continue
        if re.match(r"^-{4,}\s*(original message|forwarded message)", s, re.I):
            continue
        if re.match(r"^-+\s*{0,3}original message", s, re.I):
            continue
        kept.append(s)
    text = "\n".join(kept)
    idx = re.search(r"----- ?Original Message ?-----", text)
    if idx:
        text = text[: idx.start()]
    idx = re.search(r"\n-{3,} ?Forwarded by", text)
    if idx:
        text = text[: idx.start()]
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def primary_others(e, owner):
    """Return the (single) primary 'other party' email for a 2-party email, else None."""
    frm = e["from_addr"]
    tos = e["to_addrs"]
    if not frm:
        return None
    # Determine the owner side: if owner is a participant, other is the non-owner.
    others = set(tos)
    if frm == owner and owner in tos:
        others = set(tos) - {owner, frm}
        if len(others) == 1:
            return next(iter(others))
        return None
    if owner and owner in tos and len([t for t in tos if t != owner]) == 1:
        # sender is the 'other'
        return frm
    # no owner present: require exactly 2 participants total (from + single to)
    if len(tos) == 1 and tos[0] != frm:
        return tos[0]
    return None


# Build "edges": for each email that is a clean 2-party exchange involving
# the maildir owner, record (owner_origin, owner_email, other) -> list
clean_edges = defaultdict(list)
for e in emails:
    origin = e["origin"]
    owner = OWNER.get(origin)
    if not owner:
        continue
    frm = e["from_addr"]
    if frm in BANNED_SENDERS:
        continue
    if not e["body"] or len(e["body"]) < 40:
        continue
    if MONITOR_SUBJECT.search(e["subject_norm"] or ""):
        continue
    if e["ts"] is None:
        continue
    other = primary_others(e, owner)
    if not other:
        continue
    if other in BANNED_SENDERS:
        continue
    clean_edges[(origin, owner)].append((other, e))

# Now group by (origin, owner, other) into all emails for that 2-party pair.
pair_groups = defaultdict(list)
for (origin, owner), lst in clean_edges.items():
    by_other = defaultdict(list)
    for other, e in lst:
        by_other[other].append(e)
    for other, es in by_other.items():
        pair_groups[(origin, owner, other)].extend(es)

# For each pair, further split by subject cluster (handles multiple distinct threads between same pair)
def split_by_subject(es):
    # cluster by subject_norm with fuzzy merge (exact norm key)
    clusters = defaultdict(list)
    for e in es:
        clusters[e["subject_norm"] or ""].append(e)
    return clusters

threads = []
for (origin, owner, other), es in pair_groups.items():
    for subjkey, subj_es in split_by_subject(es).items():
        subj_es = [e for e in subj_es if e["ts"]]
        if len(subj_es) < 2:
            continue
        # require both directions OR at least 3 emails for a real thread;
        # prioritize threads where both parties wrote
        froms = set(e["from_addr"] for e in subj_es)
        substantive = sum(1 for e in subj_es if len(e["body"]) > 80)
        subj_es.sort(key=lambda e: e["ts"])
        threads.append({
            "origin": origin,
            "owner": owner,
            "other": other,
            "subject_key": subjkey,
            "emails": subj_es,
            "count": len(subj_es),
            "n_from": len(froms),
            "substantive": substantive,
            "span_days": (subj_es[-1]["ts"] - subj_es[0]["ts"]) / 86400,
        })

threads.sort(key=lambda t: (t["n_from"] > 1, t["substantive"], -t["count"], -t["span_days"]), reverse=True)

# Rank: prefer both-way, substantive, multi-email threads
scored = []
for t in threads:
    score = 0
    if t["n_from"] > 1:
        score += 50
    score += min(t["count"], 30) * 2
    score += min(t["substantive"], 20) * 1.5
    score += min(t["span_days"] / 30, 24)
    scored.append((score, t))
scored.sort(key=lambda x: -x[0])

print(f"Total 2-party subject clusters with >=2 emails: {len(scored)}")
with open(RAW / "working/pairs.json", "w") as f:
    json.dump([{"score": s, **{k: (v if k != "emails" else [ {kk: (e["path"] if kk=="path" else e[kk]) for kk in ["from_addr","subject","subject_norm","ts","body","message_id"]} for e in t["emails"]]) for k, v in t.items()}} for s, t in scored], f, indent=0)

# Print top 80
for score, t in scored[:80]:
    both = "BOTH" if t["n_from"] > 1 else "one-way"
    subj = (t["emails"][0]["subject"] or "NONE")[:40]
    print(f"score={score:6.1f} n={t['count']:2d} {both:6s} subst={t['substantive']:2d} span={t['span_days']:6.1f}d " 
          f"{t['origin']:<12}{t['owner'][:22]:<23}>{t['other'][:38]:<39} {subj}")
