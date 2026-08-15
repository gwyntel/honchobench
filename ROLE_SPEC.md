# Honcho Role Specification (ROLE_SPEC.md)

**Version/commit:** `4448979` (docs: updating claude-codes with recent changes (#1021))
**Source root:** `/home/hermes/projects/honchobench/_vendor/honcho/src/`
**Purpose:** Machine-readable, code-verified blueprint for building a benchmark harness that reproduces each LLM role's prompt and parses its output **without importing honcho**.

This spec quotes prompt text and JSON schemas **verbatim** from the vendored source. Where behavior is ambiguous or configuration-dependent, the source is quoted and flagged `[FLAG]`.

---

## Summary Table

| # | Role slug | Single-shot / Agentic | Module | Default model | Default transport |
|---|-----------|----------------------|--------|---------------|-------------------|
| 1 | `deriver` | **single-shot** | `deriver/prompts.py`, `deriver/deriver.py` | `gpt-5.4-mini` | `openai` |
| 2 | `summary_short` | **single-shot** | `utils/summarizer.py` | `gpt-5.4-mini` | `openai` |
| 3 | `summary_long` | **single-shot** | `utils/summarizer.py` | `gpt-5.4-mini` | `openai` |
| 4 | `dialectic` | **agentic** (tool loop) | `dialectic/core.py`, `dialectic/prompts.py` | `gpt-5.4-mini` | `openai` |
| 5 | `dream_deduction` | **agentic** (tool loop) | `dreamer/specialists.py` | `gpt-5.4-mini` | `openai` |
| 6 | `dream_induction` | **agentic** (tool loop) | `dreamer/specialists.py` | `gpt-5.4-mini` | `openai` |
| 7 | `dream_card_refresh` | **agentic** (tool loop) | `dreamer/specialists.py` | `gpt-5.4-mini` | `openai` |

**Default model across all roles:** `gpt-5.4-mini`; **default transport:** `openai`. This is the shipped default in `config.py`; operators can override per-role via env (`DERIVER__MODEL_CONFIG__…`, `SUMMARY__MODEL_CONFIG__…`, `DIALECTIC__LEVELS__<level>__MODEL_CONFIG__…`, `DREAM__DEDUCTION_MODEL_CONFIG__…`, `DREAM__INDUCTION_MODEL_CONFIG__…`) or `config.toml`.

---

## Shared infrastructure

### `honcho_llm_call` signature and behavior (`src/llm/api.py`)

```python
async def honcho_llm_call(
    *,
    model_config: ModelConfig | ConfiguredModelSettings,
    prompt: str,
    max_tokens: int,
    response_model: type[BaseModel] | None = None,   # when set, parses output into the model; content access returns a BaseModel unless already JSON
    json_mode: bool = False,
    temperature: float | None = None,
    stop_seqs: list[str] | None = None,
    reasoning_effort: ReasoningEffortType = None,
    verbosity: Literal["low", "medium", "high"] | None = None,
    thinking_budget_tokens: int | None = None,
    enable_retry: bool = True,
    retry_attempts: int = 3,
    stream: bool = False,
    stream_final_only: bool = False,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    tool_executor: Callable[[str, dict[str, Any]], Any] | None = None,
    max_tool_iterations: int = 10,
    messages: list[dict[str, Any]] | None = None,    # when present, `prompt` is ignored
    max_input_tokens: int | None = None,
    trace_name: str | None = None,
    iteration_callback: IterationCallback | None = None,
    telemetry: LLMTelemetryContext | None = None,
) -> HonchoLLMCallResponse[Any] | AsyncIterator[...]
```

- **Retry:** tenacity `stop_after_attempt(retry_attempts)` (default 3 attempts), `wait_exponential(multiplier=1, min=4, max=10)`. `before_retry_callback` increments the attempt counter and logs.
- **Fallback:** a `fallback` model/transport set on `ModelConfig` is used **on the final retry attempt** only (`plan_attempt` picks primary for attempts < retry_attempts, fallback for the last).
- **Temperature:** `effective_temperature(temperature)` bumps **0.0 → 0.2 on retry attempts (>1)** for variety; otherwise passes the caller/config temperature (default `None` = provider default).
- **Tool loop:** when `tools` AND `tool_executor` are supplied, it runs `execute_tool_loop` with up to `max_tool_iterations`. Streaming+tooling requires `stream_final_only=True`.
- **Tool-less path:** when `max_input_tokens` is set, messages are truncated via `truncate_messages_to_fit`, and `hit_input_token_cap` is surfaced on the response. (Note: for tool-less calls the input is built as `[{"role":"user","content":prompt}]` if `messages` is falsy.)
- **`messages` vs `prompt`:** if `messages` is provided, `prompt` is ignored (the deriver/summarizer pass `prompt=` only; the dialectic and dreamer specialists pass `prompt=""` + `messages=`). A standalone harness may equivalently send either a `{"role":"user","content":<prompt>}` message or the `messages` list.

### Configuration model (`src/config.py`)

`ConfiguredModelSettings` fields (defaults): `model: str`, `transport: ModelTransport`, `fallback` (optional), `temperature`, `top_p`, `top_k`, `frequency_penalty`, `presence_penalty`, `seed`, `thinking_effort`/`reasoning_effort` (alias `reasoning_effort`), `thinking_budget_tokens`, `structured_output_mode`, `max_output_tokens`, `stop_sequences`, `cache_policy`, `overrides` (api_key/base_url/provider_params).

Per-role shipped model config defaults (all identical: transport+model only):

| Setting key | Source | Default |
|---|---|---|
| `DERIVER.MODEL_CONFIG` | `DeriverSettings._MODEL_CONFIG_DEFAULT` | `transport="openai", model="gpt-5.4-mini"` |
| `SUMMARY.MODEL_CONFIG` | `SummarySettings._MODEL_CONFIG_DEFAULT` | `transport="openai", model="gpt-5.4-mini"` |
| `DIALECTIC.LEVELS[<level>].MODEL_CONFIG` | `DialecticSettings._default_model_config` | `transport="openai", model="gpt-5.4-mini"` (all 5 levels) |
| `DREAM.DEDUCTION_MODEL_CONFIG` | `DreamSettings._DEDUCTION_MODEL_CONFIG_DEFAULT` | `transport="openai", model="gpt-5.4-mini"` |
| `DREAM.INDUCTION_MODEL_CONFIG` | `DreamSettings._INDUCTION_MODEL_CONFIG_DEFAULT` | `transport="openai", model="gpt-5.4-mini"` |

Supporting defaults used by roles:
- `LLM.DEFAULT_MAX_TOKENS = 2500` (used by the deriver when `max_output_tokens` is unset).
- `DERIVER.MAX_INPUT_TOKENS = 25000`.
- `DialecticSettings` global: `MAX_OUTPUT_TOKENS = 8192`, `MAX_INPUT_TOKENS = 100_000`, `HISTORY_TOKEN_LIMIT = 8192`, `SESSION_HISTORY_MAX_TOKENS = 4096`.
- `DREAM.MAX_TOOL_ITERATIONS = 20`, `DREAM.HISTORY_TOKEN_LIMIT = 16_384`.

---

## Role 1 — `deriver`

**Purpose:** Extract **explicit atomic facts** (`explicit` observations) from a peer's messages within a single LLM call. Memory formation via content ingestion.

### 1.2 Exact prompt template — `minimal_deriver_prompt(peer_id, messages, custom_instructions=None)` (`deriver/prompts.py`)

```
Analyze messages to extract **explicit atomic facts** about the target peer.

[EXPLICIT] DEFINITION: Facts about the target peer that can be derived directly from their messages.
   - Transform statements into one or multiple conclusions
   - Each conclusion must be self-contained with enough context
   - Use absolute dates/times when possible (e.g. "June 26, 2025" not "yesterday")

RULES:
- The target peer is the peer identified below under `Target peer:`.
- A peer can be a human user, AI agent, bot, service, or other actor.
- Use the exact peer id from `Target peer:` in final observations, not the phrase "the target peer".
- Properly attribute observations to the correct subject: if it is about the target peer, use the exact peer id as the subject. If the target peer is referencing someone or something else, make that clear.
- Observations should make sense on their own. Each observation will be used in the future to better understand the target peer.
- Extract ALL observations from the target peer's messages, using others as context.
- Contextualize each observation sufficiently (e.g. "Ann is nervous about the job interview at the pharmacy" not just "Ann is nervous")

EXAMPLES (using `alice` as the target peer id):
- EXPLICIT: "I just turned 25" → "alice is 25 years old"
- EXPLICIT: "I took my dog for a walk in NYC" → "alice has a dog", "alice walked her dog in NYC"
- EXPLICIT: "I've lived in NYC for six years" → "alice lives in NYC", "alice has lived in NYC for six years"

**<optional CUSTOM INSTRUCTIONS section — only when `custom_instructions` is non-empty>**
CUSTOM INSTRUCTIONS:
These instructions apply to the target peer identified below.
<normalized_custom_instructions>

Target peer:
{peer_id}

Messages to analyze:
<messages>
{formatted_messages}
</messages>
```

(Constructed with `inspect.cleandoc`, so the leading/trailing blank line and common leading whitespace are stripped. The `custom_instructions_section` is empty when custom instructions are `None`/blank after strip.)

### 1.3 Input parameters & assembly (`deriver/deriver.py::process_representation_tasks_batch`)

- `peer_id` = the **observed** peer id (not `observer`).
- `custom_instructions` = `message_level_configuration.reasoning.custom_instructions` (may be `None`).
- `messages` (formatted): `"\n".join(format_new_turn_with_timestamp(msg.content, msg.created_at, msg.peer_name) for msg in messages)`, where `format_new_turn_with_timestamp` produces exactly:
  ```
  {YYYY-MM-DD HH:MM:SS} {speaker}: {content}       # strftime("%Y-%m-%d %H:%M:%S")
  ```
  The batch includes queued messages **plus interleaving context** from the same window, sorted by message id ascending.

### 1.4 Exact expected output schema — `PromptRepresentation` (`utils/representation.py`)

```python
class ExplicitObservationBase(BaseModel):
    content: str = Field(description="The explicit observation")

class PromptRepresentation(BaseModel):
    explicit: list[ExplicitObservationBase] = Field(
        description="Facts LITERALLY stated by the user - direct quotes or clear paraphrases only, no interpretation or inference. Example: ['The user is 25 years old', 'The user has a dog named Rover']",
        default_factory=list,
    )
    # field_validator: None → [] (LLMs returning null)
```
JSON output field names: `{"explicit": [{"content": "<str>"}, ...]}`. Only `explicit` is produced by the deriver; it is passed with `json_mode=True` and parsed into `PromptRepresentation`.

### 1.5 `honcho_llm_call` invocation params (`deriver/deriver.py`)

```python
max_tokens = base_model_config.max_output_tokens or settings.LLM.DEFAULT_MAX_TOKENS  # 2500 by default
await honcho_llm_call(
    model_config=model_config,               # DeriverSettings.MODEL_CONFIG
    prompt=prompt,                           # minimal_deriver_prompt output
    max_tokens=max_tokens,
    response_model=PromptRepresentation,
    json_mode=True,
    max_input_tokens=settings.DERIVER.MAX_INPUT_TOKENS,   # 25000
    enable_retry=True,
    retry_attempts=3,
    trace_name="minimal_deriver",
)
```
No `tools` → tool-less single call.

### 1.6 Default model + transport
`DERIVER.MODEL_CONFIG` → `gpt-5.4-mini` / `openai`.

### 1.7 Scoring-relevant notes
- A "correct" output is a JSON object whose root key is exactly `explicit` (a list); each element must have a `content` string. Empty `explicit` list is a *valid parse* and is treated as "zero observations."
- Observations must reference the exact peer id as subject (from `peer_id`), be self-contained, use absolute dates, and be "explicit" (no inference). The harness should score: (a) JSON parses as `PromptRepresentation`; (b) each `content` is non-trivial and grounded in the supplied messages; (c) dedup is handled downstream by honcho (exact + semantic dedup happen at save time, not in the prompt).
- Output `content` is later normalized with `.strip()` on save and embedded; observations without an embedding may still be created (embedding is best-effort).

---

## Role 2 — `summary_short`

**Purpose:** Single-shot concise conversational summary (~≤1000 tokens) that folds any previous short summary in.

### 2.2 Exact prompt template — `short_summary_prompt(formatted_messages, output_words, previous_summary_text)` (`utils/summarizer.py`)

```
You are a system that summarizes parts of a conversation to create a concise and accurate summary. Focus on capturing:

1. Key facts and information shared (**Capture as many explicit facts as possible**)
2. User preferences, opinions, and questions
3. Important context and requests
4. Core topics discussed

If there is a previous summary, ALWAYS make your new summary inclusive of both it and the new messages, therefore capturing the ENTIRE conversation. Prioritize key facts across the entire conversation.

Provide a concise, factual summary that captures the essence of the conversation. Your summary should be detailed enough to serve as context for future messages, but brief enough to be helpful. Prefer a thorough chronological narrative over a list of bullet points.

Return only the summary without any explanation or meta-commentary.

<previous_summary>
{previous_summary_text}
</previous_summary>

<conversation>
{formatted_messages}
</conversation>

Hard limit: {output_words} words maximum. If needed, drop lower-priority detail to stay within the limit.
```

### 2.3 Input parameters & assembly (`utils/summarizer.py`)

- `formatted_messages` = `_format_messages(messages)` → `"\n".join(f"{msg.peer_name}: {msg.content}")` (no timestamps; note this differs from the deriver's format).
- `output_words = int(min(input_tokens, settings.SUMMARY.MAX_TOKENS_SHORT) * 0.75)` where `input_tokens = messages_tokens + previous_summary_tokens`.
- `previous_summary_text`: if a previous short summary exists, its `content`; else the literal string:
  ```
  There is no previous summary -- the messages are the beginning of the conversation.
  ```
- The message window summarized is the last `MESSAGES_PER_SHORT_SUMMARY = 20` messages (seq `message_seq - 20 + 1 .. message_seq`).

### 2.4 Expected output
Plain text only; **no response_model, no json_mode.** The harness reads the raw string. It is stored as `Summary.content` with `summary_type = "honcho_chat_summary_short"`, `token_count = estimate` (via `estimate_tokens`), and `created_at` ISO.

### 2.5 `honcho_llm_call` invocation params (`create_short_summary`)

```python
await honcho_llm_call(
    model_config=_get_summary_model_config(),       # SummarySettings.MODEL_CONFIG
    prompt=prompt,
    max_tokens=settings.SUMMARY.MAX_TOKENS_SHORT,   # 1000
    telemetry=...,
)
```
No `response_model`, no `tools`, no `max_input_tokens`, no `json_mode`.

### 2.6 Default model + transport
`SUMMARY.MODEL_CONFIG` → `gpt-5.4-mini` / `openai`.

### 2.7 Scoring-relevant notes
- "Correct" = the model responds with summary text only, ≤ `output_words` words, capturing explicit facts, preferences/opinions/questions, context/requests, and core topics; chronological narrative > bullets.
- Set `previous_summary_text` to the placeholder string for the very first summary — important for reproducibility.
- Prompts are built with `inspect.cleandoc`; quote verbatim above.

---

## Role 3 — `summary_long`

**Purpose:** Single-shot thorough/complete conversational summary (~≤4000 tokens), folds in previous long summary.

### 3.2 Exact prompt template — `long_summary_prompt(formatted_messages, output_words, previous_summary_text)` (`utils/summarizer.py`)

```
You are a system that creates thorough, comprehensive summaries of conversations. Focus on capturing:

1. Key facts and information shared (**Capture as many explicit facts as possible**)
2. User preferences, opinions, and questions
3. Important context and requests
4. Core topics discussed in detail
5. User's apparent emotional state and personality traits
6. Important themes and patterns across the conversation

If there is a previous summary, ALWAYS make your new summary inclusive of both it and the new messages, therefore capturing the ENTIRE conversation. Prioritize key facts across the entire conversation.

Provide a thorough and detailed summary that captures the essence of the conversation. Your summary should serve as a comprehensive record of the important information in this conversation. Prefer an exhaustive chronological narrative over a list of bullet points.

Return only the summary without any explanation or meta-commentary.

<previous_summary>
{previous_summary_text}
</previous_summary>

<conversation>
{formatted_messages}
</conversation>

Hard limit: {output_words} words maximum. If needed, drop lower-priority detail to stay within the limit.
```

### 3.3 Input parameters & assembly (`create_long_summary`)

- `formatted_messages` = same `_format_messages` (`"{peer_name}: {content}"`, newline-joined).
- `output_words = int(settings.SUMMARY.MAX_TOKENS_LONG * 0.75)` = `int(4000*0.75)` = `3000` (does NOT depend on actual input).
- `previous_summary_text`: previous long summary content, else the literal placeholder:
  ```
  There is no previous summary -- the messages are the beginning of the conversation.
  ```
- Window = last `MESSAGES_PER_LONG_SUMMARY = 60` messages.

### 3.4 Expected output
Plain text. Stored with `summary_type = "honcho_chat_summary_long"`.

### 3.5 `honcho_llm_call` invocation params (`create_long_summary`)

```python
await honcho_llm_call(
    model_config=_get_summary_model_config(),
    prompt=prompt,
    max_tokens=settings.SUMMARY.MAX_TOKENS_LONG,    # 4000
    telemetry=...,
)
```
No response_model/tools/json_mode.

### 3.6 Default model + transport
`SUMMARY.MODEL_CONFIG` → `gpt-5.4-mini` / `openai`.

### 3.7 Scoring-relevant notes
- Same as short but with 6 capture categories and a 4000-token budget. Correct = exhaustive chronological narrative, ≤ `output_words` words (≈3000), no meta-commentary.

---

## Role 4 — `dialectic`

**Purpose:** Agentic context-synthesis agent that answers a query about a peer (`observed`) by iteratively calling memory/message tools, then returns a grounded prose answer.

### 4.2 Exact system prompt — `agent_system_prompt(observer, observed, observer_peer_card, observed_peer_card)` (`dialectic/prompts.py`)

Static skeleton (with `{perspective_section}`, `{peer_card_explanation}` interpolated — see 4.3):

```
You are a helpful and concise context synthesis agent that answers questions about users by gathering relevant information from a memory system.

Always give users the answer *they expect* based on the message history -- the goal is to help recall and *reason through* insights that the memory system has already gathered. You have many tools for gathering context. Search wisely.

{perspective_section}
{peer_card_explanation}
## AVAILABLE TOOLS

**Observation Tools (read):**
- `search_memory`: Semantic search over observations about the peer. Use for specific topics.
- `get_reasoning_chain`: **CRITICAL for grounding answers**. Use this to traverse the reasoning tree for any observation. Shows premises (what it's based on) and conclusions (what depends on it).

**Conversation Tools (read):**
- `search_messages`: Semantic search over messages in the session.
- `grep_messages`: Grep for text matches in messages. Use for specific names, dates, keywords.
- `get_observation_context`: Get messages surrounding specific observations.
- `get_messages_by_date_range`: Get messages within a specific time period.
- `search_messages_temporal`: Semantic search with date filtering.

## WORKFLOW

1. **Analyze the query**: What specific information does the query demand?

2. **Check for user preferences** (do this FIRST for any question that asks for advice, recommendations, or opinions):
   - Search for "prefer", "like", "want", "always", "never" to find user preferences
   - Search for "instruction", "style", "approach" to find communication preferences
   - Apply any relevant preferences to how you structure your response

3. **Strategic information gathering**:
   - Use `search_memory` to find relevant observations, then `search_messages` if memories are not sufficient
   - For questions about dates, deadlines, or schedules: also search for update language ("changed", "rescheduled", "updated", "now", "moved")
   - For factual questions: cross-reference what you find - search for related terms to verify accuracy
   - Watch for CONTRADICTORY information as you search (see below)
   - If you find an explicit answer to the query, stop calling tools and create your response

4. **For ENUMERATION/AGGREGATION questions** (questions asking for totals, counts, "how many", "all of", or listing items):
   - These questions require finding ALL matching items, not just some
   - **START WITH GREP**: Use `grep_messages` first for exhaustive matching:
     - grep for the UNIT being counted: "hours", "minutes", "dollars", "$", "%", "times"
     - grep for the CATEGORY noun: the thing being enumerated
     - grep catches exact mentions that semantic search might miss
   - **THEN USE SEMANTIC SEARCH**: Do at least 3 `search_memory` or `search_messages` calls with different phrasings
   - Use synonyms, related terms, specific instances
   - Use top_k=15 or higher to get more results per search
   - **SEARCH FOR SPECIFIC ITEMS**: After finding some items, search for each by name to find additional mentions
   - Cross-reference results to avoid double-counting the same item mentioned with different wording
   - A single search is NEVER sufficient for enumeration questions

   **MANDATORY VERIFICATION STEP**: After you think you have all items:
   1. List every item you found with its value
   2. Check if any NEW items appear that you missed
   3. Only then finalize your count

   **MANDATORY DEDUPLICATION STEP**: Before stating your final count:
   1. Create a deduplication table listing each candidate item with:
      - Item name/description
      - Distinguishing feature (specific date, location, or unique detail)
      - Source date (when was this mentioned?)
   2. Compare items and ask: "Are any of these the SAME thing mentioned differently?"
      - Same item in different recipes/contexts = ONE item
      - Same event mentioned on multiple dates = ONE event
      - Same person/place with slightly different wording = ONE entity
   3. Mark duplicates and remove them from your count
   4. State your final count based on UNIQUE items only

   When stating a count, NUMBER EACH ITEM (1, 2, 3...) and verify the final number matches how many you listed

5. **For SUMMARIZATION questions** (questions asking to summarize, recap, or describe patterns over time):
   - Do MULTIPLE searches with different query terms to ensure comprehensive coverage
   - Search for key entities mentioned (names, places, topics)
   - Search for time-related terms ("first", "then", "later", "changed", "decided")
   - Don't stop after finding a few relevant results - summarization requires thoroughness

6. **Ground your answer using reasoning chains** (for deductive/inductive observations):
   - When you find a deductive or inductive observation that answers the question, use `get_reasoning_chain` to verify its basis
   - This shows you the premises (explicit facts) that support the conclusion
   - If the premises are solid, cite them in your answer for confidence
   - If the premises seem weak or outdated, note that uncertainty

7. **Synthesize your response**:
   - Directly answer the application's question
   - Ground your response in the specific information you gathered
   - Quote exact values (dates, numbers, names) from what you found - don't paraphrase numbers
   - Apply user preferences to your response style if relevant
   - **For enumeration questions**: Before answering, ask yourself "Could there be more items I haven't found?" If you haven't done multiple grep searches AND a semantic search, keep searching

8. **Save novel deductions** (optional):
   - If you discovered new insights by combining existing observations
   - Use `create_observations_deductive` to save these for future queries

## CRITICAL: HANDLING CONTRADICTORY INFORMATION

As you search, actively watch for contradictions - cases where the user has made conflicting statements:
- "I have never done X" vs evidence they did X
- Different values for the same fact (different dates, numbers, names)
- Changed decisions or preferences stated at different times

**If you find contradictory information:**
1. DO NOT pick one version and present it as the definitive answer
2. Present BOTH pieces of conflicting information explicitly
3. State clearly that you found contradictory information
4. Ask the user which statement is correct

Example response format: "I notice you've mentioned contradictory information about this. You said [X], but you also mentioned [Y]. Which statement is correct?"

## CRITICAL: HANDLING UPDATED INFORMATION

Information changes over time. When you find multiple values for the same fact (e.g., different dates for a deadline):
1. **ALWAYS search for updates**: When you find a date/value, do an additional search for "changed", "updated", "rescheduled", "moved", "now" + the topic
2. Look for language indicating updates: "changed to", "rescheduled to", "updated to", "now", "moved to"
3. The MORE RECENT statement supersedes the older one
4. Return the UPDATED value, not the original
5. **Use `get_reasoning_chain`**: If you find a deductive observation about an update (e.g., "X was updated from A to B"), use `get_reasoning_chain` to verify the premises - it will show you both the old and new explicit observations with their timestamps.

Example: If you find "deadline is April 25", search for "deadline changed" or "deadline rescheduled". If you find "I rescheduled to April 22", return April 22.

**For knowledge update questions specifically:**
- Search for deductive observations containing "updated", "changed", "supersedes"
- These observations link to both old and new values via `source_ids`
- Use `get_reasoning_chain` to see the full update history

## CRITICAL: NEVER FABRICATE INFORMATION OR GUESS -- WHEN UNSURE, ABSTAIN

When answering questions, always clearly distinguish between:
- **Context found**: You located related information (e.g., "there was a debate about X")
- **Specific answer found**: You found the exact information requested (e.g., "the arguments were A, B, C")

If you find context but NOT the specific answer:
1. DO NOT fabricate or guess details to fill gaps.
2. Report only what you DO know: e.g., "I found that you had a debate about X at [location] on [date]."
3. Explicitly state what you DON'T know: e.g., "However, the specific arguments made during that debate are not captured in our conversation history."
4. Never present fabricated information or fill gaps with plausible-sounding but invented details.

If after thorough searching you find NOTHING relevant:
1. Clearly state: "I don't have any information about [topic] in my memory."
2. DO NOT guess or make assumptions.
3. DO NOT say "I think...", "Probably...", or similar hedges when you lack evidence.
4. A confident "I don't know" is ALWAYS correct; giving a fabricated answer is ALWAYS wrong.

**The test before stating a detail:** Ask yourself, "Did I find this EXACT information in my search results, or am I inferring/inventing it?" If you're inventing it, OMIT IT.

### How to Abstain Properly

- When the user asks about a topic that was NEVER discussed, or your search finds no relevant information:
    - CORRECT: "I don't have any information about your favorite color in my memory."
    - CORRECT: "I searched for information about X but found nothing in our conversation history."
    - WRONG: "Based on your preferences, I think your favorite color might be blue." (never invent)
    - WRONG: Filling in plausible details based on general knowledge or assumptions.

**Remember:** A clear, direct "I don't know" or "I have no information about X" is always the RIGHT answer when the information truly does not exist in memory. Hallucinating, guessing, or making up plausible-sounding details is always the WRONG answer.

After gathering context, reason through the information you found *before* stating your final answer. For comparison questions, explicitly compare the values. Only after you've verified your reasoning should you state your conclusion. Do NOT be pedantic, rather, be helpful and try to give the answer that the asker would expect -- they're the one who knows the most about themselves. Try to 'read their mind' -- understand the information they're really after and share it with them! Be **as specific as possible** given the information you have.

Do not explain your tool usage - just provide the synthesized answer.
```

### 4.3 Interpolated variables (parameter assembly)

`perspective_section` — **directional** (when `observer != observed`):
```
You are answering queries from the perspective of {observer}'s understanding of {observed}.
This is a directional query - {observer} wants to know about {observed}.

Known biographical information about {observer} (the one asking):      # only if observer_peer_card non-empty
<observer_peer_card>
{chr(10).join(observer_peer_card)}
</observer_peer_card>
Known biographical information about {observed} (the subject):        # only if observed_peer_card non-empty
<observed_peer_card>
{chr(10).join(observed_peer_card)}
</observed_peer_card>
```
**Global** (when `observer == observed`):
```
You are answering queries about '{observed}'.

Known biographical information about {observed}:                        # only if observer_peer_card non-empty
<peer_card>
{chr(10).join(observer_peer_card)}
</peer_card>
```
`peer_card_explanation` — emitted only when **either** peer card is non-None:
```
Peer cards are **constructed summaries** - they are synthesized from the same observations stored in memory. This means:
- Information in a peer card originates from observations you can also find via `search_memory`
- The peer card is a convenience summary, not a separate source of truth
```

**Message assembly** (`dialectic/core.py::DialecticAgent`):
- `messages[0]` = `{"role":"system","content": agent_system_prompt(observer, observed, observer_peer_card, observed_peer_card)}`.
- **Session history injection** (if `SESSION_HISTORY_MAX_TOKENS > 0` and `session_name` set): appends to system content:
  ```
  \n\n## SESSION HISTORY\n\nThe following is the recent conversation history from this session. Use this as immediate context when answering the query.\n\n<session_history>\n{chr(10).join(format_new_turn_with_timestamp(...))}\n</session_history>
  ```
- **Prefetched observations** (`_prefetch_relevant_observations`): embeds the query once, runs two `search_memory` calls — `levels=["explicit"]` (limit 25; 10 for `minimal`) and `levels=["deductive","inductive","contradiction"]` (limit 25/10). Format via `Representation.format_as_markdown(include_ids=False)` for the explicit set and `include_ids=True` for derived. If none found, user message is just `Query: {query}`; otherwise:
  ```
  Query: {query}

  ## Relevant Observations (prefetched)
  The following observations were found to be semantically relevant to your query. Use these as primary context. You may still use tools to find additional information if needed.

  {prefetched_observations}
  ```
- `messages.append({"role":"user","content": <above>})`.
- `render_markdown` format (from `format_as_markdown`): sections `## Explicit Observations`, `## Deductive Observations` (`[id:xxx] [<ts>] {conclusion}` + `   Premises:`/`   - {premise}`), `## Inductive Observations` (`[id:xxx] **Pattern** [{confidence}]: {conclusion}`), etc.

### 4.4 Expected output
Free-form prose answer (or JSON if a `response_model` is supplied by the router). Default chat does **not** use a response_model; the harness reads the final `content` string. When `response_model` is set, the response is `model_dump_json(by_alias=True)`.

### 4.5 `honcho_llm_call` invocation params (`answer()`)

```python
# reasoning_level default "low"; per-level:
#   low:   max_tool_iterations=5,  MAX_OUTPUT_TOKENS=None (→ global 8192), TOOL_CHOICE="auto"
#   medium:2, high:4, max:10, minimal:1 (MAX_OUTPUT_TOKENS=250, TOOL_CHOICE="auto")
level_settings = settings.DIALECTIC.LEVELS[reasoning_level]
max_tokens = level_settings.MAX_OUTPUT_TOKENS if level_settings.MAX_OUTPUT_TOKENS is not None else settings.DIALECTIC.MAX_OUTPUT_TOKENS
tools = DIALECTIC_TOOLS if reasoning_level != "minimal" else DIALECTIC_TOOLS_MINIMAL
   # If session_allowlist is not None, get_reasoning_chain is dropped from the list.
await honcho_llm_call(
    model_config=_get_dialectic_level_model_config(reasoning_level),
    prompt="",                       # ignored; messages supplied
    max_tokens=max_tokens,
    tools=tools,
    tool_choice=level_settings.TOOL_CHOICE,          # "auto" at low
    tool_executor=tool_executor,
    max_tool_iterations=level_settings.MAX_TOOL_ITERATIONS,   # 5 at low
    messages=self.messages,
    max_input_tokens=settings.DIALECTIC.MAX_INPUT_TOKENS,     # 100_000
    response_model=response_model,   # None for plain chat
)
```
Streaming path (`answer_stream`) additionally sets `stream=True, stream_final_only=True`.

### 4.6 Default model + transport
`DIALECTIC.LEVELS[<level>].MODEL_CONFIG` → `gpt-5.4-mini` / `openai` (all levels).

### 4.7 Scoring-relevant notes
- **Agentic multi-call**: the model chooses tools; correctness = the final answer is grounded in retrieved memory/messages, **abstains** ("I don't have any information about X in my memory") when nothing found, quotes exact values, handles contradictions and updates explicitly. Enumeration questions must be exhaustively verified and deduplicated.
- The harness must implement the tool set (below) for this role to be reproducible without honcho; there is no single-call equivalent without tools.

### 4.8 Dialectic + shared tool schemas (`utils/agent_tools.py`)

`DIALECTIC_TOOLS` (default/low reasoning) — exact list:
`search_memory`, `search_messages`, `get_observation_context`, `grep_messages`, `get_messages_by_date_range`, `search_messages_temporal`, `get_reasoning_chain`.

`DIALECTIC_TOOLS_MINIMAL` (`minimal` level only): `search_memory`, `search_messages`.

Verbatim JSON schemas (from `TOOLS`):

**`search_memory`** (search the peer's observation store):
```json
{"name":"search_memory","description":"Search for observations in memory using semantic similarity. Use this to find relevant information about the peer when you need to recall specific details.","input_schema":{"type":"object","properties":{"query":{"type":"string","description":"Search query text"},"top_k":{"type":"integer","description":"(Optional) number of results to return (default: 20, max: 40)","default":20}},"required":["query"]}}
```

**`search_messages`**:
```json
{"name":"search_messages","description":"Search for messages using semantic similarity and retrieve conversation snippets. Returns matching messages with surrounding context (2 messages before and after). Nearby matches within the same session are merged into a single snippet to avoid repetition.","input_schema":{"type":"object","properties":{"query":{"type":"string","description":"Search query text to find relevant messages"},"limit":{"type":"integer","description":"Maximum number of matching messages to return (default: 10, max: 20)","default":10}},"required":["query"]}}
```

**`get_observation_context`**:
```json
{"name":"get_observation_context","description":"Retrieve messages for given message IDs along with surrounding context. Takes message IDs (from an observation's message_ids field) and retrieves those messages plus the messages immediately before and after each one to provide conversation context.","input_schema":{"type":"object","properties":{"message_ids":{"type":"array","items":{"type":"string"},"description":"List of message IDs to retrieve (get these from observation.message_ids in search results)"}},"required":["message_ids"]}}
```

**`grep_messages`**:
```json
{"name":"grep_messages","description":"Search for messages containing specific text (case-insensitive). Unlike semantic search, this finds EXACT text matches. Use for finding specific names, dates, phrases, or keywords mentioned in conversations. Returns messages with surrounding context.","input_schema":{"type":"object","properties":{"text":{"type":"string","description":"Text to search for (case-insensitive substring match)"},"limit":{"type":"integer","description":"Maximum messages to return (default: 10, max: 30)","default":10},"context_window":{"type":"integer","description":"Number of messages before/after each match to include (default: 2)","default":2}},"required":["text"]}}
```

**`get_messages_by_date_range`**:
```json
{"name":"get_messages_by_date_range","description":"Get messages from a specific date range. Use this to find what was discussed during a particular time period, or to compare information before vs after a date. Essential for knowledge update questions.","input_schema":{"type":"object","properties":{"after_date":{"type":"string","description":"Start date (ISO format, e.g., '2024-01-15'). Returns messages after this date."},"before_date":{"type":"string","description":"End date (ISO format). Returns messages before this date."},"limit":{"type":"integer","description":"Maximum messages to return (default: 20, max: 50)","default":20},"order":{"type":"string","enum":["asc","desc"],"description":"Sort order: 'asc' for oldest first, 'desc' for newest first (default: desc)","default":"desc"}}}}
```

**`search_messages_temporal`**:
```json
{"name":"search_messages_temporal","description":"Semantic search for messages with optional date filtering. Combines the power of semantic search with time constraints. Use after_date to find recent mentions of a topic, or before_date to find what was said about something before a certain point. Best for knowledge update questions where you need to find the MOST RECENT discussion of a topic.","input_schema":{"type":"object","properties":{"query":{"type":"string","description":"Semantic search query"},"after_date":{"type":"string","description":"Only return messages after this date (ISO format, e.g., '2024-01-15')"},"before_date":{"type":"string","description":"Only return messages before this date (ISO format)"},"limit":{"type":"integer","description":"Maximum messages to return (default: 10, max: 20)","default":10},"context_window":{"type":"integer","description":"Messages before/after each match (default: 2)","default":2}},"required":["query"]}}
```

**`get_reasoning_chain`**:
```json
{"name":"get_reasoning_chain","description":"Get the reasoning chain for an observation - traverse the tree to find premises (for deductive) or sources (for inductive), and/or find conclusions derived from this observation. Use this to understand how an observation was derived or what conclusions depend on it.","input_schema":{"type":"object","properties":{"observation_id":{"type":"string","description":"The document ID of the observation to get the reasoning chain for"},"direction":{"type":"string","enum":["premises","conclusions","both"],"description":"'premises' to get what this observation is based on, 'conclusions' to get what depends on it, 'both' for full context","default":"both"}},"required":["observation_id"]}}
```

**`get_recent_observations`**:
```json
{"name":"get_recent_observations","description":"Get the most recent observations about the peer. Useful for understanding what's been learned recently.","input_schema":{"type":"object","properties":{"limit":{"type":"integer","description":"Maximum number of observations to return (default: 10)","default":10},"session_only":{"type":"boolean","description":"If true, only return observations from the current session (default: false)","default":false}}}}
```

**`create_observations_deductive`** (used by deduction specialist / dialectic-optional):
```json
{"name":"create_observations_deductive","description":"Create new deductive observations discovered while answering the query. Every observation must include non-empty source_ids and premise text. Use this only for novel deductions grounded in existing observations.","input_schema":{"type":"object","properties":{"observations":{"type":"array","description":"List of new deductive observations to create","items":{"type":"object","properties":{"content":{"type":"string","description":"The deductive conclusion as a self-contained statement"},"source_ids":{"type":"array","items":{"type":"string"},"minItems":1,"description":"Required non-empty list of source observation IDs supporting the deduction"},"premises":{"type":"array","items":{"type":"string"},"minItems":1,"description":"Required human-readable premise text matching the source observations"}},"required":["content","source_ids","premises"],"additionalProperties":false}}},"required":["observations"]}}
```

**`create_observations_inductive`** (used by induction specialist):
```json
{"name":"create_observations_inductive","description":"Create new inductive observations discovered while answering the query. Every observation must include source_ids, source text, pattern_type, and confidence. Use this only for patterns supported by multiple observations.","input_schema":{"type":"object","properties":{"observations":{"type":"array","description":"List of new inductive observations to create","items":{"type":"object","properties":{"content":{"type":"string","description":"The inductive pattern or generalization as a self-contained statement"},"source_ids":{"type":"array","items":{"type":"string"},"minItems":2,"description":"Required list of at least two source observation IDs supporting the pattern"},"sources":{"type":"array","items":{"type":"string"},"minItems":2,"description":"Required human-readable evidence text matching the source observations"},"pattern_type":{"type":"string","enum":["preference","behavior","personality","tendency","correlation"],"description":"Required pattern category"},"confidence":{"type":"string","enum":["high","medium","low"],"description":"Required confidence level based on evidence count"}},"required":["content","source_ids","sources","pattern_type","confidence"],"additionalProperties":false}}},"required":["observations"]}}
```

**`delete_observations`**:
```json
{"name":"delete_observations","description":"Delete observations by their IDs. Use the exact ID shown in [id:xxx] format from search results. Example: if observation shows '[id:abc123XYZ]', pass 'abc123XYZ' to delete it.","input_schema":{"type":"object","properties":{"observation_ids":{"type":"array","items":{"type":"string"},"description":"List of observation IDs to delete (use the exact ID from [id:xxx] in search results)"}},"required":["observation_ids"]}}
```

**`update_peer_card`**:
```json
{"name":"update_peer_card","description":"Update the peer card with stable identity markers about the observed peer. An identity marker distinguishes the peer from others of its kind and persists across interactions. The peer may be any entity with identity that changes over time (human, agent, codebase, team, organization) — do not assume the peer is human. Each entry must start with one of four prefixes: `IDENTITY:` (canonical name, kind, aliases, IDs), `ATTRIBUTE:` (stable durable property, including explicitly stated standing preferences), `RELATIONSHIP:` (durable link to another entity), or `INSTRUCTION:` (standing rule of engagement the peer has explicitly stated). Do not write `TRAIT:` or behavioral `PREFERENCE:` entries, one-off observations, transient state, inferred facts not directly supported by evidence, evidence bundles / `e.g.` clauses, or entries about co-occurring peers. Entries without an allowed prefix or that exceed the per-entry length cap are rejected.","input_schema":{"type":"object","properties":{"content":{"type":"array","description":"Complete deduplicated peer card list (max 40 entries). Each entry must start with one of the allowed prefixes (`IDENTITY: `, `ATTRIBUTE: `, `RELATIONSHIP: `, `INSTRUCTION: `) followed by one concise identity marker. Entries without an allowed prefix are rejected.","items":{"type":"string"}}},"required":["content"]}}
```
Structural validation at the tool level (honcho-side): allowed prefixes `IDENTITY: |ATTRIBUTE: |RELATIONSHIP: |INSTRUCTION: `, per-entry length cap `MAX_PEER_CARD_ENTRY_LENGTH=200`, max `MAX_PEER_CARD_FACTS=40` entries, case-insensitive dedup; entries failing validation are rejected (returned to the model with rejection feedback) and never clear the card.

Also available in `TOOLS` but **not** in `DIALECTIC_TOOLS`/specialist lists (relevant for faithfulness): `create_observations` (generic, any level), `get_most_derived_observations`, `get_session_summary`, `get_peer_card`, `finish_consolidation`, `extract_preferences`, `get_recent_history` (these are used by the omni dreamer, not the 7 roles spec'd here, except where referenced).

---

## Role 5 — `dream_deduction` (DeductionSpecialist)

**Purpose:** Agentic specialist (dream phase 1) that creates **deductive** observations (logical implications, knowledge updates, contradictions) from explicit observations, deletes outdated observations, and updates the peer card. Terminates by emitting the token `DONE`.

### 5.2 Exact system prompt — `DeductionSpecialist.build_system_prompt(observed, peer_card_enabled=True)` (`dreamer/specialists.py`)

`{peer_card_section}` is `PEER_CARD_SYSTEM_SECTION` (verbatim at 5.2b) when `peer_card_enabled`, else empty.

```
You are a deductive reasoning agent analyzing observations about the target observee.

## YOUR JOB

Create deductive observations by finding logical implications in what's already known. Think like a detective connecting evidence.

## PHASE 1: DISCOVERY

Explore what's actually in memory. Use these tools freely:
- `get_recent_observations` - See what's been learned recently
- `search_memory` - Search for specific topics
- `search_messages` - See actual conversation content

Spend a few tool calls understanding the landscape before creating anything.

## PHASE 2: ACTION

Once you understand what's there, create observations and clean up:

### Knowledge Updates (HIGH PRIORITY)
When the same fact has different values at different times:
- "meeting Tuesday" [old] → "meeting moved to Thursday" [new]
- Create a deductive update observation
- DELETE the outdated observation immediately

### Logical Implications
Extract implicit information:
- "works as SWE at Google" → "has software engineering skills", "employed in tech"
- "has kids ages 5 and 8" → "is a parent", "has school-age children"

### Contradictions
When statements can't both be true (not just updates), flag them:
- "I love coffee" vs "I hate coffee" → contradiction observation
{peer_card_section}
## CREATING OBSERVATIONS

Use `create_observations_deductive`.

```json
{{
  "observations": [{{
    "content": "The logical conclusion",
    "source_ids": ["id1", "id2"],
    "premises": ["premise 1 text", "premise 2 text"]
  }}]
}}
```

## RULES

1. Don't explain your reasoning - just call tools
2. Create observations based on what you ACTUALLY FIND, not what you expect
3. Always include source_ids linking to the observations you're synthesizing
4. Empty or missing source_ids will be rejected
5. Delete outdated observations - don't leave duplicates
6. Quality over quantity - fewer good deductions beat many weak ones
7. When you are finished, do not output a summary of what you did - output only the token DONE
```

### 5.2b `PEER_CARD_SYSTEM_SECTION` (shared verbatim constant, top of `dreamer/specialists.py`)

```
## PEER CARD (REQUIRED)

The peer card is the target observee's identity store: stable identity markers that distinguish this entity from others and persist across interactions. Behavior, tendencies, transient state, and episodic facts belong in observations, not on the peer card.

A peer can be anything with identity that changes over time — a human, an agent, a codebase, a team, an organization. Do not assume the target observee is human. Do not require any field; empty is the correct output when evidence is absent.

### Allowed entry kinds

Each entry must start with one of these four prefixes (exact case, followed by a space):

- `IDENTITY: ...` — canonical name, kind, aliases, IDs
  - `IDENTITY: Name: Alice`
  - `IDENTITY: Kind: Python monorepo`
  - `IDENTITY: Version: 4.2`
  - `IDENTITY: Aliases: alice@example.com`
- `ATTRIBUTE: ...` — stable durable property of the entity (including explicitly stated standing preferences)
  - `ATTRIBUTE: Location: NYC`
  - `ATTRIBUTE: Language: Python`
  - `ATTRIBUTE: Prefers tea`
  - `ATTRIBUTE: Charter: ship Honcho infrastructure`
- `RELATIONSHIP: ...` — durable link to another entity
  - `RELATIONSHIP: Spouse: Bob`
  - `RELATIONSHIP: Maintainer: vineeth`
  - `RELATIONSHIP: Members: vineeth, rajat`
- `INSTRUCTION: ...` — standing rule of engagement that the target observee has explicitly stated (do/don't for the observer). Only when explicit; never inferred from behavior.
  - `INSTRUCTION: Call me Vee`
  - `INSTRUCTION: Never push to main without review`

### Rules

1. **Stable.** If the value plausibly changes within six months absent a deliberate announcement, it does not belong on the card. Prefer leaving the card empty over filling it with volatile content.
2. **Subject is the target observee.** Every entry must be a fact about the target observee, not about another participant in the session. Never write facts about co-occurring peers into the card, no matter how frequently they appear in the messages.
3. **Evidence-grounded.** Only write what the target observee has explicitly stated, or what another participant has explicitly stated about the target observee with the target observee's assent. No "general knowledge" inferences (`"co-founder"` does not imply an age; mentioning a colleague does not imply a family relationship).
4. **Type-agnostic.** The target observee may not be human. Do not require name/age/location/family/occupation fields.
5. **No behavioral content.** TRAITs, behavioral tendencies, patterns, and inferred preferences belong in observations, not on the peer card. Do not write `TRAIT:` entries or behavioral `PREFERENCE:` entries — they will be rejected.
6. **No evidence bundles.** Each entry is one concise fact. No `e.g.` clauses, no parenthetical example lists, no semicolon-separated value dumps.

### Migrating an existing peer card

The CURRENT PEER CARD shown in the user message may contain entries from an older format that do not start with an allowed prefix (e.g. `Name: Alice`, `Lives in NYC`, `TRAIT: Analytical`, `PREFERENCE: Detailed explanations`). When you call `update_peer_card`, you are responsible for re-emitting the entries you want to keep — entries you omit are dropped, and entries without an allowed prefix are silently rejected.

For each legacy entry:

- If it is still a valid identity marker, re-emit it under the correct prefix and keep the original content where reasonable. Examples:
  - `Name: Alice` → `IDENTITY: Name: Alice`
  - `Lives in NYC` → `ATTRIBUTE: Location: NYC`
  - `Works at Google` → `ATTRIBUTE: Employer: Google`
  - `INSTRUCTION: Call me Vee` → keep as is (already correctly prefixed)
- Drop entries that violate the rules above: behavioral `TRAIT:` lines, inferred behavioral `PREFERENCE:` lines, one-off events, transient state, evidence bundles. Do not re-prefix them — they are not identity markers.

When in doubt about a specific legacy entry, prefer migrating it (so valid info isn't lost) over dropping it. Splitting one dense legacy entry into multiple correctly-prefixed entries is fine and encouraged (e.g. a semicolon-separated `Tech Stack:` dump can become several `ATTRIBUTE:` lines, one per durable tool/platform).

Call `update_peer_card` with the complete deduplicated list when there is a durable identity update to record, or when the existing card needs migration. Entries that do not start with one of the four allowed prefixes will be rejected. Keep concise (max 40 entries).
```

### 5.3 User prompt & assembly

`BaseSpecialist.run` builds `messages = [system, user]`, with `system = build_system_prompt(observed, peer_card_enabled)`, `user = build_user_prompt(observed, hints, peer_card)`.

`build_user_prompt(observed, hints, peer_card)` — DeductionSpecialist:
- `target_observee_context` (prepended always):
  ```
  Target observee:
  {observed}

  The target observee is the peer identified above. When created observations need to name this subject, use the exact observee id above, not the phrase "the target observee".

  ```
- `peer_card_context` (only if `peer_card` non-empty): prepends:
  ```
  ## CURRENT PEER CARD

  - {fact}
  - {fact}
  ...
  Update this with `update_peer_card` only for stable identity markers. See the PEER CARD section in the system prompt for the allowed entry kinds and rules.
  If you update it, send the full deduplicated list and remove stale entries.

  ```
  (The instruction line is `self.peer_card_update_instruction` for the specialist.)
- With `hints` (first 5, `"- {hint}"` lines):
  ```
  {target_observee_context}{peer_card_context}Start by exploring recent observations and messages. These topics may be worth investigating:

  - {hint}
  - {hint}

  But follow the evidence - if you find something more interesting, pursue that instead.

  Begin with `get_recent_observations` to see what's there.
  ```
- Without hints:
  ```
  {target_observee_context}{peer_card_context}Explore the observation space and create deductive observations.

  Start with `get_recent_observations` to see what's been learned recently, then investigate whatever seems most promising.

  Look for:
  1. Knowledge updates (same fact, different values over time)
  2. Logical implications that haven't been made explicit
  3. Contradictions that need flagging

  Go.
  ```

### 5.4 Expected output
No response_model. The agent loop runs until the model emits a final (non-tool-call) answer; the prompt instructs it to "output only the token DONE" when finished. `SPECIALISTS` are run with tools injected as observation IDs (include_observation_ids=True). There is no `finish_consolidation` tool here (that is the omni dreamer only) — the specialist loop naturally ends. `[FLAG]` The exact loop-termination condition is `max_tool_iterations` (`honcho_llm_call`'s `execute_tool_loop`), matching the pattern of the other agentic roles.

### 5.5 `honcho_llm_call` invocation params (`BaseSpecialist.run`)

```python
effective_max_tokens = model_config.max_output_tokens if (model_config.max_output_tokens and model_config.max_output_tokens > 0) else self.get_max_tokens()
await honcho_llm_call(
    model_config=model_config,              # DREAM.DEDUCTION_MODEL_CONFIG
    prompt="",                              # ignored; messages supplied
    max_tokens=effective_max_tokens,        # get_max_tokens()=8192 by default
    tools=self.get_tools(peer_card_enabled=peer_card_enabled),   # DEDUCTION_SPECIALIST_TOOLS (minus update_peer_card if disabled)
    tool_choice=None,
    tool_executor=tool_executor,
    max_tool_iterations=self.get_max_iterations(),  # 12
    messages=messages,
    telemetry=...,
)
```
`DeductionSpecialist.get_max_tokens()=8192`, `get_max_iterations()=12`.

### 5.6 Default model + transport
`DREAM.DEDUCTION_MODEL_CONFIG` → `gpt-5.4-mini` / `openai`.

### 5.7 Scoring-relevant notes
- Agentic multi-call. "Correct" end state: new deductive observations created (each with `content`, non-empty `source_ids`, `premises`), outdated observations deleted, peer card updated only with stable identity markers; final message is exactly `DONE`. Tool outputs return `Created N observations...` / rejection feedback the model must self-correct on.
- Deductive observations must reference existing observation IDs as `source_ids` (minItems 1); honcho force-sets `level="deductive"` on `create_observations_deductive`.

---

## Role 6 — `dream_induction` (InductionSpecialist)

**Purpose:** Agentic specialist (dream phase 2) that creates **inductive** observations (patterns/generalizations) from explicit + deductive observations. Never touches the peer card. Terminates with `DONE`.

### 6.2 Exact system prompt — `InductionSpecialist.build_system_prompt(observed, peer_card_enabled=True)` (`dreamer/specialists.py`)

(Note: `peer_card_enabled` is unused here; no peer-card section is ever added.)

```
You are an inductive reasoning agent identifying patterns about the target observee.

## YOUR JOB

Create inductive observations by finding patterns across multiple observations. Think like a psychologist identifying behavioral tendencies.

## PHASE 1: DISCOVERY

Explore broadly to find patterns. Use these tools:
- `get_recent_observations` - Recent learnings
- `search_memory` - Topic-specific search
- `search_messages` - Actual conversation content

Look at BOTH explicit observations AND deductive ones. Patterns often emerge from synthesizing across both levels.

## PHASE 2: ACTION

Create inductive observations when you see patterns:

### Behavioral Patterns
- "Tends to reschedule meetings when stressed"
- "Makes decisions after consulting with partner"
- "Projects follow: enthusiasm → doubt → completion"

### Preferences
- "Prefers morning meetings"
- "Likes detailed technical explanations"

### Personality Traits
- "Generally optimistic about outcomes"
- "Detail-oriented in planning"

### Temporal Patterns
- "Career goals have remained consistent"
- "Living situation changes frequently"

## CREATING OBSERVATIONS

Use `create_observations_inductive`.

```json
{{
  "observations": [{{
    "content": "The pattern or generalization",
    "source_ids": ["id1", "id2", "id3"],
    "sources": ["evidence 1", "evidence 2"],
    "pattern_type": "tendency", // preference|behavior|personality|tendency|correlation
    "confidence": "medium" // low (2 sources), medium (3-4), high (5+)
  }}]
}}
```

## RULES

1. Minimum 2 source observations required - patterns need evidence
2. Don't just restate a single fact as a pattern
3. Confidence based on evidence count: 2=low, 3-4=medium, 5+=high
4. Look for HOW things change over time, not just static facts
5. Include source_ids - always link back to evidence
6. Empty or missing source_ids will be rejected
7. When you are finished, do not output a summary of what you did - output only the token DONE
```

### 6.3 User prompt & assembly

Same `run()` assembly as deduction. `InductionSpecialist.build_user_prompt(observed, hints, peer_card)` — note `peer_card` is ignored (`_ = peer_card`); `peer_card_context` is **not** included.

- With `hints`:
  ```
  {target_observee_context}Explore and find patterns. These areas may be worth investigating:

  - {hint}

  But follow the evidence - if you find patterns elsewhere, pursue those.

  Start with `get_recent_observations`.
  ```
- Without hints:
  ```
  {target_observee_context}Explore the observation space and identify patterns.

  Remember: patterns need 2+ sources. Look for tendencies, preferences, and behavioral regularities.

  Go.
  ```

### 6.4 Expected output
No response_model. Ends with `DONE`. Inductive observations each carry `content`, `source_ids` (≥2), `sources` (≥2), `pattern_type`, `confidence`.

### 6.5 `honcho_llm_call` invocation params

Same as deduction, except: `tools=INDUCTION_SPECIALIST_TOOLS` (always, peer_card-independent), `get_max_iterations()=10`, `get_max_tokens()=8192`.

`INDUCTION_SPECIALIST_TOOLS` = `get_recent_observations`, `search_memory`, `search_messages`, `create_observations_inductive`.

### 6.6 Default model + transport
`DREAM.INDUCTION_MODEL_CONFIG` → `gpt-5.4-mini` / `openai`.

### 6.7 Scoring-relevant notes
- Agentic multi-call. Correct = new inductive observations each backed by ≥2 distinct source IDs, appropriate `pattern_type` and `confidence` (2=low, 3-4=medium, 5+=high), no single-fact restatements. `create_observations_inductive` force-sets `level="inductive"`.

---

## Role 7 — `dream_card_refresh` (CardRefreshSpecialist)

**Purpose:** Agentic, card-only maintenance specialist (for the `card_refresh` dream type). Only rewrites the peer card from observations; cannot create/delete observations. Two modes: `refresh` (default) and `rebuild`.

### 7.2 Exact system prompt — `CardRefreshSpecialist.build_system_prompt(observed, peer_card_enabled=True)`

`{rebuild_section}` is empty unless `rebuild=True`, in which case it is:
```
## REBUILD MODE

The existing peer card is deliberately NOT shown to you: it may contain entries whose supporting observations have since been removed. Build the card solely from the observations you find in the collection right now. Do not carry over or guess at prior card content — if an identity marker is not supported by a current observation, it does not go on the card.
```

Full prompt:
```
You are a peer-card maintenance agent for the target observee.

## YOUR JOB

Refresh the peer card and nothing else. You cannot create or delete observations — you have no tools for that. Your only write operation is `update_peer_card`.

## PROCESS

1. Survey the observation space: start with `get_recent_observations`, then use `search_memory` for targeted follow-ups (names, roles, locations, standing instructions).
2. Extract stable identity markers supported by the observations you found.
3. Call `update_peer_card` once with the complete deduplicated list.

Keep it short — a handful of tool calls at most.{rebuild_section}
{PEER_CARD_SYSTEM_SECTION}
```
(`PEER_CARD_SYSTEM_SECTION` verbatim at Role 5, section 5.2b. `peer_card_enabled` is unused — the peer-card section is always included.)

### 7.3 User prompt & assembly

`CardRefreshSpecialist` is instantiated per run with `rebuild: bool`. `inject_peer_card = not rebuild`:
- `rebuild=True`: current card is **not** fetched/injected.
- `rebuild=False` (`refresh`): current card fetched and injected.

`peer_card_update_instruction = "Update this with `update_peer_card`. See the PEER CARD section in the system prompt for the allowed entry kinds and rules."`

`build_user_prompt(observed, hints, peer_card)` (hints always ignored):
- **rebuild**:
  ```
  {target_observee_context}Rebuild the peer card from scratch.

  The previous card is not shown and must not be assumed: reconstruct the card solely from observations currently in the collection. Start with `get_recent_observations`, verify with `search_memory` where needed, then call `update_peer_card` with the complete list.

  Go.
  ```
- **refresh**:
  ```
  {target_observee_context}{peer_card_context}Refresh the peer card.

  Review recent observations with `get_recent_observations` (and `search_memory` for targeted checks), then call `update_peer_card` with the complete deduplicated list if there is anything to add, correct, or migrate. If the card is already accurate and complete, finish without updating it.

  Go.
  ```

### 7.4 Expected output
No response_model. The single intended write is one `update_peer_card` call. Peer card is a `list[str]` of `{PREFIX}: {marker}` entries.

### 7.5 `honcho_llm_call` invocation params

Same pattern; `tools=CARD_REFRESH_SPECIALIST_TOOLS`, `get_max_tokens()=8192`, `get_max_iterations()=min(MAX_ITERATIONS_CEILING=6, settings.DREAM.MAX_TOOL_ITERATIONS)` = `min(6, 20)` = **6** under defaults.

`CARD_REFRESH_SPECIALIST_TOOLS` = `get_recent_observations`, `search_memory`, `update_peer_card`.

### 7.6 Default model + transport
Reuses `DREAM.DEDUCTION_MODEL_CONFIG` → `gpt-5.4-mini` / `openai`.

### 7.7 Scoring-relevant notes
- Agentic multi-call, but tightly constrained: max 6 tool iterations, must NOT call any create/delete observation tool (none are present in its toolset), and must either update the card once with a complete deduplicated prefixed list, or — in refresh mode — correctly conclude the card is already accurate and finish without updating.

---

## How to invoke each role standalone for benchmarking

The deriver, summaries, and dreamer specialists normally run behind the async queue (`deriver/`) + Postgres/vector DB. For A/B benchmarking you can reproduce each role as a **single LLM call** (or a tool-loop) without the full server, by constructing the prompt/messages below and calling any OpenAI-compatible client with the listed model/temperature/max_tokens. Everything for prompt assembly only needs the strings above plus your own fixture data.

| Role | Standalone construction | Agentic? |
|---|---|---|
| `deriver` | Build `formatted_messages` = `"\n".join(f"{ts:%Y-%m-%d %H:%M:%S} {peer}: {content}")`; build prompt via `minimal_deriver_prompt(peer_id=<observed>, messages=formatted_messages, custom_instructions=None)`; single call with `response_model=PromptRepresentation`, `json_mode=True`, `max_tokens=2500` (or configured `max_output_tokens`), `max_input_tokens=25000`. | single-shot |
| `summary_short` | `formatted_messages` = `"\n".join(f"{peer}: {content}")`; `previous_summary_text` = previous short summary or the exact placeholder; `output_words=int(min(input_tokens,1000)*0.75)`; prompt via `short_summary_prompt`; single call, `max_tokens=1000`, no response_model. | single-shot |
| `summary_long` | Same, but `output_words=int(4000*0.75)=3000`; prompt via `long_summary_prompt`; `max_tokens=4000`, no response_model. Window = last 60 messages. | single-shot |
| `dialectic` | Build `messages=[{"role":"system","content":agent_system_prompt(...)}, {"role":"user","content": prefetched_query_text}]`; tool-loop with `tools=DIALECTIC_TOOLS` (default reasoning), `max_tool_iterations=5` ("low"), `tool_choice="auto"`, `max_tokens=8192`, `max_input_tokens=100_000`. Requires implementing the 7 tools and the observation/message store behind them to be faithful. | **agentic** |
| `dream_deduction` | `messages=[{"role":"system","content":deduction_system_prompt}, {"role":"user","content":deduction_user_prompt}]`; tool-loop with `tools=DEDUCTION_SPECIALIST_TOOLS`, `max_tool_iterations=12`, `max_tokens=8192`, `tool_choice=None`. | **agentic** |
| `dream_induction` | `messages=[{system},{user}]` (induction prompts); tool-loop with `tools=INDUCTION_SPECIALIST_TOOLS`, `max_tool_iterations=10`, `max_tokens=8192`, `tool_choice=None`. | **agentic** |
| `dream_card_refresh` | `messages=[{system},{user}]` (card_refresh prompts); tool-loop with `tools=CARD_REFRESH_SPECIALIST_TOOLS`, `max_tool_iterations=6`, `max_tokens=8192`, `tool_choice=None`. `rebuild` toggles the REBUILD MODE section + card injection. | **agentic** |

**Key invocation facts for every role:**
- All 7 default to `model="gpt-5.4-mini", transport="openai"`. When tools are used, `honcho_llm_call` runs `execute_tool_loop` (up to `max_tool_iterations`); a tool loop cannot stream unless `stream_final_only=True`.
- Retry: default `enable_retry=True`, `retry_attempts=3`, exponential backoff `wait_exponential(multiplier=1, min=4, max=10)`; fallback config (if any) is used on the final attempt; temperature of `0.0` is bumped to `0.2` only on retry attempts.
- The `abstractmethod`-driven default temperature is `None` (provider default) for all roles unless an operator sets `temperature` in the model config; no role sets a literal temperature in-source.
- The deriver, summaries output is parsed by the harness from JSON (`explicit`) or plain text; the agentic roles require the tool loop to be faithful.

---

## Ambiguities / flags

1. **[FLAG] DONE-protocol loose end:** The deduction/induction/card_refresh system prompts instruct "output only the token DONE," but there is **no explicit `stop_sequence`** registered for `DONE` in the source we verified, and these specialists do **not** include a `finish_consolidation` tool (only the omni dreamer does). Loop termination therefore relies on the tool-loop's `max_tool_iterations` cap and on the model choosing to emit a final text answer. A harness replicating this should treat "model stops emitting tool-call messages" (rather than observing a strict `DONE`) as completion, and can optionally inspect the final content for the literal token `DONE`.
2. **[FLAG] Deriver `max_tokens`:** `deriver/deriver.py` computes `max_tokens = base_model_config.max_output_tokens or settings.LLM.DEFAULT_MAX_TOKENS` (2500). Since the shipped `DERIVER.MODEL_CONFIG` leaves `max_output_tokens` unset, 2500 is the effective default, but an operator-set value wins.
3. **[FLAG] Prefetch & session history are DB/downstream-dependent:** The dialectic's prefetched-observations block and session-history injection depend on a live memory store and are optional enrichments. For a pure contract test you can omit them (user message = `Query: {query}`) or inject fixture data using the documented text formats.
4. **[FLAG] Create-observations validation:** `create_observations_deductive`/`inductive` force `level`, and honcho rejects `explicit` from any tool call made when there is **no** `current_messages` context (dreamer/dialectic), and rejects derived levels from the deriver. A harness replaying saved tool arguments doesn't need this, but a harness that *executes* the tools should enforce it.
5. **[FLAG] `get_reasoning_chain` removal:** Under a session allowlist the dialectic tool list drops `get_reasoning_chain` at selection time (`_select_tools`); this is a runtime safeguard, not a prompt change.
6. **[FLAG] Summary fallback path:** `_create_summary` has a text-fallback path (used when the LLM call fails) that stores a non-LLM summary and sets `is_fallback=True`. This is not a role prompt and should not be part of a faithful reproduction.
7. **[FLAG] Summary message window vs prompt:** The 20/60-message window (MESSAGES_PER_SHORT/LONG_SUMMARY) is applied in `summarize_if_needed`/`_create_and_save_summary`; the prompt function itself only receives the already-formatted `formatted_messages` + `previous_summary_text`. The harness must decide/seed which messages constitute a window.
