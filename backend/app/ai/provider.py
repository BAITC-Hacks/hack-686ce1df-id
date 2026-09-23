"""Small stateless OpenAI Responses adapter with no import-time dependencies.

Only the service decides whether a reply is grounded in the supplied evidence.
This adapter checks the transport envelope and JSON syntax, never executes tools,
and never includes upstream text or credentials in public exception messages.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Mapping


class ConfigError(ValueError):
    """Invalid local configuration; the offending value is intentionally omitted."""

    code = "invalid_config"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True)
class AIConfig:
    provider: str = "openai"
    model: str = ""
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 30.0
    max_tool_calls: int = 3

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) for value in (self.provider, self.model, self.api_key)):
            raise ConfigError()
        for name in ("provider", "model", "api_key"):
            value = getattr(self, name).strip()
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ConfigError()
            object.__setattr__(self, name, value.lower() if name == "provider" else value)
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 30
            or not math.isfinite(self.timeout_seconds)
            or isinstance(self.max_tool_calls, bool)
            or not isinstance(self.max_tool_calls, int)
            or not 0 <= self.max_tool_calls <= 3
        ):
            raise ConfigError()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AIConfig:
        env = os.environ if environ is None else environ
        try:
            timeout = float(env.get("AI_TIMEOUT_SECONDS", "30"))
            max_calls = int(env.get("AI_MAX_TOOL_CALLS", "3"))
        except (TypeError, ValueError, OverflowError):
            raise ConfigError() from None
        return cls(
            provider=env.get("AI_PROVIDER", "openai"),
            model=env.get("AI_MODEL", ""),
            api_key=env.get("OPENAI_API_KEY", ""),
            timeout_seconds=timeout,
            max_tool_calls=max_calls,
        )

    @property
    def unavailable_reason(self) -> str | None:
        if self.provider != "openai":
            return "unsupported_provider"
        if not self.api_key:
            return "missing_api_key"
        if not self.model:
            return "missing_model"
        return None


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict | str


@dataclass
class ProviderReply:
    message: dict | None = None
    calls: list[ToolCall] = field(default_factory=list)
    # Private continuation state, including encrypted reasoning. Never add this
    # field to AIResponse or its checks/trace; only replay it to the provider.
    output_items: list[dict] = field(default_factory=list, repr=False)


class ProviderError(Exception):
    """A safe machine reason, without raw API errors or request details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _reject_constant(_value: str) -> None:
    raise ValueError("non_finite_number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non_finite_number")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _json_object(value: str | bytes) -> dict:
    try:
        parsed = json.loads(
            value,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_unique_object,
        )
    except (ValueError, TypeError, RecursionError):
        raise ProviderError("provider_invalid_response") from None
    if not isinstance(parsed, dict):
        raise ProviderError("provider_invalid_response")
    return parsed


def _parse_reply(payload: dict) -> ProviderReply:
    status = payload.get("status")
    if not isinstance(status, str):
        raise ProviderError("provider_invalid_response")
    if status == "incomplete":
        raise ProviderError("provider_incomplete")
    if payload.get("error") is not None or status in {"failed", "cancelled"}:
        raise ProviderError("provider_error")
    if status != "completed":
        raise ProviderError("provider_invalid_response")
    output = payload.get("output")
    if not isinstance(output, list):
        raise ProviderError("provider_invalid_response")

    calls: list[ToolCall] = []
    text_parts: list[str] = []
    call_ids: set[str] = set()
    for item in output:
        if not isinstance(item, dict):
            raise ProviderError("provider_invalid_response")
        item_type = item.get("type")
        if item_type == "reasoning":
            continue
        if item.get("status", "completed") != "completed":
            raise ProviderError("provider_incomplete")
        if item_type == "function_call":
            call_id, name, arguments = (item.get(key) for key in ("call_id", "name", "arguments"))
            if (
                not isinstance(call_id, str)
                or not call_id.strip()
                or call_id in call_ids
                or not isinstance(name, str)
                or not name.strip()
                or not isinstance(arguments, str)
            ):
                raise ProviderError("provider_invalid_response")
            call_ids.add(call_id)
            calls.append(ToolCall(call_id=call_id, name=name, arguments=_json_object(arguments)))
        elif item_type == "message":
            content = item.get("content")
            if item.get("role") != "assistant" or not isinstance(content, list):
                raise ProviderError("provider_invalid_response")
            for part in content:
                if not isinstance(part, dict):
                    raise ProviderError("provider_invalid_response")
                if part.get("type") == "refusal":
                    raise ProviderError("provider_refusal")
                if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                    raise ProviderError("provider_invalid_response")
                text_parts.append(part["text"])
        else:
            # No built-in tools are enabled; unexpected output cannot be used as
            # a substitute for a completed, grounded response.
            raise ProviderError("provider_invalid_response")

    if calls and text_parts or len(text_parts) > 1:
        raise ProviderError("provider_invalid_response")
    if calls:
        return ProviderReply(calls=calls, output_items=output)
    if not text_parts or not text_parts[0].strip():
        raise ProviderError("provider_empty")
    return ProviderReply(message=_json_object(text_parts[0]), output_items=output)


class OpenAIProvider:
    """Single-attempt HTTP adapter. The service owns the overall request budget.

    ``transport`` accepts an httpx async transport (MockTransport in tests).
    httpx is imported only for a configured request, keeping local fallback
    usable before the optional provider dependency has been installed.
    """

    def __init__(self, config: AIConfig, transport: Any = None) -> None:
        self.config = config
        self._transport = transport

    async def respond(
        self,
        *,
        input: list[dict],
        schema: dict,
        tools: list[dict],
        timeout: float,
    ) -> ProviderReply:
        if reason := self.config.unavailable_reason:
            raise ProviderError(reason)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ProviderError("provider_timeout")
        deadline = min(timeout, self.config.timeout_seconds)
        try:
            import httpx
        except ImportError:
            raise ProviderError("missing_dependency") from None

        strict_tools = []
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                raise ProviderError("provider_request_error")
            strict_tools.append({**tool, "strict": True})
        body = {
            "model": self.config.model,
            "input": input,
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "money_graph_ai_response",
                    "strict": True,
                    "schema": schema,
                }
            },
            "tools": strict_tools,
            "tool_choice": "auto" if strict_tools else "none",
            "max_output_tokens": 4096,
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(deadline),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await asyncio.wait_for(
                    client.post(
                        "https://api.openai.com/v1/responses",
                        headers={"Authorization": f"Bearer {self.config.api_key}"},
                        json=body,
                    ),
                    timeout=deadline,
                )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            raise ProviderError("provider_timeout") from None
        except httpx.RequestError:
            raise ProviderError("provider_unavailable") from None
        except (ValueError, TypeError, UnicodeError):
            raise ProviderError("provider_request_error") from None

        if response.status_code in {401, 403}:
            raise ProviderError("provider_auth_error")
        if response.status_code == 429:
            raise ProviderError("provider_rate_limited")
        if response.status_code >= 500:
            raise ProviderError("provider_unavailable")
        if not 200 <= response.status_code < 300:
            raise ProviderError("provider_request_error")
        reply = _parse_reply(_json_object(response.content))
        if reply.calls and not strict_tools:
            raise ProviderError("provider_invalid_response")
        return reply
