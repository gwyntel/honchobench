"""HonchoBench — a standalone, model-agnostic benchmark harness for the 7 honcho roles.

Reproduces the exact prompts and tool contracts from ROLE_SPEC.md without importing
honcho or requiring a live server. Provides:

  - prompts:   verbatim prompt constructors for all 7 roles
  - clients:   thin async transport wrapper (openai / anthropic / gemini)
  - store:     in-memory conversation store + fake tool executors
  - tool_loop: the agentic tool-call loop
  - roles:     per-role run_* entry points
  - scoring:   automatic metrics vs ground-truth labels
  - report:    .md + .csv per-role report writer
"""

__version__ = "0.1.0"

ROLES = [
    "deriver",
    "summary_short",
    "summary_long",
    "dialectic",
    "deduction",
    "induction",
    "card_refresh",
]
