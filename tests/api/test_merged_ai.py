"""Exercise D's public entry points against B's real models, Store and routes."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from backend.app.ai import service
from backend.app.ai.provider import AIConfig, ProviderReply, ToolCall
from backend.app.ai.service import AIService
from backend.app.ai.tools import ToolError, execute_tool
from backend.app.contracts import AIResponse

from conftest import RUN_ID


class ScriptedProvider:
    """An in-memory provider: this suite cannot issue an external AI request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    async def respond(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        return self.replies.pop(0)


def install_service(monkeypatch, provider, *, configured=True):
    config = AIConfig(model="synthetic-model", api_key="synthetic-key") if configured else AIConfig()
    engine = AIService(config, provider)
    # Keep the actual public service.explain/investigate boundary and its model
    # validation; replace only the factory's network/configuration dependencies.
    monkeypatch.setattr(service, "AIService", lambda: engine)


def request_body(kind="node", target="0007", question=None):
    result = {"run_id": RUN_ID, "target": {"kind": kind, "id": target}}
    if question is not None:
        result["question"] = question
    return result


def test_d_rejected_question_remains_an_http_validation_error(client, monkeypatch):
    provider = ScriptedProvider()
    install_service(monkeypatch, provider)

    response = client.post("/api/ai/investigate", json=request_body(question="x" * 4001))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.json()["run_id"] == RUN_ID
    assert provider.requests == []


@pytest.mark.parametrize("kind,target", [("node", "0007"), ("cluster", "cluster-main")])
def test_public_d_fallback_reads_real_store(client, monkeypatch, kind, target):
    provider = ScriptedProvider()
    install_service(monkeypatch, provider, configured=False)

    response = client.post("/api/ai/explain", json=request_body(kind, target))

    assert response.status_code == 200
    data = AIResponse.model_validate(response.json())
    assert data.run_id == RUN_ID
    assert data.status == "fallback"
    assert data.fallback_reason == "missing_api_key"
    assert data.claims and data.limitations
    assert provider.requests == []


def test_public_d_explain_accepts_b_models_and_grounded_response(client, monkeypatch):
    provider = ScriptedProvider(ProviderReply(message={
        "summary": "Справка по наблюдаемой сети.",
        "claims": [{"text": "Исходящая сумма составляет 100.00 KZT.",
                    "evidence_ids": ["node:0007:metrics"]}],
        "limitations": [],
    }))
    install_service(monkeypatch, provider)

    response = client.post("/api/ai/explain", json=request_body())

    assert response.status_code == 200
    data = AIResponse.model_validate(response.json())
    assert data.run_id == RUN_ID and data.status == "ok"
    assert data.claims[0].evidence_ids == ["node:0007:metrics"]
    assert data.checks == []
    assert len(provider.requests) == 1
    assert provider.requests[0]["tools"] == []


def test_public_d_investigate_keeps_real_neighbor_result(client, monkeypatch):
    provider = ScriptedProvider(
        ProviderReply(calls=[ToolCall("check-neighbors", "get_neighbors", {
            "gid": "0007", "radius": 1, "limit": 50,
        })]),
        ProviderReply(message={
            "summary": "Окружение прочитано из текущего запуска.",
            "claims": [{"text": "Проверены наблюдаемые соседи.", "evidence_ids": ["check:1"]}],
            "limitations": [],
        }),
    )
    install_service(monkeypatch, provider)

    response = client.post("/api/ai/investigate", json=request_body(question="Проверь соседей."))

    assert response.status_code == 200
    data = AIResponse.model_validate(response.json())
    assert data.status == "ok" and data.run_id == RUN_ID
    assert len(data.checks) == 1
    check = data.checks[0]
    assert check.tool == "get_neighbors" and check.evidence_id == "check:1"
    assert check.result == client.app.state.store.get_neighbors("0007").model_dump(mode="json")
    assert len(provider.requests) == 2


def test_native_d_timeout_keeps_completed_checks(client, monkeypatch):
    class SlowFinalProvider:
        requests = 0

        async def respond(self, **kwargs):
            self.requests += 1
            if self.requests == 1:
                return ProviderReply(calls=[ToolCall("check-node", "get_node", {"gid": "0007"})])
            await asyncio.sleep(1)
            raise AssertionError("The request deadline must cancel this response")

    provider = SlowFinalProvider()
    engine = AIService(AIConfig(model="synthetic-model", api_key="synthetic-key", timeout_seconds=0.05), provider)
    monkeypatch.setattr(service, "AIService", lambda: engine)
    client.app.state.settings = replace(client.app.state.settings, ai_timeout_seconds=0.05)
    assert client.app.state.ai_service is None  # Import the actual public D module.

    response = client.post("/api/ai/investigate", json=request_body(question="Проверь карточку."))

    assert response.status_code == 200
    data = AIResponse.model_validate(response.json())
    assert data.status == "fallback" and data.fallback_reason == "timeout"
    assert data.run_id == RUN_ID
    assert len(data.checks) == 1
    assert data.checks[0].tool == "get_node"
    assert data.checks[0].result == client.app.state.store.get_node("0007").model_dump(mode="json")
    assert provider.requests == 2


@pytest.mark.parametrize("tool,args", [
    ("get_node", {"gid": "absent"}),
    ("get_cluster", {"cluster_id": "absent"}),
    ("get_neighbors", {"gid": "absent"}),
    ("check_concentration", {"gid": "absent"}),
])
def test_missing_real_store_record_is_an_unknown_target(client, tool, args):
    with pytest.raises(ToolError) as caught:
        asyncio.run(execute_tool(tool, args, client.app.state.store, RUN_ID))

    assert caught.value.code == "target_not_found"
