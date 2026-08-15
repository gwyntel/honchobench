"""Automatic scoring of each role's parsed_output against labels.json ground truth.

No LLM judge required. Metrics:
  - deriver / deduction / induction: fact-set precision / recall / F1 via
    token-overlap based similarity (an extracted fact matches a label fact when
    they share significant n-gram/word overlap).
  - summary_short / summary_long: word-level precision / recall / F1 of the
    generated summary against the reference summary (ROUGE-style, unigram).
  - dialectic: per-QA answer presence + evidence-groundedness (fraction of
    reference-evidence phrases recovered in the answer).

Labels absent -> score() returns an all-None record (reports mark "no labels").
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

_STOP = set("the a an and or but of to in for on with at by from as is are was were be been being it its this that these those not no then than".split())


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", text.lower()) if t and t not in _STOP]


def prf(precision, recall):
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _overlap_score(a_toks: list[str], b_toks: list[str]) -> float:
    """Significant n-gram overlap ratio between two token lists (0..1)."""
    if not a_toks or not b_toks:
        return 0.0
    sa, sb = set(a_toks), set(b_toks)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def _fact_match(ext_toks, gold_toks, threshold=0.5) -> bool:
    """True when the extracted fact overlaps a gold fact meaningfully."""
    return _overlap_score(ext_toks, gold_toks) >= threshold


def score_fact_set(extracted: list[str], gold: list[str]) -> dict:
    """Precision/Recall/F1 of extracted facts vs gold facts.

    A gold fact is 'recalled' if at least one extracted fact matches it.
    An extracted fact counts toward precision if it matches any gold fact.
    """
    if gold:
        gold_t = [tokenize(g) for g in gold]
    else:
        gold_t = []
    ext_t = [tokenize(e) for e in extracted if e]

    if not gold_t:
        return {
            "precision": 1.0 if not ext_t else 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "n_extracted": len(extracted),
            "n_gold": len(gold),
        }
    if not ext_t:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0,
                "n_extracted": 0, "n_gold": len(gold)}

    matched_gold = set()
    precision_hits = 0
    for et in ext_t:
        hit = False
        for gi, gt in enumerate(gold_t):
            if _fact_match(et, gt):
                hit = True
                matched_gold.add(gi)
        if hit:
            precision_hits += 1
    precision = precision_hits / len(ext_t)
    recall = len(matched_gold) / len(gold_t)
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(prf(precision, recall), 4),
        "n_extracted": len(extracted),
        "n_gold": len(gold),
    }


def rouge_word_prf(generated: str, reference: str) -> dict:
    """Unigram word-level precision/recall/F1 of generated vs reference."""
    g = Counter(tokenize(generated))
    r = Counter(tokenize(reference))
    if not r:
        return {"precision": 1.0 if not g else 0.0, "recall": 0.0, "f1": 0.0,
                "n_ref_words": 0, "n_gen_words": len(g)}
    if not g:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0,
                "n_ref_words": len(r), "n_gen_words": 0}
    overlap = sum((g & r).values())
    precision = overlap / sum(g.values())
    recall = overlap / sum(r.values())
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(prf(precision, recall), 4),
        "n_ref_words": sum(r.values()),
        "n_gen_words": sum(g.values()),
    }


def score_dialectic(answer: str, qa: list[dict]) -> dict:
    """Answer presence + evidence-groundedness over the label's qa entries."""
    if not qa:
        return {"answer_presence": 0.0, "evidence_coverage": 0.0, "qa_f1": 0.0, "n_qa": 0}
    answer_toks = tokenize(answer or "")
    ev_hits = 0
    ev_total = 0
    qa_f1s = []
    for item in qa:
        # answer presence: does the answer contain a meaningful chunk of the gold answer?
        gold_a = item.get("a", "")
        a_toks = tokenize(gold_a)
        qa_f1s.append(rouge_word_prf(answer or "", gold_a)["f1"])
        if a_toks:
            cover = _overlap_score(answer_toks, a_toks)
            if cover >= 0.3:
                ev_hits += 1
        # evidence coverage
        for ev in (item.get("evidence") or []):
            ev_total += 1
            evt = tokenize(ev)
            if evt and _overlap_score(answer_toks, evt) >= 0.3:
                ev_hits += 1
    # ev_hits counts both answer presence an evidence matches
    n_presence = sum(1 for item in qa if tokenize(item.get("a", "")) and
                     _overlap_score(answer_toks, tokenize(item.get("a", ""))) >= 0.3)
    answer_presence = n_presence / len(qa)
    evidence_coverage = (ev_hits - n_presence) / ev_total if ev_total else 0.0
    return {
        "answer_presence": round(answer_presence, 4),
        "evidence_coverage": round(evidence_coverage, 4),
        "qa_f1": round(sum(qa_f1s) / len(qa_f1s), 4),
        "n_qa": len(qa),
    }


def score_result(result: dict, labels: Optional[dict]) -> dict:
    """Score a single role result against the conversation's labels.

    Returns a flat dict of metric -> value (plus ids). When labels are missing,
    all metric values are None and 'labeled' is False.
    """
    role = result["role"]
    conv_id = result["conversation_id"]
    model = result["model"]
    parsed = result.get("parsed_output") or {}

    base = {
        "role": role,
        "conversation_id": conv_id,
        "model": model,
        "labeled": bool(labels),
    }

    if not labels:
        base.update(_unlabeled_metrics(role))
        return base

    metrics: dict = {}
    if role == "deriver":
        extracted = parsed.get("explicit", [])
        gold = labels.get("explicit_facts", [])
        metrics.update(score_fact_set(extracted, gold))
    elif role in ("summary_short", "summary_long"):
        key = "short_summary" if role == "summary_short" else "long_summary"
        gen = parsed.get("summary", "")
        ref = labels.get(key, "")
        metrics.update(rouge_word_prf(gen, ref))
    elif role == "dialectic":
        metrics.update(score_dialectic(parsed.get("answer", ""), labels.get("qa", [])))
    elif role == "deduction":
        gold = labels.get("deductive", [])
        metrics.update(score_fact_set(parsed.get("observations", []), gold))
    elif role == "induction":
        gold = labels.get("inductive", [])
        obs = [o["content"] if isinstance(o, dict) else o for o in parsed.get("observations", [])]
        metrics.update(score_fact_set(obs, gold))
    elif role == "card_refresh":
        # peer card scored against explicit_facts coverage (identity markers)
        gold = labels.get("explicit_facts", [])
        metrics.update(score_fact_set(parsed.get("peer_card", []), gold))
    else:
        metrics = _unlabeled_metrics(role)
    base.update(metrics)
    return base


def _unlabeled_metrics(role: str) -> dict:
    keys = {
        "deriver": ["precision", "recall", "f1", "n_extracted", "n_gold"],
        "summary_short": ["precision", "recall", "f1", "n_ref_words", "n_gen_words"],
        "summary_long": ["precision", "recall", "f1", "n_ref_words", "n_gen_words"],
        "dialectic": ["answer_presence", "evidence_coverage", "qa_f1", "n_qa"],
        "deduction": ["precision", "recall", "f1", "n_extracted", "n_gold"],
        "induction": ["precision", "recall", "f1", "n_extracted", "n_gold"],
        "card_refresh": ["precision", "recall", "f1", "n_extracted", "n_gold"],
    }
    return {k: None for k in keys.get(role, [])}
