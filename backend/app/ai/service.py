"""Explain/investigate orchestration with a single deadline and immutable evidence.

AIService returns a contract-shaped dict for independent tests. Public module
functions wrap it in B's AIResponse, imported only at the integration boundary.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from typing import TYPE_CHECKING, Any

from .context import SEMANTIC_LIMITATION, build_context, fallback, result_limitations, unique
from .prompts import system_prompt
from .provider import AIConfig, ConfigError, OpenAIProvider, ProviderError
from .tools import RequestError, ToolError, execute_tool, require_run, to_data, tool_schemas, validate_arguments
from .validation import ValidationError, response_schema, validate_response

if TYPE_CHECKING:
    from backend.app.contracts import AIResponse, ExplainRequest, InvestigateRequest
    from backend.app.store import ResultStore

MAX_CONTEXT_BYTES = 128_000
MAX_QUESTION_LENGTH = 4_000


def _request(request: Any, mode: str, store: Any) -> dict:
    try:
        data = to_data(request)
    except ToolError as exc:
        raise RequestError("invalid_request", 422, "Некорректный AI-запрос.", getattr(store, "run_id", None)) from exc
    fields = {"run_id", "target"} | ({"question"} if mode == "investigate" else set())
    target = data.get("target")
    if (set(data) != fields or not isinstance(target, dict) or set(target) != {"kind", "id"}
            or target["kind"] not in ("node", "cluster")
            or not isinstance(target["id"], str) or not target["id"].strip()):
        raise RequestError("invalid_request", 422, "Нужны run_id и точный target узла или кластера.", getattr(store, "run_id", None))
    require_run(store, data["run_id"])
    if mode == "investigate" and (not isinstance(data["question"], str)
            or not data["question"].strip() or len(data["question"]) > MAX_QUESTION_LENGTH):
        raise RequestError("invalid_request", 422, "Вопрос должен содержать от 1 до 4000 символов.", store.run_id)
    return data


def _encode(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ToolError("context_limit", "Контекст превышает допустимый размер.")
    return encoded


class AIService:
    """Inject config/provider for tests; request-local history is never shared."""

    def __init__(self, config: AIConfig | None = None, provider: Any = None):
        self.config = config
        self.provider = provider

    async def explain(self, request: Any, store: Any) -> dict:
        return await self._run("explain", request, store)

    async def investigate(self, request: Any, store: Any) -> dict:
        return await self._run("investigate", request, store)

    async def _run(self, mode: str, request: Any, store: Any) -> dict:
        started = asyncio.get_running_loop().time()
        data = _request(request, mode, store)
        run_id, target = data["run_id"], data["target"]
        config_error = None
        try:
            config = self.config or AIConfig.from_env()
        except ConfigError:
            config = AIConfig()
            config_error = "invalid_configuration"
        deadline = started + config.timeout_seconds
        context = None
        checks = []
        limits = []
        try:
            async with asyncio.timeout_at(deadline):
                name = "get_node" if target["kind"] == "node" else "get_cluster"
                args = {"gid" if name == "get_node" else "cluster_id": target["id"]}
                try:
                    record = await execute_tool(name, args, store, run_id)
                except ToolError as exc:
                    if exc.code == "target_not_found":
                        raise RequestError("not_found", 404, "Выбранный объект отсутствует в текущем запуске.", run_id) from exc
                    raise
                context = build_context(target, record)
                unavailable = config_error or config.unavailable_reason
                if unavailable:
                    return fallback(run_id, context, checks, unavailable, limits)
                provider = self.provider or OpenAIProvider(config)
                history = [
                    {"role": "system", "content": system_prompt(mode)},
                    {"role": "user", "content": _encode({
                        "contract_version": "1.0", "run_id": run_id, "target": target,
                        "question": data.get("question"), "card": context.record,
                        "evidence": context.evidence, "limitations": context.limitations,
                    })},
                ]
                evidence = deepcopy(context.evidence)
                call_ids = set()
                while True:
                    require_run(store, run_id)
                    _encode(history)  # Bound the entire request, including previous function outputs.
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError
                    enabled = mode == "investigate" and len(checks) < config.max_tool_calls
                    reply = await provider.respond(input=deepcopy(history), schema=response_schema(run_id),
                                                   tools=tool_schemas() if enabled else [], timeout=remaining)
                    require_run(store, run_id)
                    if reply.calls:
                        if mode == "explain" or reply.message is not None:
                            raise ToolError("unexpected_tool_call", "Ответ не соответствует режиму объяснения.")
                        history.extend(deepcopy(reply.output_items))
                        for call in reply.calls:
                            if len(checks) >= config.max_tool_calls:
                                limits.append("Достигнут лимит локальных проверок; дополнительные функции не выполнялись.")
                                return fallback(run_id, context, checks, "tool_budget_exhausted", limits)
                            if not isinstance(call.call_id, str) or not call.call_id or call.call_id in call_ids:
                                raise ToolError("invalid_call_id", "Некорректный идентификатор вызова функции.")
                            call_ids.add(call.call_id)
                            args = validate_arguments(call.name, call.arguments)
                            result = await execute_tool(call.name, args, store, run_id)
                            evidence_id = f"check:{len(checks) + 1}"
                            check = {"tool": call.name, "args": args, "result": result, "evidence_id": evidence_id}
                            checks.append(check)
                            evidence[evidence_id] = deepcopy(result)
                            limits.extend(result_limitations(call.name, result))
                            history.append({"type": "function_call_output", "call_id": call.call_id,
                                            "output": _encode({"contract_version": "1.0", "run_id": run_id,
                                                               "evidence_id": evidence_id, "result": result})})
                        if len(checks) >= config.max_tool_calls:
                            limits.append("Достигнут лимит локальных проверок; ответ использует только уже полученные данные.")
                        # One final call with tools disabled is allowed after the third execution.
                        continue
                    fragment = validate_response(reply.message, evidence)
                    require_run(store, run_id)
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError
                    return {"contract_version": "1.0", "run_id": run_id, "status": "ok",
                            "summary": fragment["summary"], "claims": fragment["claims"],
                            "limitations": unique(context.limitations + limits + fragment["limitations"] + [SEMANTIC_LIMITATION]),
                            "checks": deepcopy(checks), "fallback_reason": None}
        except RequestError:
            raise
        except TimeoutError:
            reason = "timeout"
            limits.append("Общий срок запроса истёк; незавершённая проверка не считается выполненной.")
        except (ProviderError, ValidationError, ToolError) as exc:
            reason = exc.code
        except Exception:
            # No raw provider error, key, prompt or question is sent to clients/logs.
            reason = "ai_unavailable"
        require_run(store, run_id)
        return fallback(run_id, context, checks, reason, limits)


async def explain(request: ExplainRequest, store: ResultStore) -> AIResponse:
    """B's route calls this function once backend.app.contracts is available."""
    from backend.app.contracts import AIResponse

    return AIResponse.model_validate(await AIService().explain(request, store))


async def investigate(request: InvestigateRequest, store: ResultStore) -> AIResponse:
    """B owns HTTP error translation and the canonical Pydantic models."""
    from backend.app.contracts import AIResponse

    return AIResponse.model_validate(await AIService().investigate(request, store))
