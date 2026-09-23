"""Provider tests use synthetic payloads and never contact the OpenAI API."""

import asyncio
import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

import httpx

from backend.app.ai.provider import (
    AIConfig,
    ConfigError,
    OpenAIProvider,
    ProviderError,
    ProviderReply,
)


SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}
TOOL = {
    "type": "function",
    "name": "get_node",
    "description": "Read one node from the current run.",
    "parameters": {
        "type": "object",
        "properties": {"gid": {"type": "string"}},
        "required": ["gid"],
        "additionalProperties": False,
    },
}


def message(text='{"summary":"Наблюдаемые данные."}'):
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def call(call_id="call_1", arguments='{"gid":"0007"}'):
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": "get_node",
        "arguments": arguments,
        "status": "completed",
    }


class AIConfigTests(unittest.TestCase):
    def test_defaults_are_offline_without_key_or_model(self):
        with patch.dict(os.environ, {}, clear=True):
            config = AIConfig.from_env()
        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.timeout_seconds, 30)
        self.assertEqual(config.max_tool_calls, 3)
        self.assertEqual(config.unavailable_reason, "missing_api_key")

    def test_explicit_environment_and_secret_repr(self):
        env = {
            "AI_PROVIDER": " OpenAI ",
            "AI_MODEL": " model-under-test ",
            "OPENAI_API_KEY": " sk-synthetic-secret ",
            "AI_TIMEOUT_SECONDS": "12.5",
            "AI_MAX_TOOL_CALLS": "2",
        }
        with patch.dict(os.environ, env, clear=True):
            config = AIConfig.from_env()
        self.assertEqual(config.model, "model-under-test")
        self.assertEqual(config.api_key, "sk-synthetic-secret")
        self.assertEqual(config.timeout_seconds, 12.5)
        self.assertEqual(config.max_tool_calls, 2)
        self.assertIsNone(config.unavailable_reason)
        self.assertNotIn("sk-synthetic-secret", repr(config))

    def test_no_default_model_and_no_provider_switch(self):
        self.assertEqual(AIConfig(api_key="synthetic").unavailable_reason, "missing_model")
        self.assertEqual(
            AIConfig(provider="nvidia", model="example", api_key="synthetic").unavailable_reason,
            "unsupported_provider",
        )

    def test_invalid_environment_is_rejected_without_value_leak(self):
        for name, values in {
            "AI_TIMEOUT_SECONDS": ["", "secret-invalid", "nan", "inf", "-1", "0", "30.1"],
            "AI_MAX_TOOL_CALLS": ["", "secret-invalid", "1.5", "-1", "4"],
        }.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    with patch.dict(os.environ, {name: value}, clear=True):
                        with self.assertRaises(ConfigError) as error:
                            AIConfig.from_env()
                    self.assertNotIn("secret-invalid", str(error.exception))

    def test_direct_construction_cannot_bypass_limits(self):
        for kwargs in (
            {"timeout_seconds": float("nan")},
            {"timeout_seconds": 31},
            {"timeout_seconds": 10 ** 1000},
            {"timeout_seconds": True},
            {"max_tool_calls": 4},
            {"max_tool_calls": 1.5},
            {"max_tool_calls": True},
            {"api_key": "secret\r\nheader"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ConfigError):
                AIConfig(**kwargs)
        self.assertEqual(AIConfig(max_tool_calls=0).max_tool_calls, 0)

    def test_reply_lists_are_not_shared(self):
        first, second = ProviderReply(), ProviderReply()
        first.output_items.append({"type": "reasoning"})
        self.assertEqual(second.output_items, [])
        self.assertIsNot(first.calls, second.calls)


class OpenAIProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = AIConfig(model="model-under-test", api_key="sk-synthetic-secret")

    async def invoke(self, payload, *, tools=None, status=200, config=None):
        def handler(request):
            self.last_request = request
            if isinstance(payload, bytes):
                return httpx.Response(status, content=payload)
            return httpx.Response(status, json=payload)

        provider = OpenAIProvider(config or self.config, transport=httpx.MockTransport(handler))
        return await provider.respond(input=[{"role": "user", "content": "Synthetic."}],
                                      schema=SCHEMA, tools=tools or [], timeout=2)

    async def test_structured_request_and_fixed_endpoint(self):
        reply = await self.invoke({"status": "completed", "output": [message()]})
        self.assertEqual(reply.message, {"summary": "Наблюдаемые данные."})
        request = self.last_request
        body = json.loads(request.content)
        self.assertEqual(str(request.url), "https://api.openai.com/v1/responses")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer sk-synthetic-secret")
        self.assertFalse(body["store"])
        self.assertEqual(body["model"], "model-under-test")
        self.assertEqual(body["tool_choice"], "none")
        self.assertEqual(body["tools"], [])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertEqual(body["text"]["format"]["schema"], SCHEMA)
        self.assertIn("reasoning.encrypted_content", body["include"])
        self.assertLessEqual(request.extensions["timeout"]["read"], 2)

    async def test_calls_and_encrypted_reasoning_are_preserved_for_continuation(self):
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [],
                     "encrypted_content": "synthetic-encrypted-state"}
        output = [reasoning, call(), call("call_2")]
        tool = copy.deepcopy(TOOL)
        reply = await self.invoke({"status": "completed", "output": output}, tools=[tool])
        self.assertIsNone(reply.message)
        self.assertEqual(reply.output_items, output)
        self.assertEqual(reply.calls[0].arguments, {"gid": "0007"})
        self.assertEqual([item.call_id for item in reply.calls], ["call_1", "call_2"])
        body = json.loads(self.last_request.content)
        self.assertTrue(body["tools"][0]["strict"])
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(tool, TOOL)

    async def test_service_replays_function_output_and_private_reasoning(self):
        from backend.app.ai.service import AIService
        from tests.ai.fixtures import FakeStore, request

        output = [
            {"type": "reasoning", "id": "rs_1", "summary": [],
             "encrypted_content": "synthetic-private-reasoning"},
            {"type": "function_call", "call_id": "concentration_1",
             "name": "check_concentration", "arguments": '{"gid":"0007"}'},
        ]
        final_answer = {
            "summary": "Справка по наблюдаемой сети.",
            "claims": [{"text": "Доля крупнейшего получателя составляет 70%.",
                        "evidence_ids": ["check:1"]}],
            "limitations": [],
        }
        requests = []

        def handler(http_request):
            requests.append(json.loads(http_request.content))
            items = output if len(requests) == 1 else [message(json.dumps(final_answer))]
            return httpx.Response(200, json={"status": "completed", "output": items})

        store = FakeStore()
        provider = OpenAIProvider(self.config, transport=httpx.MockTransport(handler))
        result = await AIService(self.config, provider).investigate(
            request(question="Какова концентрация получателей?"), store
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1]["input"][2:4], output)
        tool_output = requests[1]["input"][4]
        self.assertEqual(tool_output["type"], "function_call_output")
        self.assertEqual(tool_output["call_id"], "concentration_1")
        evidence = json.loads(tool_output["output"])
        self.assertEqual(evidence["evidence_id"], "check:1")
        self.assertEqual(evidence["run_id"], store.run_id)
        self.assertEqual(evidence["result"]["top_receiver_share"], 0.7)
        self.assertEqual(result["claims"][0]["evidence_ids"], ["check:1"])
        self.assertEqual(result["checks"][0]["result"]["top_receiver_share"], 0.7)
        self.assertEqual(store.calls.count(("check_concentration", "0007")), 1)
        self.assertNotIn("synthetic-private-reasoning", json.dumps(result))
        self.assertTrue(all(item["store"] is False for item in requests))

    async def test_failure_payloads_are_normalized(self):
        cases = [
            ({"status": "incomplete", "output": [message()]}, "provider_incomplete"),
            ({"status": "failed", "error": {"message": "upstream-secret"}}, "provider_error"),
            ({"status": "completed", "output": []}, "provider_empty"),
            ({"status": "completed", "output": [{"type": "reasoning", "summary": []}]}, "provider_empty"),
            ({"status": "completed", "output": [call(), call()]}, "provider_invalid_response"),
            ({"status": "completed", "output": [message(), call()]}, "provider_invalid_response"),
            ({"status": "completed", "output": [message("not JSON upstream-secret")]}, "provider_invalid_response"),
            ({"status": "completed", "output": [message('{"value":NaN}')]}, "provider_invalid_response"),
            ({"status": "completed", "output": [message('{"value":1e999}')]}, "provider_invalid_response"),
            ({"status": "completed", "output": [message('{"value":1,"value":2}')]}, "provider_invalid_response"),
            ({"status": "completed", "output": [message("[]")]}, "provider_invalid_response"),
            ({"status": "completed", "output": [call(arguments='{"gid":NaN}')]}, "provider_invalid_response"),
            ({"status": "completed", "output": [call(arguments="[]")]}, "provider_invalid_response"),
            ({"status": "completed", "output": [{"type": "web_search_call"}]}, "provider_invalid_response"),
            ({"status": "completed", "output": [message(), message()]}, "provider_invalid_response"),
            ({"status": "completed", "output": [dict(call(), call_id="")]}, "provider_invalid_response"),
            ({"output": [message()]}, "provider_invalid_response"),
            ({"status": {}, "output": [message()]}, "provider_invalid_response"),
            ({"status": "completed", "output": None}, "provider_invalid_response"),
            (b'{"status":"completed","status":"incomplete"}', "provider_invalid_response"),
            (b'{"status": NaN}', "provider_invalid_response"),
            (b"upstream-secret", "provider_invalid_response"),
        ]
        for payload, code in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ProviderError) as error:
                    await self.invoke(payload, tools=[TOOL])
                self.assertEqual(error.exception.code, code)
                self.assertNotIn("upstream-secret", str(error.exception))
                self.assertNotIn(self.config.api_key, str(error.exception))

    async def test_refusal_is_explicit(self):
        refusal = dict(message(), content=[{"type": "refusal", "refusal": "upstream-secret"}])
        with self.assertRaises(ProviderError) as error:
            await self.invoke({"status": "completed", "output": [refusal]})
        self.assertEqual(error.exception.code, "provider_refusal")
        self.assertNotIn("upstream-secret", str(error.exception))

    async def test_calls_are_rejected_when_no_tools_were_offered(self):
        with self.assertRaises(ProviderError) as error:
            await self.invoke({"status": "completed", "output": [call()]})
        self.assertEqual(error.exception.code, "provider_invalid_response")

    async def test_http_errors_do_not_expose_body_or_retry(self):
        for status, code in [(401, "provider_auth_error"), (403, "provider_auth_error"),
                             (429, "provider_rate_limited"), (503, "provider_unavailable"),
                             (400, "provider_request_error"), (302, "provider_request_error")]:
            seen = []

            def handler(request):
                seen.append(request)
                return httpx.Response(status, text="upstream-secret", headers={"location": "https://example.com"})

            provider = OpenAIProvider(self.config, transport=httpx.MockTransport(handler))
            with self.subTest(status=status), self.assertRaises(ProviderError) as error:
                await provider.respond(input=[], schema=SCHEMA, tools=[], timeout=1)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(len(seen), 1)
            self.assertNotIn("upstream-secret", str(error.exception))

    async def test_timeout_and_connection_error_are_sanitized(self):
        for exception, code in [(httpx.ReadTimeout("upstream-secret"), "provider_timeout"),
                                (httpx.ConnectError("upstream-secret"), "provider_unavailable")]:
            def handler(request):
                raise exception

            provider = OpenAIProvider(self.config, transport=httpx.MockTransport(handler))
            with self.subTest(code=code), self.assertRaises(ProviderError) as error:
                await provider.respond(input=[], schema=SCHEMA, tools=[], timeout=1)
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("upstream-secret", str(error.exception))

    async def test_hanging_transport_is_cancellable(self):
        cancelled = asyncio.Event()

        async def handler(request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        provider = OpenAIProvider(self.config, transport=httpx.MockTransport(handler))
        with self.assertRaises(ProviderError) as error:
            await provider.respond(input=[], schema=SCHEMA, tools=[], timeout=0.01)
        self.assertEqual(error.exception.code, "provider_timeout")
        self.assertTrue(cancelled.is_set())

    async def test_outer_cancellation_propagates(self):
        started = asyncio.Event()

        async def handler(request):
            started.set()
            await asyncio.Event().wait()

        provider = OpenAIProvider(self.config, transport=httpx.MockTransport(handler))
        task = asyncio.create_task(provider.respond(input=[], schema=SCHEMA, tools=[], timeout=1))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_missing_configuration_or_dependency_causes_no_network(self):
        seen = []

        def handler(request):
            seen.append(request)
            raise AssertionError("No request should be sent")

        for config, code in [(AIConfig(), "missing_api_key"),
                             (AIConfig(api_key="synthetic"), "missing_model"),
                             (AIConfig(provider="nvidia"), "unsupported_provider")]:
            provider = OpenAIProvider(config, transport=httpx.MockTransport(handler))
            with self.subTest(code=code), self.assertRaises(ProviderError) as error:
                await provider.respond(input=[], schema=SCHEMA, tools=[], timeout=1)
            self.assertEqual(error.exception.code, code)
        self.assertEqual(seen, [])
        with patch.dict(sys.modules, {"httpx": None}):
            provider = OpenAIProvider(self.config)
            with self.assertRaises(ProviderError) as error:
                await provider.respond(input=[], schema=SCHEMA, tools=[], timeout=1)
        self.assertEqual(error.exception.code, "missing_dependency")

    async def test_expired_deadline_does_not_call_transport(self):
        def handler(request):
            raise AssertionError("No request should be sent")

        provider = OpenAIProvider(self.config, transport=httpx.MockTransport(handler))
        with self.assertRaises(ProviderError) as error:
            await provider.respond(input=[], schema=SCHEMA, tools=[], timeout=0)
        self.assertEqual(error.exception.code, "provider_timeout")


if __name__ == "__main__":
    unittest.main()
