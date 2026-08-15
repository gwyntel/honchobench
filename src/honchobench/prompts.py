"""Verbatim prompt constructors for the 7 honcho roles.

All prompt text below is copied EXACTLY from ROLE_SPEC.md (which quotes the
vendored honcho source). Do not paraphrase or "improve" these strings — they are
the benchmark contract.
"""

import inspect


def cleandoc(text: str) -> str:
    """Replicate inspect.cleandoc behavior used by honcho's prompt builders."""
    return inspect.cleandoc(text)


# ---------------------------------------------------------------------------
# Role 1 — deriver
# ---------------------------------------------------------------------------
def minimal_deriver_prompt(peer_id: str, messages: str, custom_instructions=None) -> str:
    """Build the exact minimal_deriver_prompt from deriver/prompts.py.

    `messages` is the already-formatted timestamped message block, i.e.
    formatter: "\\n".join("{ts} {peer}: {content}").
    `custom_instructions` optional; when non-empty a CUSTOM INSTRUCTIONS section
    is inserted.
    """
    custom_section = ""
    if custom_instructions:
        custom_section_src = (
            "CUSTOM INSTRUCTIONS:\n"
            "These instructions apply to the target peer identified below.\n"
            + str(custom_instructions).strip()
        )
        custom_section = cleandoc(custom_section_src) + "\n\n"

    template = cleandoc(
        """\
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

        {custom_section}Target peer:
        {peer_id}

        Messages to analyze:
        <messages>
        {formatted_messages}
        </messages>"""
    )
    return template.format(
        custom_section=custom_section,
        peer_id=peer_id,
        formatted_messages=messages,
    )


# ---------------------------------------------------------------------------
# Roles 2 & 3 — summaries
# ---------------------------------------------------------------------------
SHORT_SUMMARY_BODY = """\
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
"""

LONG_SUMMARY_BODY = """\
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
"""

NO_PREVIOUS_SUMMARY = (
    "There is no previous summary -- the messages are the beginning of the conversation."
)

SHORT_SUMMARY_MAX_TOKENS = 1000
LONG_SUMMARY_MAX_TOKENS = 4000
MESSAGES_PER_SHORT_SUMMARY = 20
MESSAGES_PER_LONG_SUMMARY = 60


def short_summary_prompt(formatted_messages: str, output_words: int, previous_summary_text: str) -> str:
    """Exact short_summary_prompt from utils/summarizer.py."""
    return inspect.cleandoc(
        SHORT_SUMMARY_BODY.format(
            previous_summary_text=previous_summary_text,
            formatted_messages=formatted_messages,
            output_words=str(output_words),
        )
    )


def long_summary_prompt(formatted_messages: str, output_words: int, previous_summary_text: str) -> str:
    """Exact long_summary_prompt from utils/summarizer.py."""
    return inspect.cleandoc(
        LONG_SUMMARY_BODY.format(
            previous_summary_text=previous_summary_text,
            formatted_messages=formatted_messages,
            output_words=str(output_words),
        )
    )


# ---------------------------------------------------------------------------
# Role 4 — dialectic
# ---------------------------------------------------------------------------
def dialectic_perspective_section(observer, observed, observer_peer_card, observed_peer_card) -> str:
    if observer == observed:
        lines = [f"You are answering queries about '{observed}'."]
        if observer_peer_card:
            lines.append("")
            lines.append("Known biographical information about {0}:".format(observed))
            lines.append("<peer_card>")
            lines.append("\n".join(observer_peer_card))
            lines.append("</peer_card>")
    else:
        lines = [
            "You are answering queries from the perspective of {0}'s understanding of {1}.".format(
                observer, observed
            ),
            "This is a directional query - {0} wants to know about {1}.".format(observer, observed),
        ]
        if observer_peer_card:
            lines.append("")
            lines.append("Known biographical information about {0} (the one asking):".format(observer))
            lines.append("<observer_peer_card>")
            lines.append("\n".join(observer_peer_card))
            lines.append("</observer_peer_card>")
        if observed_peer_card:
            lines.append("Known biographical information about {0} (the subject):".format(observed))
            lines.append("<observed_peer_card>")
            lines.append("\n".join(observed_peer_card))
            lines.append("</observed_peer_card>")
    return "\n".join(lines)


PEER_CARD_EXPLANATION = (
    "Peer cards are **constructed summaries** - they are synthesized from the same observations "
    "stored in memory. This means:\n"
    "- Information in a peer card originates from observations you can also find via `search_memory`\n"
    "- The peer card is a convenience summary, not a separate source of truth"
)


def dialectic_agent_system_prompt(observer, observed, observer_peer_card=None, observed_peer_card=None) -> str:
    """Exact agent_system_prompt from dialectic/prompts.py."""
    perspective = dialectic_perspective_section(observer, observed, observer_peer_card, observed_peer_card)
    peer_explanation = ""
    if (observer_peer_card is not None and observer_peer_card) or (
        observed_peer_card is not None and observed_peer_card
    ):
        peer_explanation = PEER_CARD_EXPLANATION + "\n"

    skeleton = """\
You are a helpful and concise context synthesis agent that answers questions about users by gathering relevant information from a memory system.

Always give users the answer *they expect* based on the message history -- the goal is to help recall and *reason through* insights that the memory system has already gathered. You have many tools for gathering context. Search wisely.

{perspective}
{peer_explanation}## AVAILABLE TOOLS

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
"""
    return inspect.cleandoc(
        skeleton.format(perspective=perspective, peer_explanation=peer_explanation)
    )


