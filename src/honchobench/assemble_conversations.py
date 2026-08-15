#!/usr/bin/env python3
"""Assemble the final HonchoBench conversations from the full Enron index.

Each conversation is a topically-coherent, chronologically-ordered thread carved
from the corpus around a dense business relationship. Multi-party participants
are included (per CONVENTIONS). Emails are deduped and cleaned.
"""
import json, re, email.utils
from collections import defaultdict
from pathlib import Path
from datetime import datetime

RAW = Path("/home/hermes/projects/honchobench/data/raw")
OUT = Path("/home/hermes/projects/honchobench/data/dataset")

OWNER_EMAIL = {
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
OWNER_NAME = {
    "DASOVICH-J": "Jeff Dasovich",
    "KEAN-S": "Steven Kean",
    "SHACKLETON-S": "Sara Shackleton",
    "KITCHEN-L": "Louise Kitchen",
    "TAYLOR-M": "Mark Taylor",
    "WHALLEY-G": "Greg Whalley",
    "JONES-T": "Tana Jones",
    "SKILLING-J": "Jeff Skilling",
    "LAY-K": "Ken Lay",
    "LOKAY-M": "Mark Lokay",
}

with open(RAW / "working/index.json") as f:
    EMAILS = json.load(f)


def clean_body(body, subject=None):
    if not body:
        return ""
    kept = []
    for ln in body.split("\n"):
        s = ln.rstrip("\n").rstrip()
        if not s.strip():
            if kept and kept[-1] == "":
                continue
            kept.append("")
            continue
        low = s.lower()
        if re.match(r"^>+[ \t]?(from|sent|to|cc|subject|date):", s, re.I):
            continue
        if re.match(r"^-{4,}[ \t]*(original message|forwarded message|reply message)", s, re.I):
            continue
        if re.match(r"^----- ?(?i:original|forwarded|reply)[ \t]+message ?-----", s):
            continue
        # skip bare leading attribution lines already covered by body parse
        if re.match(r"^(sent:?|from:?|to:?|cc:?|subject:?|date:?)[ \t]+", s, re.I) and len(s) < 70:
            continue
        kept.append(s)
    text = "\n".join(kept)
    # cut at embedded "-----Original Message-----" (forwarded quote blocks)
    text = re.split(r"----- ?(?:original|forwarded|reply) ?message ?-----", text, flags=re.I)[0]
    # cut trailing nested quoting that repeats a full "From:" header block
    text = re.split(r"\n\n(?=(?:From|Sent by):[^\n]+\n(?:To:|Cc:|Bcc:|Subject:|Date:))", text)[0]
    # collapse runs of blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # drop a trailing single "________________________________" or "----" separator
    return text


def body_hash_sig(body):
    t = clean_body(body)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t[:250]


def participant_emails(origin, center):
    """Emails where center is a participant (from or to); include full CC."""
    owner = OWNER_EMAIL[origin]
    for e in EMAILS:
        if e["origin"] != origin:
            continue
        if e["ts"] is None:
            continue
        frm = e["from_addr"]
        tos = e["to_addrs"]
        if not frm:
            continue
        people = set([frm] + tos)
        if center not in people:
            continue
        if len(e["body"]) < 40:
            continue
        yield e


def build_conversation(origin, center, people_focus, kw_patterns, tmin, tmax,
                       conv_id, subject_hint="", dedup_sig_len=200):
    """Assemble a topical thread. people_focus: required anchor participants;
    kw_patterns: list of regex; only emails whose subject OR body mention a kw,
    within [tmin,tmax], involving center and any participant."""
    owner = OWNER_EMAIL[origin]
    rows = []
    seen = set()
    for e in EMAILS:
        if e["origin"] != origin or e["ts"] is None:
            continue
        frm = e["from_addr"]
        tos = e["to_addrs"]
        if not frm:
            continue
        t = e["ts"]
        if t < tmin or t > tmax:
            continue
        people = set([frm] + tos)
        # must include the center
        if center not in people:
            continue
        # must touch at least one focus person (or focus empty => all)
        if people_focus and not (people & people_focus):
            continue
        subj = (e["subject_norm"] or "") + " " + (e["subject"] or "").lower()
        bodytext = re.sub(r"\s+", " ", (e["body"] or "")).lower()
        if kw_patterns:
            if not any(re.search(p, subj) or re.search(p, bodytext) for p in kw_patterns):
                continue
        # dedupe
        sig = body_hash_sig(e["body"])[:dedup_sig_len]
        key = (frm, round(t // 120), sig)
        if key in seen:
            continue
        seen.add(key)
        rows.append((t, e))
    rows.sort(key=lambda x: x[0])
    if not rows:
        return None
    msgs = []
    for t, e in rows:
        body = clean_body(e["body"])
        if not body:
            continue
        sender = e["from_addr"]
        msgs.append({"sender": sender, "text": body})
        if len(msgs) == 1:
            first_dt = datetime.fromtimestamp(t)
    return {
        "conversation_id": conv_id,
        "source": "enron",
        "messages": msgs,
        "target_peer": center,
        "_origin": origin,
        "_n": len(msgs),
        "_span_days": (rows[-1][0] - rows[0][0]) / 86400 if len(rows) > 1 else 0,
    }
