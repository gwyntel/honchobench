# HonchoBench — project conventions

## Layout
- `_vendor/honcho` — vendored clone of plastic-labs/honcho at HEAD 4448979 (read-only reference).
- `data/raw/` — raw downloaded Enron corpus (enron_mail_20150507.tar.gz).
- `data/dataset/` — the curated benchmark dataset (conversations.json + manifest.json).
- `src/honchobench/` — the runner package.
- `src/roles/` — per-role benchmark implementations.
- `reports/` — generated .md + .csv outputs.
- `tests/` — pytest.

## Dataset format — `data/dataset/conversations.json`
A JSON array. Each element is ONE conversation (a reconstructed Enron thread or a doc conversation).

```json
{
  "conversation_id": "enron_<thread_or_user>_<n>",
  "source": "enron",
  "messages": [
    {"sender": "kenneth.lay@enron.com", "text": "..."},
    {"sender": "sara.shackleton@enron.com", "text": "..."}
  ],
  "target_peer": "sara.shackleton@enron.com"
}
```

- `target_peer`: the peer whose representation each role must build (the deriver subject / dialectic observee). One per conversation. The messages may be multi-party; only `target_peer` is the analysis subject.
- `sender` is the display name / email. Keep email addresses as the stable peer id.

## Labels — `data/dataset/labels.json`
Ground-truth for scoring, keyed by `conversation_id`. See `SCORING.md`-style keys per role:

```json
{
  "enron_<id>": {
    "explicit_facts": ["self-contained atomic facts about the target peer that a perfect deriver must extract"],
    "short_summary": "reference short summary text",
    "long_summary": "reference long summary text",
    "qa": [
      {"q": "question", "a": "ground-truth answer", "evidence": "phrase(s) in the conversation supporting it"}
    ],
    "deductive": ["logical implications/updates a deduction specialist should draw"],
    "inductive": ["behavioral patterns/preferences a psychologist would infer, each backed by 2+ sources"]
  }
}
```

## Role list (canonical, from honcho telemetry CallPurpose)
1. deriver.representation — explicit atomic facts about target peer (single-shot, structured `{"explicit": [...]}`)
2. summary.short — concise rolling summary (~20 msgs)
3. summary.long — thorough cumulative summary (~60 msgs)
4. dialectic.answer — agentic Q&A over memory (tool loop)
5. dream.deduction — deductive observations + peer-card write
6. dream.induction — inductive patterns/preferences
7. dream.card_refresh — peer-card-only maintenance

## Scoring principle
Each role is scored by matching the model's structured output against ground truth. Prefer automatic metrics (precision/recall over reference fact sets via string/embedding similarity) over LLM-as-judge, but an LLM-as-judge pass is acceptable where exact match is meaningless (summaries). Quantified per-role scores go in reports/.

## Model config
Keys come from env: `OPENAI_API_KEY` (+ `OPENAI_BASE_URL` if set), `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`. The runner reads these; it never hardcodes keys.