def dialectic_user_prompt(query: str, prefetched_observations: str = "") -> str:
    """Build the dialectic user message. Omit prefetched block if empty (FLAG #3)."""
    user = f"Query: {query}"
    if prefetched_observations:
        user += (
            "\n\n## Relevant Observations (prefetched)\n"
            "The following observations were found to be semantically relevant to your query. "
            "Use these as primary context. You may still use tools to find additional information if needed.\n\n"
            + prefetched_observations
        )
    return user


# ---------------------------------------------------------------------------
# Roles 5, 6, 7 — dreamer specialists (deduction / induction / card_refresh)
# ---------------------------------------------------------------------------
PEER_CARD_SYSTEM_SECTION = """\
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
"""


def _target_observee_context(observed: str) -> str:
    return (
        f"Target observee:\n{observed}\n\n"
        "The target observee is the peer identified above. When created observations need to name this subject, "
        "use the exact observee id above, not the phrase \"the target observee\".\n\n"
    )


def _peer_card_context(peer_card) -> str:
    lines = ["## CURRENT PEER CARD", ""]
    for fact in peer_card or []:
        lines.append(f"- {fact}")
    lines.append("")
    lines.append(
        "Update this with `update_peer_card` only for stable identity markers. "
        "See the PEER CARD section in the system prompt for the allowed entry kinds and rules."
    )
    lines.append("If you update it, send the full deduplicated list and remove stale entries.")
    lines.append("")
    return "\n".join(lines)


# --- Role 5 — deduction ---
DEDUCTION_SYSTEM_PROMPT = """\
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
"""


def deduction_system_prompt(observed: str, peer_card_enabled: bool = True) -> str:
    peer_card_section = PEER_CARD_SYSTEM_SECTION if peer_card_enabled else ""
    return inspect.cleandoc(
        DEDUCTION_SYSTEM_PROMPT.format(peer_card_section=peer_card_section)
    )


def deduction_user_prompt(observed: str, hints=None, peer_card=None) -> str:
    ctx = _target_observee_context(observed)
    card_ctx = _peer_card_context(peer_card) if peer_card else ""
    if hints:
        hint_lines = "\n".join(f"- {h}" for h in hints[:5])
        body = (
            f"Start by exploring recent observations and messages. These topics may be worth investigating:\n\n"
            f"{hint_lines}\n\n"
            "But follow the evidence - if you find something more interesting, pursue that instead.\n\n"
            "Begin with `get_recent_observations` to see what's there."
        )
    else:
        body = (
            "Explore the observation space and create deductive observations.\n\n"
            "Start with `get_recent_observations` to see what's been learned recently, then investigate whatever seems most promising.\n\n"
            "Look for:\n"
            "1. Knowledge updates (same fact, different values over time)\n"
            "2. Logical implications that haven't been made explicit\n"
            "3. Contradictions that need flagging\n\n"
            "Go."
        )
    return ctx + card_ctx + body


# --- Role 6 — induction ---
INDUCTION_SYSTEM_PROMPT = """\
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
"""


def induction_system_prompt(observed: str, peer_card_enabled: bool = True) -> str:
    return inspect.cleandoc(INDUCTION_SYSTEM_PROMPT)


def induction_user_prompt(observed: str, hints=None, peer_card=None) -> str:
    ctx = _target_observee_context(observed)
    if hints:
        hint_lines = "\n".join(f"- {h}" for h in hints[:5])
        body = (
            f"Explore and find patterns. These areas may be worth investigating:\n\n{hint_lines}\n\n"
            "But follow the evidence - if you find patterns elsewhere, pursue those.\n\n"
            "Start with `get_recent_observations`."
        )
    else:
        body = (
            "Explore the observation space and identify patterns.\n\n"
            "Remember: patterns need 2+ sources. Look for tendencies, preferences, and behavioral regularities.\n\n"
            "Go."
        )
    return ctx + body


# --- Role 7 — card refresh ---
CARD_REFRESH_REBUILD_SECTION = """\
## REBUILD MODE

The existing peer card is deliberately NOT shown to you: it may contain entries whose supporting observations have since been removed. Build the card solely from the observations you find in the collection right now. Do not carry over or guess at prior card content — if an identity marker is not supported by a current observation, it does not go on the card.
"""


def card_refresh_system_prompt(observed: str, rebuild: bool = False) -> str:
    rebuild_section = CARD_REFRESH_REBUILD_SECTION if rebuild else ""
    prompt = (
        "You are a peer-card maintenance agent for the target observee.\n\n"
        "## YOUR JOB\n\n"
        "Refresh the peer card and nothing else. You cannot create or delete observations — you have no tools for that. "
        "Your only write operation is `update_peer_card`.\n\n"
        "## PROCESS\n\n"
        "1. Survey the observation space: start with `get_recent_observations`, then use `search_memory` for targeted follow-ups (names, roles, locations, standing instructions).\n"
        "2. Extract stable identity markers supported by the observations you found.\n"
        "3. Call `update_peer_card` once with the complete deduplicated list.\n\n"
        "Keep it short — a handful of tool calls at most."
        + rebuild_section
        + "\n"
        + PEER_CARD_SYSTEM_SECTION
    )
    return inspect.cleandoc(prompt)


def card_refresh_user_prompt(observed: str, hints=None, peer_card=None) -> str:
    """rebuild mode: peer_card not shown. refresh mode: show current card."""
    ctx = _target_observee_context(observed)
    card_ctx = _peer_card_context(peer_card) if peer_card else ""
    if peer_card:
        # refresh mode
        body = "Refresh the peer card."
    else:
        # rebuild mode
        body = (
            "Rebuild the peer card from scratch.\n\n"
            "The previous card is not shown and must not be assumed: reconstruct the card solely from observations "
            "currently in the collection. Start with `get_recent_observations`, verify with `search_memory` where needed, "
            "then call `update_peer_card` with the complete list.\n\n"
            "Go."
        )
    return ctx + card_ctx + body
