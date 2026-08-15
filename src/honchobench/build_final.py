#!/usr/bin/env python3
"""Production builder: assemble conversations.json + manifest.json from curated threads.

Inputs:
  - RAW/working/threads_dedup.json  (all deduped candidates from earlier pass)

Applies robust content-based dedup (collapse cross-folder / quoted copies) and
strips quoting boilerplate from each message so only the sender's own content
remains. Writes the final dataset files.
"""
import json, re, hashlib
from collections import OrderedDict
from pathlib import Path
from datetime import datetime

RAW = Path("/home/hermes/projects/honchobench/data/raw")
OUT = Path("/home/hermes/projects/honchobench/data/dataset")

with open(RAW / "working/threads_dedup.json") as f:
    THREADS = json.load(f)


def strip_quote(text):
    """Remove quoted/forwarded boilerplate so only the sender's own content remains."""
    if not text:
        return ""
    lines = text.split("\n")
    kept = []
    for ln in lines:
        s = ln.rstrip("\n").rstrip()
        if not s.strip():
            if kept and kept[-1] == "":
                continue
            kept.append("")
            continue
        low = s.lower()
        if re.match(r"^>{0,3}[ \t]*(from|sent|to|cc|bcc|subject|date|importance|x-)[ \t]*:", s, re.I) and len(s) < 90:
            continue
        if re.match(r"^(-{3,}|_{3,})[ \t]*(original message|forwarded message|forwarded by|reply)", s, re.I):
            continue
        if re.match(r"^----- ?(original|forwarded|reply)[ \t]+message ?-----", s, re.I):
            continue
        # drop standalone "name wrote:" attribution at start of a quoted chunk
        kept.append(s)
    text = "\n".join(kept)
    # cut everything from an embedded original/forwarded message marker
    text = re.split(r"----- ?(?:original|forwarded|reply) ?message ?-----", text, flags=re.I)[0]
    text = re.split(r"\n\n+(?=From:[^\n]{5,}\n(?:To:|Cc:|Bcc:|Sent:|Subject:|Date:))", text)[0]
    # cut off trailing "On <date>, <someone> wrote:" quoted blocks
    text = re.split(r"\nOn[^\n]{5,60}, [^\n]{2,30} wrote:\n", text)[0]
    # drop lines that begin with ">" (quoted)
    text = "\n".join(l for l in text.split("\n") if not l.startswith(">") and not l.startswith("\t>"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def norm_sig(text):
    t = strip_quote(text)
    t = re.sub(r"\s+", " ", t).lower()
    # strip common assistant signature blocks for better dedup
    t = re.sub(r"(regards|thanks|thank you|best|thx|sincerely)[,\n]?$", "", t)
    t = re.sub(r"assistant to [a-z ]+|713[\.\-\s]\d{3}[\.\-\s]\d{4}|fax|phone", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def final_dedupe(emails):
    """Emails: list of dicts with from_addr, ts, body. Return unique by (sender, normalized content body)."""
    seen = {}
    out = []
    for e in emails:
        body = strip_quote(e["body"])
        if len(body) < 15:
            continue
        sig = norm_sig(body)[:180]
        key = (e["from_addr"], sig)
        if key in seen:
            continue
        seen[key] = True
        out.append(e)
    out.sort(key=lambda e: e["ts"])
    return out


def build(origin, other, subj_patterns, conv_id, target=None, required_note=""):
    """Find the genuine thread(s) matching origin+other and a subject regex;
    merge them into one conversation (same relationship/topic)."""
    matches = []
    seen = set()
    owner = None
    for t in THREADS:
        if t["origin"] != origin:
            continue
        # other match
        other_ok = (other in t["other"]) or (t["other"] == other)
        if not other_ok:
            continue
        subj = (t["subject_raw"] or "NONE")
        subjkey = t["subject_key"] or ""
        blob = (subj + " " + subjkey).lower()
        if subj_patterns and not any(re.search(p, blob) for p in subj_patterns):
            continue
        owner = t["owner"]
        # collect its emails (already deduped at thread level)
        for e in t["emails"]:
            if e.get("ts", 0) < 820454400:  # before 1996 — corrupted date
                continue
            key = (e["from_addr"], round(e["ts"] // 120), e["subject_norm"])
            if key in seen:
                continue
            seen.add(key)
            matches.append(e)
    matches = final_dedupe(matches)
    if len(matches) < 2:
        return None
    tp = target or owner
    msgs = [{"sender": e["from_addr"], "text": strip_quote(e["body"])} for e in matches]
    # remove any empty text
    msgs = [m for m in msgs if len(m["text"]) > 10]
    return {
        "conversation_id": conv_id,
        "source": "enron",
        "messages": msgs,
        "target_peer": tp,
        "_origin": origin,
        "_start": datetime.fromtimestamp(matches[0]["ts"]).strftime("%Y-%m-%d"),
        "_end": datetime.fromtimestamp(matches[-1]["ts"]).strftime("%Y-%m-%d"),
    }
