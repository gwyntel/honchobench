"""Thin async client wrapper for the three honcho transports.

- 'openai'     -> AsyncOpenAI, honours OPENAI_BASE_URL / OPENAI_API_KEY, with a
                  PLEXUS_BASE_URL + PLEXUS_API_KEY convention for the self-hosted
                  Plexus OpenAI-compatible gateway.
- 'anthropic'  -> AsyncAnthropic, ANTHROPIC_API_KEY
- 'gemini'     -> google.genai, GEMINI_API_KEY

Never hardcodes keys/base URLs; reads from os.environ (optionally seeded by a
--env-file). A single `call()` method serves both single-shot and tool-loop use.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

log = logging.getLogger("honchobench.clients")


def default_openai_base_url() -> str:
    """Resolve the OpenAI-compatible base URL.

    Priority: explicit OPENAI_BASE_URL, then CUSTOM_BASE_URL (Plexus legacy
    convention), then PLEXUS_BASE_URL (used together with PLEXUS_API_KEY for the
    self-hosted gateway), else the standard https://api.openai.com/v1.

    This is a pure fallback chain: the same key that satisfies
    :func:`default_openai_api_key` alone is enough — a base URL is only required
    when the target is not api.openai.com.
    """
    return (
        os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("CUSTOM_BASE_URL")
        or os.environ.get("PLEXUS_BASE_URL")
        or "https://api.openai.com/v1"
    )


def default_openai_api_key() -> Optional[str]:
    return os.environ.get("OPENAI_API_KEY") or os.environ.get("PLEXUS_API_KEY")


class ModelClient:
    """A single call() against any of the three transports."""

    def __init__(self, transport: str = "openai", model: Optional[str] = None):
        self.transport = transport
        self.model = model
        self._openai = None
        self._anthropic = None
        self._genai = None
        self._genai_client = None

    # -- lazy transport construction -------------------------------------
    def _get_openai(self):
        if self._openai is None:
            from openai import AsyncOpenAI

            key = default_openai_api_key()
            if not key:
                raise RuntimeError(
                    "No OPENAI_API_KEY or PLEXUS_API_KEY in environment to talk to the "
                    f"openai transport ({default_openai_base_url()}). "
                    "Set one (optionally via --env-file); see .env.example."
                )
            self._openai = AsyncOpenAI(
                api_key=key,
                base_url=default_openai_base_url(),
            )
        return self._openai

    def _get_anthropic(self):
        if self._anthropic is None:
            from anthropic import AsyncAnthropic

            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY not set for anthropic transport.")
            self._anthropic = AsyncAnthropic(api_key=key)
        return self._anthropic

    def _get_genai(self):
        if self._genai_client is None:
            from google import genai

            key = os.environ.get("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GEMINI_API_KEY not set for gemini transport.")
            self._genai = genai
            self._genai_client = genai.Client(api_key=key)
        return self._genai, self._genai_client

    # -- single call -------------------------------------------------------
    async def call(
        self,
        *,
        model: Optional[str] = None,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: Optional[float] = None,
        json_mode: bool = False,
        response_format: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Any = None,
        stop: Optional[list[str]] = None,
    ) -> dict:
        """Send a request and return a normalized response dict.

        Returns:
            {
              "content": str,                 # final accumulated text
              "tool_calls": [{"name","arguments"(dict)}, ...] or [],
              "raw": <transport-specific message>,   # for tool_loop fidelity
              "finish_reason": str,
            }
        """
        model = model or self.model
        if self.transport == "openai":
            return await self._call_openai(
                model, messages, max_tokens, temperature, json_mode, response_format,
                tools, tool_choice, stop,
            )
        if self.transport == "anthropic":
            return await self._call_anthropic(
                model, messages, max_tokens, temperature, json_mode, response_format,
                tools, tool_choice, stop,
            )
        if self.transport == "gemini":
            return await self._call_gemini(
                model, messages, max_tokens, temperature, json_mode, response_format,
                tools, tool_choice, stop,
            )
        raise ValueError(f"Unknown transport: {self.transport}")

    async def _call_openai(self, model, messages, max_tokens, temperature, json_mode,
                           response_format, tools, tool_choice, stop):
        client = self._get_openai()
        kwargs: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if tools:
            # OpenAI SDK wants each tool wrapped as {"type":"function","function":{...
            # name,description,parameters}}. honcho schemas use `input_schema`.
            wrapped = []
            for t in tools:
                wrapped.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", t.get("parameters", {"type": "object", "properties": {}})),
                    },
                })
            kwargs["tools"] = wrapped
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if stop:
            kwargs["stop"] = stop
        fmt = response_format
        if fmt is None and json_mode:
            fmt = {"type": "json_object"}
        if fmt is not None:
            kwargs["response_format"] = fmt

        resp = await client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        tool_calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"name": tc.function.name, "arguments": args})
        return {
            "content": msg.content or "",
            "tool_calls": tool_calls,
            "raw": msg,
            "finish_reason": resp.choices[0].finish_reason,
            "model": resp.model,
        }

    @staticmethod
    def _canonical_to_anthropic(messages):
        """Convert canonical openai-style messages (incl. tool_calls/tool role)
        into anthropic messages format."""
        system = None
        msgs = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                system = (system + "\n\n" if system else "") + m.get("content", "")
            elif role == "assistant" and m.get("tool_calls"):
                content_blocks = []
                if m.get("content"):
                    content_blocks.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", f"call_{len(msgs)}"),
                        "name": fn.get("name", ""),
                        "input": args,
                    })
                msgs.append({"role": "assistant", "content": content_blocks})
            elif role == "tool":
                msgs.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id", ""),
                            "content": m.get("content", ""),
                        }
                    ],
                })
            else:
                msgs.append({"role": role, "content": m.get("content", "")})
        return system, msgs

    @staticmethod
    def _canonical_to_gemini(messages, genai):
        """Convert canonical messages to genai contents + system."""
        system = None
        contents = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                system = (system + "\n\n" if system else "") + m.get("content", "")
            elif role == "assistant" and m.get("tool_calls"):
                parts = []
                if m.get("content"):
                    parts.append(genai.types.Part(text=m["content"]))
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    parts.append(genai.types.Part(
                        function_call=genai.types.FunctionCall(name=fn.get("name", ""), args=args)
                    ))
                contents.append(genai.types.Content(role="model", parts=parts))
            elif role == "tool":
                contents.append(genai.types.Content(
                    role="user",
                    parts=[genai.types.Part(
                        function_response=genai.types.FunctionResponse(
                            name=f"fn_{m.get('tool_call_id','')}",
                            response={"result": json.loads(m.get("content", "{}"))},
                        )
                    )],
                ))
            elif role == "user":
                contents.append(genai.types.Content(role="user", parts=[m.get("content", "")]))
            else:
                contents.append(genai.types.Content(role="model", parts=[m.get("content", "")]))
        return system, contents

    async def _call_anthropic(self, model, messages, max_tokens, temperature, json_mode,
                              response_format, tools, tool_choice, stop):
        client = self._get_anthropic()
        system, msgs = self._canonical_to_anthropic(messages)
        kwargs: dict = {
            "model": model,
            "messages": msgs,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        if stop:
            kwargs["stop_sequences"] = stop
        if tools:
            # anthropic wants {"name","description","input_schema"}
            anthropic_tools = []
            for t in tools:
                anthropic_tools.append({
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema", t.get("parameters", {"type": "object", "properties": {}})),
                })
            kwargs["tools"] = anthropic_tools

        resp = await client.messages.create(**kwargs)
        content_parts = []
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"name": block.name, "arguments": block.input or {}})
        return {
            "content": "".join(content_parts),
            "tool_calls": tool_calls,
            "raw": resp,
            "finish_reason": resp.stop_reason,
            "model": resp.model,
        }

    async def _call_gemini(self, model, messages, max_tokens, temperature, json_mode,
                           response_format, tools, tool_choice, stop):
        genai, client = self._get_genai()
        system, contents = self._canonical_to_gemini(messages, genai)

        config_kwargs = dict(
            max_output_tokens=max_tokens,
        )
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if tools:
            declarations = []
            for t in tools:
                declarations.append({
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", t.get("parameters", {"type": "object", "properties": {}})),
                })
            config_kwargs["tools"] = [genai.types.Tool(function_declarations=declarations)]
        if json_mode or response_format:
            config_kwargs["response_mime_type"] = "application/json"

        config = genai.types.GenerateContentConfig(**config_kwargs)
        if system:
            config.system_instruction = system

        resp = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        text = resp.text or ""
        tool_calls = []
        if resp.candidates:
            for part in resp.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    tool_calls.append({"name": fc.name, "arguments": fc.args or {}})
        return {
            "content": text,
            "tool_calls": tool_calls,
            "raw": resp,
            "finish_reason": (resp.candidates[0].finish_reason if resp.candidates else "unknown"),
            "model": model,
        }

    async def aclose(self) -> None:
        pass
