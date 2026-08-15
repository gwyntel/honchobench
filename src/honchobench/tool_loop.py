"""The agentic tool loop.

Given a canonical (openai-style) message list, tool schemas, and a ModelClient,
repeatedly call the model, execute tool calls against an InMemoryStore, append
results, and terminate when the model stops emitting tool calls (optionally
inspecting for a literal DONE token). Enforces max_tool_iterations per
ROLE_SPEC.md.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

log = logging.getLogger("honchobench.tool_loop")


async def run_tool_loop(
    client,
    messages: list[dict],
    tools: list[dict],
    store,
    *,
    model: str,
    max_tool_iterations: int,
    max_tokens: int = 8192,
    tool_choice: Optional[str] = None,
    call_kwargs: Optional[dict] = None,
) -> dict:
    """Run the agentic loop.

    `messages` should start as [system, user]. The loop appends assistant+tool
    messages in canonical openai format. Returns the final assistant content
    (the "final text" the model emitted) plus diagnostics.
    """
    call_kwargs = call_kwargs or {}
    working = list(messages)
    tool_calls_made = 0
    done_token_seen = False

    for iteration in range(max_tool_iterations):
        resp = await client.call(
            model=model,
            messages=working,
            max_tokens=max_tokens,
            tools=tools if tools else None,
            tool_choice=tool_choice,
            **call_kwargs,
        )
        content = resp.get("content", "") or ""
        tool_calls = resp.get("tool_calls", []) or []

        if not tool_calls:
            # Model produced a final text answer -> completion.
            if content.strip().upper() == "DONE":
                done_token_seen = True
            return {
                "final_content": content,
                "tool_calls_made": tool_calls_made,
                "iterations": iteration + 1,
                "done_token": done_token_seen,
                "messages": working,
            }

        # Append the assistant message with tool calls to the history.
        assistant_msg = {
            "role": "assistant",
            "content": content or None,
            "tool_calls": [],
        }
        for tc in tool_calls:
            assistant_msg["tool_calls"].append({
                "id": f"call_{tool_calls_made}_{len(assistant_msg['tool_calls'])}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("arguments") or {}),
                },
            })
        working.append(assistant_msg)

        # Execute each tool call and append a tool message.
        for tc in tool_calls:
            name = tc["name"]
            args = tc.get("arguments") or {}
            tool_calls_made += 1
            tool_call_id = assistant_msg["tool_calls"][
                [x["function"]["name"] for x in assistant_msg["tool_calls"]].index(name)
            ]["id"]
            try:
                result = await store.execute_tool(name, args)
            except Exception as exc:  # noqa: BLE001
                result = {"error": str(exc)}
            log.debug("tool %s args=%s -> %r", name, args, result)
            working.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(result, default=str),
            })

        # Loop continues; model may respond only with text next, or more tools.
        tools = tools  # keep same tools

    # Hit max iterations without a final text.
    last = working[-1] if working else {}
    return {
        "final_content": "",
        "tool_calls_made": tool_calls_made,
        "iterations": max_tool_iterations,
        "done_token": done_token_seen,
        "messages": working,
        "truncated": True,
    }
