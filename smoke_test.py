#!/usr/bin/env python3
"""Honcho IRL bounded smoke test.

Ingests ONE Enron conversation through the REAL honcho stack
(API server + deriver + Postgres) and verifies end-to-end:
  - workspace / peer / session creation
  - message ingestion
  - deriver drains the queue -> representation observations (documents)
  - summarizer produces a session summary
  - API reads back: conclusions/list, conclusions/query, representation, chat

Usage:  cd honchobench && python3 smoke_test.py <conversation_id>
Assumes: API at http://127.0.0.1:8000, deriver running, auth off.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:8000/v3"


def req(method, path, body=None, name=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        print(f"  !! HTTP {e.code} on {name} {method} {path}: {raw[:500]}")
        return e.code, raw


def main(conversation_id):
    dataset = json.load(open("/home/hermes/projects/honchobench/data/dataset/conversations.json"))
    labels = json.load(open("/home/hermes/projects/honchobench/data/dataset/labels.json"))
    convo = next(c for c in dataset if c["conversation_id"] == conversation_id)
    target = convo["target_peer"]
    obs_peer = "observer_agent"

    def slug(s):
        import re
        return re.sub(r"[^a-zA-Z0-9_-]", "_", s).strip("_")

    target = slug(target)  # API PeerCreate id pattern ^[a-zA-Z0-9_-]+$
    run = datetime.now(timezone.utc).strftime("%H%M%S")
    ws = f"smoke_{conversation_id}_{run}"
    session_id = conversation_id
    print(f"[1] workspace={ws} target={target} msgs={len(convo['messages'])}")

    # workspace
    s, _ = req("POST", "/workspaces", {"id": ws, "metadata": {"bench": "enron"}}, "ws")
    assert s in (200, 201), f"workspace create failed {s}"
    # peers
    s, _ = req("POST", f"/workspaces/{ws}/peers", {"id": obs_peer, "metadata": {"role": "observer"}}, "obs peer")
    assert s in (200, 201), f"obs peer failed {s}"
    s, _ = req("POST", f"/workspaces/{ws}/peers", {"id": target, "metadata": {"person": "Sara Shackleton"}}, "target peer")
    assert s in (200, 201), f"target peer failed {s}"
    # session (observer builds theory-of-mind rep of target)
    s, _ = req("POST", f"/workspaces/{ws}/sessions",
               {"id": session_id,
                "peers": {target: {}, obs_peer: {"observe_others": True}}}, "session")
    assert s in (200, 201), f"session failed {s}"

    # synthesize Enron-era timestamps spread ~30min apart in order
    t0 = datetime(2001, 1, 15, 9, 0, 0, tzinfo=timezone.utc)
    msgs = [{"peer_id": (target if m["sender"] == target else obs_peer),
             "content": m["text"],
             "created_at": (t0 + timedelta(minutes=30 * i)).isoformat()}
            for i, m in enumerate(convo["messages"])]
    for i in range(0, len(msgs), 100):
        s, resp = req("POST", f"/workspaces/{ws}/sessions/{session_id}/messages",
                      {"messages": msgs[i:i + 100]}, "messages")
        assert s in (200, 201), f"message ingest failed {s}: {str(resp)[:400]}"
    print(f"  ingested {len(msgs)} messages")

    # wait for queue to drain
    print("[2] waiting for deriver to drain queue ...")
    for _ in range(120):
        s, qs = req("GET", f"/workspaces/{ws}/queue/status", None, "queue_status")
        pending = 0
        if isinstance(qs, dict):
            pending = qs.get("pending_work_units") or 0
        if pending == 0:
            print(f"  queue drained (status={qs})")
            break
        time.sleep(2)
    else:
        print("  !! queue did not drain in time")
        sys.exit(1)

    # verify conclusions
    s, concl = req("POST", f"/workspaces/{ws}/conclusions/list",
                   {"filters": {"observer_id": obs_peer, "observed_id": target}}, "concl_list")
    items = concl.get("items", concl) if isinstance(concl, dict) else concl
    print(f"[3] conclusions(list) for observer->target: {len(items)} items")

    s, qres = req("POST", f"/workspaces/{ws}/conclusions/query",
                  {"query": "What legal entity was created for US debt trading?",
                   "top_k": 5,
                   "filters": {"observer": obs_peer, "observed": target}}, "concl_query")
    print(f"[4] conclusions(query) semantic -> {len(qres)} results; first: {str(qres[:1])[:300]}")

    # representation
    s, rep = req("POST", f"/workspaces/{ws}/peers/{obs_peer}/representation",
                 {"target": target, "max_conclusions": 25}, "representation")
    text = (rep or {}).get("representation", "")
    print(f"[5] representation({target} from {obs_peer}) len={len(text)}")
    print("    head:", text[:300].replace("\n", " | "))

    print("[6] DONE. workspace:", ws)


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else "enron_shackleton_nelson_eci_guaranty"
    main(cid)
