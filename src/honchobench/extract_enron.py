#!/usr/bin/env python3
"""Extract an info-dense slice of the Enron corpus and reconstruct threads.

Produces data/dataset/conversations.json and data/dataset/manifest.json.
"""
import os
import re
import json
import email
import email.utils
import hashlib
from datetime import datetime
from collections import defaultdict
from pathlib import Path

ROOT = Path("/home/hermes/projects/honchobench")
MAILDIR = ROOT / "data/raw/scratch/maildir"
RAW = ROOT / "data/raw"
OUT = ROOT / "data/dataset"

# Selected maildirs (lastname-firstinitial) dense with real business relationships
SELECTED = [
    "lay-k",        # Kenneth Lay, Enron CEO/Chairman
    "shackleton-s", # Sara Shackleton, Ken Lay's assistant / executive assistant
    "skilling-j",   # Jeff Skilling, President/COO then CEO
    "kitchen-l",    # Louise Kitchen, head of trading / energy
    "whalley-g",    # Greg Whalley, COO / President of Enron
    "kean-s",       # Steven Kean, VP/Chief of Staff
    "dasovich-j",   # Jeff Dasovich, regulatory affairs (CA energy crisis)
    "lokay-m",      # Mark Lokay
    "taylor-m",     # Mark Taylor, VP of Petroleum / very well connected
    "jones-t",      # Tana Jones, executive assistant
]

# The well-known individual -> email address mapping (stable peer ids)
NAME_EMAIL = {
    "lay-k": "kenneth.lay@enron.com",
    "shackleton-s": "sara.shackleton@enron.com",
    "skilling-j": "jeff.skilling@enron.com",
    "kitchen-l": "louise.kitchen@enron.com",
    "whalley-g": "greg.whalley@enron.com",
    "whalley-l": "greg.whalley@enron.com",
    "kean-s": "steven.kean@enron.com",
    "dasovich-j": "jeff.dasovich@enron.com",
    "lokay-m": "mark.lokay@enron.com",
    "taylor-m": "mark.taylor@enron.com",
    "jones-t": "tana.jones@enron.com",
}

SUBJECT_STRIP = re.compile(r"^(?:re|fw|fwd|sv|aw|回复|答复|antw|wg|ref|转?发?[:：])[\s:\[\]]*", re.I)


def norm_subject(subj):
    s = (subj or "").strip()
    # repeatedly strip fwd/re prefixes
    for _ in range(5):
        ns = SUBJECT_STRIP.sub("", s)
        ns = re.sub(r"^[\[\]()\s]+", "", ns).strip()
        if ns == s:
            break
        s = ns
    return re.sub(r"\s+", " ", s).lower().strip("- ").strip()


def parse_date(dt_str):
    try:
        ts = email.utils.parsedate_to_datetime(dt_str)
        return ts.timestamp()
    except Exception:
        return None


def parse_email(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
        msg = email.message_from_bytes(raw)
    except Exception:
        return None

    hdr = lambda k: msg.get(k) or ""
    from_str = hdr("From") or hdr("X-From")
    to_str = hdr("To")
    subj = hdr("Subject")
    date_str = hdr("Date")
    mid = hdr("Message-ID")
    origin = hdr("X-Origin").upper()

    # Extract from email address
    from_addr = None
    from_display = None
    if from_str:
        try:
            name, addr = email.utils.parseaddr(from_str)
            if addr:
                from_addr = addr.lower()
                if not name and "." in addr:
                    name = addr.split("@")[0]
                from_display = name or from_addr
        except Exception:
            from_addr = None

    to_addrs = []
    if to_str:
        for name, addr in email.utils.getaddresses([to_str]):
            if addr:
                to_addrs.append(addr.lower())

    ts = parse_date(date_str)
    body_text = ""
    # Extract plain text body
    body_txt = ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "text/plain" and not part.get_filename():
            try:
                body_txt = part.get_payload(decode=True).decode("utf-8", "replace")
                break
            except Exception:
                continue
    if not body_txt:
        body_txt = msg.get_payload(decode=False)
        if isinstance(body_txt, bytes):
            body_txt = body_txt.decode("utf-8", "replace")
    body_text = body_txt or ""

    return {
        "path": str(path),
        "origin": origin.rstrip("\r\n").strip(),
        "from_addr": from_addr,
        "from_display": from_display,
        "to_addrs": to_addrs,
        "subject": subj,
        "subject_norm": norm_subject(subj),
        "date_str": date_str,
        "ts": ts,
        "message_id": mid.strip() if mid else None,
        "body": body_text,
    }


def clean_body(body, subject):
    """Strip quoting/forwarding boilerplate, keep real content."""
    if not body:
        return ""
    # Remove leading blank lines, collapse to single text
    # Remove lines that are pure quoted replies prefixed by > (but keep some context)
    lines = body.split("\n")
    out = []
    for ln in lines:
        s = ln.rstrip("\n").rstrip()
        stripped = s.strip()
        # Skip pure quote markers / email separators
        if re.match(r"^[>\s]*$", s) and not re.search(r"[a-zA-Z0-9]", s):
            continue
        out.append(s)
    body = "\n".join(out)

    # Cut off at common quoting delimiters: "-----Original Message-----"
    cut = re.split(r"----- ?Original Message ?-----|^--+[ \n]*Original Message|^From:.*\n.*\n.*\n(?:To|Cc):", body, maxsplit=1, flags=re.M)
    body = cut[0].strip()

    # Cut at repeated attribution blocks
    # Remove trailing forwarding chains header

    # Remove signature-ish repeated address blocks

    # Collapse 3+ blank lines to 1
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def main():
    all_emails = []
    skipped_bad = 0
    per_maildir = defaultdict(int)
    for md in SELECTED:
        mdir = MAILDIR / md
        if not mdir.exists():
            continue
        for dirpath, _dirnames, filenames in os.walk(mdir):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rec = parse_email(full)
                if not rec:
                    skipped_bad += 1
                    continue
                per_maildir[md] += 1
                rec["maildir"] = md
                rec["origin_email"] = NAME_EMAIL.get(rec.get("origin", "").lower(), None)
                all_emails.append(rec)
    print(f"Parsed {len(all_emails)} emails, skipped {skipped_bad}")
    for md, c in per_maildir.items():
        print(f"  {md}: {c}")

    # Save raw index for reuse
    with open(RAW / "working/index.json", "w") as f:
        json.dump(all_emails, f)
    print("saved index.json")


if __name__ == "__main__":
    main()
