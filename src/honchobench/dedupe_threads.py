#!/usr/bin/env python3
"""Dedupe emails across subfolders, then assemble curated candidate threads."""
import json, re, hashlib
from collections import defaultdict
from pathlib import Path
RAW = Path("/home/hermes/projects/honchobench/data/raw")
threads_full = json.load(open(RAW / "working/threads_full.json"))

def clean_body(body):
    if not body: return ""
    kept = []
    for ln in body.split("\n"):
        s = ln.rstrip("\n").rstrip()
        if not s.strip():
            if kept and kept[-1]=="": continue
            kept.append(""); continue
        if re.match(r"^>{1,4}\s{0,3}(from|sent|to|cc|subject|date):", s, re.I): continue
        if re.match(r"^-{4,}\s*(original message|forwarded by)", s, re.I): continue
        if re.match(r"^-----Original Message-----", s, re.I): continue
        if re.match(r"^From:.*\n?$", s) and "enron.com" in s and len(s) < 60: continue
        kept.append(s)
    text = "\n".join(kept)
    i = re.search(r"----- ?Original Message ?-----", text)
    if i: text = text[:i.start()]
    text = re.sub(r"\n{3,}","\n\n",text).strip()
    # collapse leading quoted "On X wrote," stack retained
    return text

# Normalize body for hashing: strip quotes and whitespace to detect near-dups
def body_hash(body):
    t = clean_body(body)
    # remove quoted lines and signatures-ish lines for robust dup detection
    t = re.sub(r"\n", " ", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    # keep first 400 chars
    return hashlib.md5(t[:400].encode()).hexdigest()[:12]

def round_ts(ts):
    # round to 60s to tolerate timezone/folder drifts
    import math
    return int(ts // 60)

# Dedupe across the whole thread set
results = []
for t in threads_full:
    seen = set()
    deduped = []
    for e in t["emails"]:
        if not e.get("ts"): continue
        key = (e.get("from_addr"), round_ts(e["ts"]), body_hash(e.get("body","")))
        if key in seen: continue
        seen.add(key)
        ecopy = dict(e)
        ecopy["clean_body"] = clean_body(e.get("body",""))
        deduped.append(ecopy)
    if len(deduped) < 2: continue
    t = dict(t)
    t["emails"] = deduped
    t["count"] = len(deduped)
    t["n_from"] = len(set(e["from_addr"] for e in deduped))
    t["substantive"] = sum(1 for e in deduped if len(e["clean_body"])>60)
    t["span_days"] = (deduped[-1]["ts"] - deduped[0]["ts"])/86400
    results.append(t)

print(f"After dedup, threads with >=2 unique emails: {len(results)}")
json.dump(results, open(RAW / "working/threads_dedup.json","w"))

# Show the good business threads again
def score(t):
    s=0
    if t["n_from"]>1: s+=40
    s += min(t["count"],30)*1.3
    s += min(t["substantive"],30)
    s += min(t["span_days"]/15,25)
    return s
results.sort(key=lambda t: -score(t))
for t in results:
    if t["n_from"]>1 and t["count"]>=5 and t["substantive"]>=5:
        print(f"sc={score(t):5.1f} n={t['count']:3d} subst={t['substantive']:3d} sp={t['span_days']:5.0f}d {t['origin']:<11}{t['owner'][:20]:<21}>{t['other'][:33]:<34} {(t['subject_raw'] or 'NONE')[:35]}")
