"""Verify invalid requests cannot reach the injected AI implementation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conftest import RUN_ID, read_json
from test_http_contract import assert_envelope, assert_error


@pytest.fixture
def ai_spy(client, fixture_dir):
    response = read_json(fixture_dir / "ai_response.json")
    service = SimpleNamespace(explain=AsyncMock(return_value=response), investigate=AsyncMock(return_value=response))
    client.app.state.ai_service = service
    return service


def body_for(endpoint: str, *, run_id=RUN_ID, kind="node", target="0007"):
    body = {"run_id": run_id, "target": {"kind": kind, "id": target}}
    if endpoint == "investigate":
        body["question"] = "Объясни ограничения наблюдаемого графа."
    return body


@pytest.mark.parametrize("endpoint", ["explain", "investigate"])
def test_stale_run_is_rejected_before_service(client, ai_spy, endpoint):
    response = client.post(f"/api/ai/{endpoint}", json=body_for(endpoint, run_id="old-run"))
    assert_error(response, 409)
    ai_spy.explain.assert_not_awaited()
    ai_spy.investigate.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["explain", "investigate"])
@pytest.mark.parametrize("kind", ["node", "cluster"])
def test_unknown_ai_target_is_rejected_before_service(client, ai_spy, endpoint, kind):
    response = client.post(f"/api/ai/{endpoint}", json=body_for(endpoint, kind=kind, target="absent"))
    assert_error(response, 404)
    ai_spy.explain.assert_not_awaited()
    ai_spy.investigate.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["explain", "investigate"])
def test_wrong_target_kind_is_rejected_before_service(client, ai_spy, endpoint):
    response = client.post(f"/api/ai/{endpoint}", json=body_for(endpoint, kind="component"))
    assert_error(response, 422)
    ai_spy.explain.assert_not_awaited()
    ai_spy.investigate.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["explain", "investigate"])
@pytest.mark.parametrize("kind,target", [("node", "0007"), ("cluster", "cluster-main")])
def test_valid_ai_request_delegates_once_with_typed_request_and_store(client, ai_spy, endpoint, kind, target):
    response = client.post(f"/api/ai/{endpoint}", json=body_for(endpoint, kind=kind, target=target))
    assert response.status_code == 200
    assert_envelope(response.json())
    callback = getattr(ai_spy, endpoint)
    callback.assert_awaited_once()
    args, kwargs = callback.await_args
    request = args[0] if args else kwargs["request"]
    store = args[1] if len(args) > 1 else kwargs["store"]
    assert request.run_id == RUN_ID
    assert request.target.kind == kind and request.target.id == target
    assert store.get_node("0007").gid == "0007"


@pytest.mark.parametrize("body", [
    {"run_id": RUN_ID, "target": {"kind": "node", "id": 7}},
    {"run_id": RUN_ID, "target": {"kind": "node", "id": "0007"}},
    {"run_id": RUN_ID, "target": {"kind": "node", "id": "0007"}, "question": ""},
])
def test_malformed_investigate_rejected_before_service(client, ai_spy, body):
    assert_error(client.post("/api/ai/investigate", json=body), 422)
    ai_spy.investigate.assert_not_awaited()
