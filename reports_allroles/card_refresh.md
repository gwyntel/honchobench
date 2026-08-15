# HonchoBench — card_refresh

Scoring: matched against labels.json ground truth. Primary metric: **f1**.

## Per-model aggregate

| model | n_convs | f1 | precision | recall |
|-------|---------|---|---|---|
| hermes.main.fast | 2 | 0.600 | 0.429 | 1.000 |

Labeled conversations scored: 1/2.

## Per-conversation detail

| model | conversation_id | f1 | precision | recall |
|-------|-----------------|---|---|---|
| hermes.main.fast | enron_dasovich_steffes_marketpower | 0.600 | 0.429 | 1.000 |
| hermes.main.fast | enron_jones_greenberg_nda | no-label | no-label | no-label |

## Raw output (first conversation per model)

### hermes.main.fast — enron_dasovich_steffes_marketpower (tool calls: 7, iterations: 6)
```
The peer card has been rebuilt from the available observations. The card captures the stable identity markers that are directly evidenced:

- **IDENTITY**: Jeff Dasovich, with his Enron email address and status as an Enron employee.
- **RELATIONSHIP**: His employer (Enron) and colleagues (Steve Kean, James D Steffes) — all directly evidenced in the message threads.
- **ATTRIBUTE**: His work domain in energy market power and policy, supported by the content of the correspondence discussing market power assessment.

The observations available in this session were limited (only two messages), so I kept the card focused on what is directly evidenced without over-inferring roles, titles, or behavioral traits.
```

