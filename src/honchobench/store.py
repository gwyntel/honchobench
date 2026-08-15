"""In-memory conversation store + tool executors for the agentic honcho roles.

Operates entirely over fixture messages (substring + simple keyword relevance —
no external embedding API required). Exposes the tool executors the dialectic and
dreamer specialists call, plus the verbatim JSON tool schemas from ROLE_SPEC.md.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Verbatim JSON tool schemas from ROLE_SPEC.md section 4.8
# ---------------------------------------------------------------------------
SEARCH_MEMORY_SCHEMA = {
    "name": "search_memory",
    "description": "Search for observations in memory using semantic similarity. Use this to find relevant information about the peer when you need to recall specific details.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query text"},
            "top_k": {"type": "integer", "description": "(Optional) number of results to return (default: 20, max: 40)", "default": 20},
        },
        "required": ["query"],
    },
}

SEARCH_MESSAGES_SCHEMA = {
    "name": "search_messages",
    "description": "Search for messages using semantic similarity and retrieve conversation snippets. Returns matching messages with surrounding context (2 messages before and after). Nearby matches within the same session are merged into a single snippet to avoid repetition.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query text to find relevant messages"},
            "limit": {"type": "integer", "description": "Maximum number of matching messages to return (default: 10, max: 20)", "default": 10},
        },
        "required": ["query"],
    },
}

GET_OBSERVATION_CONTEXT_SCHEMA = {
    "name": "get_observation_context",
    "description": "Retrieve messages for given message IDs along with surrounding context. Takes message IDs (from an observation's message_ids field) and retrieves those messages plus the messages immediately before and after each one to provide conversation context.",
    "input_schema": {
        "type": "object",
        "properties": {
            "message_ids": {"type": "array", "items": {"type": "string"}, "description": "List of message IDs to retrieve (get these from observation.message_ids in search results)"},
        },
        "required": ["message_ids"],
    },
}

GREP_MESSAGES_SCHEMA = {
    "name": "grep_messages",
    "description": "Search for messages containing specific text (case-insensitive). Unlike semantic search, this finds EXACT text matches. Use for finding specific names, dates, phrases, or keywords mentioned in conversations. Returns messages with surrounding context.",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to search for (case-insensitive substring match)"},
            "limit": {"type": "integer", "description": "Maximum messages to return (default: 10, max: 30)", "default": 10},
            "context_window": {"type": "integer", "description": "Number of messages before/after each match to include (default: 2)", "default": 2},
        },
        "required": ["text"],
    },
}

GET_MESSAGES_BY_DATE_RANGE_SCHEMA = {
    "name": "get_messages_by_date_range",
    "description": "Get messages from a specific date range. Use this to find what was discussed during a particular time period, or to compare information before vs after a date. Essential for knowledge update questions.",
    "input_schema": {
        "type": "object",
        "properties": {
            "after_date": {"type": "string", "description": "Start date (ISO format, e.g., '2024-01-15'). Returns messages after this date."},
            "before_date": {"type": "string", "description": "End date (ISO format). Returns messages before this date."},
            "limit": {"type": "integer", "description": "Maximum messages to return (default: 20, max: 50)", "default": 20},
            "order": {"type": "string", "enum": ["asc", "desc"], "description": "Sort order: 'asc' for oldest first, 'desc' for newest first (default: desc)", "default": "desc"},
        },
    },
}

SEARCH_MESSAGES_TEMPORAL_SCHEMA = {
    "name": "search_messages_temporal",
    "description": "Semantic search for messages with optional date filtering. Combines the power of semantic search with time constraints. Use after_date to find recent mentions of a topic, or before_date to find what was said about something before a certain point. Best for knowledge update questions where you need to find the MOST RECENT discussion of a topic.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Semantic search query"},
            "after_date": {"type": "string", "description": "Only return messages after this date (ISO format, e.g., '2024-01-15')"},
            "before_date": {"type": "string", "description": "Only return messages before this date (ISO format)"},
            "limit": {"type": "integer", "description": "Maximum messages to return (default: 10, max: 20)", "default": 10},
            "context_window": {"type": "integer", "description": "Messages before/after each match (default: 2)", "default": 2},
        },
        "required": ["query"],
    },
}

GET_REASONING_CHAIN_SCHEMA = {
    "name": "get_reasoning_chain",
    "description": "Get the reasoning chain for an observation - traverse the tree to find premises (for deductive) or sources (for inductive), and/or find conclusions derived from this observation. Use this to understand how an observation was derived or what conclusions depend on it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "observation_id": {"type": "string", "description": "The document ID of the observation to get the reasoning chain for"},
            "direction": {"type": "string", "enum": ["premises", "conclusions", "both"], "description": "'premises' to get what this observation is based on, 'conclusions' to get what depends on it, 'both' for full context", "default": "both"},
        },
        "required": ["observation_id"],
    },
}

GET_RECENT_OBSERVATIONS_SCHEMA = {
    "name": "get_recent_observations",
    "description": "Get the most recent observations about the peer. Useful for understanding what's been learned recently.",
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Maximum number of observations to return (default: 10)", "default": 10},
            "session_only": {"type": "boolean", "description": "If true, only return observations from the current session (default: false)", "default": False},
        },
    },
}

CREATE_OBSERVATIONS_DEDUCTIVE_SCHEMA = {
    "name": "create_observations_deductive",
    "description": "Create new deductive observations discovered while answering the query. Every observation must include non-empty source_ids and premise text. Use this only for novel deductions grounded in existing observations.",
    "input_schema": {
        "type": "object",
        "properties": {
            "observations": {
                "type": "array",
                "description": "List of new deductive observations to create",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The deductive conclusion as a self-contained statement"},
                        "source_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Required non-empty list of source observation IDs supporting the deduction"},
                        "premises": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Required human-readable premise text matching the source observations"},
                    },
                    "required": ["content", "source_ids", "premises"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["observations"],
    },
}

CREATE_OBSERVATIONS_INDUCTIVE_SCHEMA = {
    "name": "create_observations_inductive",
    "description": "Create new inductive observations discovered while answering the query. Every observation must include source_ids, source text, pattern_type, and confidence. Use this only for patterns supported by multiple observations.",
    "input_schema": {
        "type": "object",
        "properties": {
            "observations": {
                "type": "array",
                "description": "List of new inductive observations to create",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The inductive pattern or generalization as a self-contained statement"},
                        "source_ids": {"type": "array", "items": {"type": "string"}, "minItems": 2, "description": "Required list of at least two source observation IDs supporting the pattern"},
                        "sources": {"type": "array", "items": {"type": "string"}, "minItems": 2, "description": "Required human-readable evidence text matching the source observations"},
                        "pattern_type": {"type": "string", "enum": ["preference", "behavior", "personality", "tendency", "correlation"], "description": "Required pattern category"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"], "description": "Required confidence level based on evidence count"},
                    },
                    "required": ["content", "source_ids", "sources", "pattern_type", "confidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["observations"],
    },
}

DELETE_OBSERVATIONS_SCHEMA = {
    "name": "delete_observations",
    "description": "Delete observations by their IDs. Use the exact ID shown in [id:xxx] format from search results. Example: if observation shows '[id:abc123XYZ]', pass 'abc123XYZ' to delete it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "observation_ids": {"type": "array", "items": {"type": "string"}, "description": "List of observation IDs to delete (use the exact ID from [id:xxx] in search results)"},
        },
        "required": ["observation_ids"],
    },
}

UPDATE_PEER_CARD_SCHEMA = {
    "name": "update_peer_card",
    "description": "Update the peer card with stable identity markers about the observed peer. An identity marker distinguishes the peer from others of its kind and persists across interactions. The peer may be any entity with identity that changes over time (human, agent, codebase, team, organization) — do not assume the peer is human. Each entry must start with one of four prefixes: `IDENTITY:` (canonical name, kind, aliases, IDs), `ATTRIBUTE:` (stable durable property, including explicitly stated standing preferences), `RELATIONSHIP:` (durable link to another entity), or `INSTRUCTION:` (standing rule of engagement the peer has explicitly stated). Do not write `TRAIT:` or behavioral `PREFERENCE:` entries, one-off observations, transient state, inferred facts not directly supported by evidence, evidence bundles / `e.g.` clauses, or entries about co-occurring peers. Entries without an allowed prefix or that exceed the per-entry length cap are rejected.",
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "array",
                "description": "Complete deduplicated peer card list (max 40 entries). Each entry must start with one of the allowed prefixes (`IDENTITY: `, `ATTRIBUTE: `, `RELATIONSHIP: `, `INSTRUCTION: `) followed by one concise identity marker. Entries without an allowed prefix are rejected.",
                "items": {"type": "string"},
            },
        },
        "required": ["content"],
    },
}

# Tool lists per role.
DIALECTIC_TOOLS = [
    SEARCH_MEMORY_SCHEMA,
    SEARCH_MESSAGES_SCHEMA,
    GET_OBSERVATION_CONTEXT_SCHEMA,
    GREP_MESSAGES_SCHEMA,
    GET_MESSAGES_BY_DATE_RANGE_SCHEMA,
    SEARCH_MESSAGES_TEMPORAL_SCHEMA,
    GET_REASONING_CHAIN_SCHEMA,
]

DEDUCTION_SPECIALIST_TOOLS = [
    GET_RECENT_OBSERVATIONS_SCHEMA,
    SEARCH_MEMORY_SCHEMA,
    SEARCH_MESSAGES_SCHEMA,
    CREATE_OBSERVATIONS_DEDUCTIVE_SCHEMA,
    DELETE_OBSERVATIONS_SCHEMA,
]

INDUCTION_SPECIALIST_TOOLS = [
    GET_RECENT_OBSERVATIONS_SCHEMA,
    SEARCH_MEMORY_SCHEMA,
    SEARCH_MESSAGES_SCHEMA,
    CREATE_OBSERVATIONS_INDUCTIVE_SCHEMA,
]

CARD_REFRESH_SPECIALIST_TOOLS = [
    GET_RECENT_OBSERVATIONS_SCHEMA,
    SEARCH_MEMORY_SCHEMA,
    UPDATE_PEER_CARD_SCHEMA,
]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class InMemoryStore:
    """Holds a conversation's messages + a synthetic observation space.

    Observations are synthesized from the explicit (target-peer-spoken) messages
    when the store is constructed, giving search_memory / get_recent_observations
    something to answer against without an external DB.
    """

    def __init__(self, conversation: dict):
        self.conversation_id = conversation.get("conversation_id", "")
        self.target_peer = conversation.get("target_peer", "")
        self.messages: list[dict] = list(conversation.get("messages", []))
        for i, m in enumerate(self.messages):
            m.setdefault("id", f"msg_{i}")
        # Synthetic observations derived from target-peer messages.
        self.observations: list[dict] = []  # {id, content, level, message_ids, source_ids}
        self.peer_card: list[str] = []
        self._seed_observations()

    # -- helpers -----------------------------------------------------------
    def _message_texts(self):
        return [(m.get("id"), m.get("sender", ""), m.get("text", "")) for m in self.messages]

    def _seed_observations(self):
        """Create one explicit observation per target-peer message (simple seed)."""
        for m in self.messages:
            if m.get("sender") != self.target_peer:
                continue
            text = m.get("text", "").strip()
            if not text:
                continue
            oid = f"obs_{m['id']}"
            self.observations.append({
                "id": oid,
                "content": text[:500],
                "level": "explicit",
                "message_ids": [m["id"]],
                "source_ids": [],
            })

    @staticmethod
    def _tokenize(text: str):
        return set(re.findall(r"[a-z0-9]+", text.lower()))

    def _context_window(self, midx: int, window: int) -> list[int]:
        lo = max(0, midx - window)
        hi = min(len(self.messages), midx + window + 1)
        return list(range(lo, hi))

    # -- executors ---------------------------------------------------------
    async def search_memory(self, query: str = "", top_k: int = 20, **_):
        """Basic keyword-overlap relevance over the observation space."""
        q = self._tokenize(query)
        scored = []
        for obs in self.observations:
            toks = self._tokenize(obs["content"])
            inter = len(q & toks)
            # also treat each target-peer message as relevance
            scored.append((inter, obs))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [s for s, _ in scored[: int(top_k)] if s > 0]
        if not top and scored:
            top = [o for _, o in scored[: max(1, int(top_k))]]
        return {
            "observations": [
                {
                    "id": o["id"],
                    "content": o["content"],
                    "level": o["level"],
                    "message_ids": o["message_ids"],
                    "source_ids": o["source_ids"],
                }
                for o in top
            ]
        }

    async def search_messages(self, query: str = "", limit: int = 10, **_):
        q = self._tokenize(query)
        scored = []
        idx = {m["id"]: i for i, m in enumerate(self.messages)}
        for i, m in enumerate(self.messages):
            inter = len(q & self._tokenize(m.get("text", "")))
            scored.append((inter, i))
        scored.sort(key=lambda x: x[0], reverse=True)
        picks = [i for s, i in scored if s > 0][: int(limit)]
        if not picks and scored:
            picks = [i for _, i in scored[: max(1, int(limit))]]
        snippets = []
        seen = set()
        for midx in picks:
            for ci in self._context_window(midx, 2):
                if ci in seen:
                    continue
                seen.add(ci)
                m = self.messages[ci]
                snippets.append(f"[{m['id']}] {m.get('sender','')}: {m.get('text','')}")
        return {"messages": snippets}

    async def grep_messages(self, text: str = "", limit: int = 10, context_window: int = 2, **_):
        pat = text.lower()
        limit = int(limit)
        context_window = int(context_window)
        matches = []
        for i, m in enumerate(self.messages):
            if pat in m.get("text", "").lower():
                matches.append(i)
        matches = matches[:limit]
        result = []
        seen = set()
        for midx in matches:
            for ci in self._context_window(midx, context_window):
                if ci in seen:
                    continue
                seen.add(ci)
                m = self.messages[ci]
                result.append(f"[{m['id']}] {m.get('sender','')}: {m.get('text','')}")
        return {"messages": result, "num_matches": len(matches)}

    async def get_messages_by_date_range(self, after_date=None, before_date=None, limit=20, order="desc", **_):
        # Fixture messages carry no authoritative timestamps; treat index as time.
        msgs = list(self.messages)
        if order == "asc":
            msgs = msgs[::-1]
        out = msgs[: int(limit)]
        return {
            "messages": [f"[{m['id']}] {m.get('sender','')}: {m.get('text','')}" for m in out],
            "note": "fixture messages have no stored timestamps; returning the most/least recent N by sequence order",
        }

    async def search_messages_temporal(self, query: str = "", after_date=None, before_date=None,
                                       limit: int = 10, context_window: int = 2, **_):
        return await self.search_messages(query=query or "", limit=limit)

    async def get_observation_context(self, message_ids=None, **_):
        ids = set(message_ids or [])
        if not ids:
            return {"messages": []}
        idx = {m["id"]: i for i, m in enumerate(self.messages)}
        result = []
        seen = set()
        for mid in ids:
            if mid not in idx:
                continue
            for ci in self._context_window(idx[mid], 2):
                m = self.messages[ci]
                if m["id"] in seen:
                    continue
                seen.add(m["id"])
                result.append(f"[{m['id']}] {m.get('sender','')}: {m.get('text','')}")
        return {"messages": result}

    async def get_recent_observations(self, limit: int = 10, session_only: bool = False, **_):
        obs = self.observations[-int(limit):] if self.observations else []
        return {
            "observations": [
                {
                    "id": o["id"],
                    "content": o["content"],
                    "level": o["level"],
                    "message_ids": o["message_ids"],
                    "source_ids": o["source_ids"],
                }
                for o in obs
            ]
        }

    async def get_reasoning_chain(self, observation_id: str = "", direction: str = "both", **_):
        obs = {o["id"]: o for o in self.observations}
        o = obs.get(observation_id)
        if not o:
            return {"observation_id": observation_id, "error": "not found"}
        premises = [self.observations[i]["id"] for i in range(len(self.observations))
                    if self.observations[i]["id"] in o.get("source_ids", [])]
        return {
            "observation_id": o["id"],
            "content": o["content"],
            "level": o["level"],
            "premises": premises,
            "conclusions": [],
        }

    async def create_observations_deductive(self, observations=None, **_):
        created = []
        for obs in observations or []:
            content = (obs.get("content") or "").strip()
            source_ids = obs.get("source_ids") or []
            premises = obs.get("premises") or []
            if not content or not source_ids or not premises:
                continue  # rejected
            self.observations.append({
                "id": f"obs_{len(self.observations)}",
                "content": content,
                "level": "deductive",
                "message_ids": [],
                "source_ids": list(source_ids),
            })
            created.append(obs)
        return {"created": len(created), "observations": [o for o in created]}

    async def create_observations_inductive(self, observations=None, **_):
        created = []
        for obs in observations or []:
            content = (obs.get("content") or "").strip()
            source_ids = obs.get("source_ids") or []
            sources = obs.get("sources") or []
            pattern_type = obs.get("pattern_type")
            confidence = obs.get("confidence")
            if not content or len(source_ids) < 2 or len(sources) < 2 or not pattern_type or not confidence:
                continue
            self.observations.append({
                "id": f"obs_{len(self.observations)}",
                "content": content,
                "level": "inductive",
                "message_ids": [],
                "source_ids": list(source_ids),
                "pattern_type": pattern_type,
                "confidence": confidence,
            })
            created.append(obs)
        return {"created": len(created), "observations": [o for o in created]}

    async def delete_observations(self, observation_ids=None, **_):
        ids = set(observation_ids or [])
        before = len(self.observations)
        self.observations = [o for o in self.observations if o["id"] not in ids]
        return {"deleted": before - len(self.observations), "observation_ids": list(ids)}

    async def update_peer_card(self, content=None, **_):
        entries = []
        rejected = []
        for e in content or []:
            if not isinstance(e, str):
                rejected.append(e)
                continue
            if re.match(r"^(IDENTITY|ATTRIBUTE|RELATIONSHIP|INSTRUCTION)[:]\s", e.strip()) and len(e) <= 200:
                entries.append(e.strip())
            else:
                rejected.append(e)
        # dedupe (case-insensitive), cap 40
        seen = set()
        dedup = []
        for e in entries:
            k = e.lower()
            if k not in seen and len(dedup) < 40:
                seen.add(k)
                dedup.append(e)
        self.peer_card = dedup
        return {"updated": len(dedup), "entries": dedup, "rejected": rejected}

    # -- dispatch -----------------------------------------------------------
    async def execute_tool(self, name: str, args: dict) -> Any:
        handler = getattr(self, name, None)
        if handler is None:
            return {"error": f"unknown tool: {name}"}
        return await handler(**args)


TOOL_SCHEMAS_BY_NAME = {t["name"]: t for t in (
    SEARCH_MEMORY_SCHEMA, SEARCH_MESSAGES_SCHEMA, GET_OBSERVATION_CONTEXT_SCHEMA,
    GREP_MESSAGES_SCHEMA, GET_MESSAGES_BY_DATE_RANGE_SCHEMA, SEARCH_MESSAGES_TEMPORAL_SCHEMA,
    GET_REASONING_CHAIN_SCHEMA, GET_RECENT_OBSERVATIONS_SCHEMA,
    CREATE_OBSERVATIONS_DEDUCTIVE_SCHEMA, CREATE_OBSERVATIONS_INDUCTIVE_SCHEMA,
    DELETE_OBSERVATIONS_SCHEMA, UPDATE_PEER_CARD_SCHEMA,
)}
