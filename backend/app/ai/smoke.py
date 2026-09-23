"""Explicit, opt-in OpenAI probes using only labelled synthetic data.

python -m backend.app.ai.smoke --list-models  # key required; no inference
python -m backend.app.ai.smoke --smoke       # key + selected AI_MODEL; paid calls
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import time

from .prompts import system_prompt
from .provider import AIConfig, ConfigError, OpenAIProvider, ProviderError
from .tools import ToolError, tool_schemas, validate_arguments
from .validation import ValidationError, response_schema, validate_response


async def list_models(config: AIConfig) -> list[str]:
    if config.provider != "openai":
        raise ProviderError("unsupported_provider")
    if not config.api_key:
        raise ProviderError("missing_api_key")
    try:
        import httpx
    except ImportError:
        raise ProviderError("missing_dependency") from None
    try:
        async with asyncio.timeout(config.timeout_seconds):
            async with httpx.AsyncClient(timeout=config.timeout_seconds, trust_env=False) as client:
                response = await client.get("https://api.openai.com/v1/models",
                                            headers={"Authorization": f"Bearer {config.api_key}"})
                response.raise_for_status()
                data = response.json()["data"]
                if not isinstance(data, list) or any(not isinstance(item.get("id"), str) for item in data):
                    raise ValueError
                return sorted(item["id"] for item in data)
    except (TimeoutError, httpx.TimeoutException):
        raise ProviderError("provider_timeout") from None
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        raise ProviderError("model_listing_failed") from None


async def probe(config: AIConfig) -> dict:
    """Verify structured text AND a function round trip; do not use real data."""
    provider = OpenAIProvider(config)
    started = time.monotonic()
    deadline = started + config.timeout_seconds
    run_id = "synthetic-ai-probe"
    evidence = {"probe:metrics": {"out_amount_kzt": "100.00"}}
    base = {"role": "system", "content": system_prompt("explain")}
    async with asyncio.timeout(config.timeout_seconds):
        reply = await provider.respond(
            input=[base, {"role": "user", "content": json.dumps({
                "synthetic": True, "run_id": run_id, "evidence": evidence,
                "question": "Объясни исходящую сумму в синтетическом примере."}, ensure_ascii=False)}],
            schema=response_schema(run_id), tools=[], timeout=deadline - time.monotonic())
        validate_response(reply.message, evidence)
        history = [
            {"role": "system", "content": system_prompt("investigate")},
            {"role": "user", "content": "Это тест подключения на искусственных данных. "
             "Вызови get_node ровно для gid synthetic-ai-probe, затем изложи результат с evidence_id ответа функции."},
        ]
        reply = await provider.respond(input=history, schema=response_schema(run_id),
                                       tools=[item for item in tool_schemas() if item["name"] == "get_node"],
                                       timeout=deadline - time.monotonic())
        if len(reply.calls) != 1 or reply.calls[0].name != "get_node":
            raise ProviderError("probe_tool_not_called")
        call = reply.calls[0]
        if validate_arguments(call.name, call.arguments) != {"gid": "synthetic-ai-probe"}:
            raise ProviderError("probe_wrong_target")
        result = {"gid": "synthetic-ai-probe", "metrics": {"out_amount_kzt": "100.00"},
                  "quality": {"outbound_censored": True, "inbound_incomplete": None},
                  "role_explanation": "Искусственные данные только для проверки подключения."}
        history.extend(reply.output_items)
        history.append({"type": "function_call_output", "call_id": call.call_id,
                        "output": json.dumps({"synthetic": True, "run_id": run_id,
                                              "evidence_id": "check:1", "result": result}, ensure_ascii=False)})
        reply = await provider.respond(input=history, schema=response_schema(run_id), tools=[],
                                       timeout=deadline - time.monotonic())
        validate_response(reply.message, {"check:1": result})
    return {"status": "ok", "provider": "openai", "model": config.model,
            "synthetic_data_only": True, "structured_output": True, "function_round_trip": True,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "checked_at": datetime.now(timezone.utc).isoformat()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list-models", action="store_true")
    group.add_argument("--smoke", action="store_true")
    options = parser.parse_args(argv)
    try:
        config = AIConfig.from_env()
        result = {"models": asyncio.run(list_models(config))} if options.list_models else asyncio.run(probe(config))
    except (ConfigError, ProviderError, ToolError, ValidationError) as exc:
        print(json.dumps({"status": "error", "reason": exc.code}, ensure_ascii=False))
        return 1
    except TimeoutError:
        print(json.dumps({"status": "error", "reason": "timeout"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
