"""Boundary regressions for optional integrations and immutable run snapshots."""

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote

import pytest

from backend.app.api import ai as ai_module
from backend.app.config import Settings
from backend.app.contracts import AICheck, AIResponse
from backend.app.main import create_app
from backend.app.store import ResultStore

from conftest import ARTIFACT_RUN_ID, RUN_ID, read_json, write_json
from test_ai_boundary import body_for
from test_http_contract import assert_error


@pytest.mark.parametrize("endpoint", ["explain", "investigate"])
def test_optional_ai_import_failure_returns_local_fallback(client, monkeypatch, endpoint):
    original_import = ai_module.importlib.import_module

    def import_with_broken_optional_module(name, *args, **kwargs):
        if name == "backend.app.ai.service":
            raise RuntimeError("Artificial optional module initialization failure")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(ai_module.importlib, "import_module", import_with_broken_optional_module)
    response = client.post(f"/api/ai/{endpoint}", json=body_for(endpoint))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "fallback" and payload["fallback_reason"]
    assert payload["run_id"] == RUN_ID
    assert client.get("/api/nodes/0007").status_code == 200


@pytest.mark.parametrize("failure", ["timeout", "wrong_run", "raises", "malformed", "mutated_nan_model"])
def test_failed_ai_service_uses_current_run_local_reference(client, fixture_dir, failure):
    supplied = read_json(fixture_dir / "ai_response.json")
    if failure == "wrong_run":
        supplied["run_id"] = "old-run"
    elif failure == "malformed":
        supplied = {"unexpected": "not an AIResponse"}
    elif failure == "mutated_nan_model":
        supplied = AIResponse.model_validate(supplied)
        supplied.checks.append(AICheck(
            tool="check_concentration", args={"gid": "0007"}, result={"share": 0.5},
            evidence_id="artificial-check",
        ))
        supplied.checks[0].result["share"] = float("nan")

    async def service(request, store):
        if failure == "timeout":
            await asyncio.sleep(60)
        elif failure == "raises":
            raise RuntimeError("Artificial provider error")
        return supplied

    callback = AsyncMock(side_effect=service)
    client.app.state.ai_service = SimpleNamespace(explain=callback)
    client.app.state.settings = replace(client.app.state.settings, ai_timeout_seconds=0.01)
    response = client.post("/api/ai/explain", json=body_for("explain"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "fallback" and payload["fallback_reason"]
    assert payload["run_id"] == RUN_ID
    assert payload["checks"] == []
    assert client.get("/api/nodes/0007").json()["node"]["role_explanation"] in payload["summary"]
    callback.assert_awaited_once()


def test_unicode_run_id_is_preserved_in_json_and_encoded_in_header(make_client, run_dir):
    run_id = "тест/运行 01"
    manifest = read_json(run_dir / "manifest.json")
    manifest["run_id"] = run_id
    write_json(run_dir / "manifest.json", manifest)
    client = make_client(run_dir)
    for endpoint in ("/api/health", "/api/nodes/0007"):
        response = client.get(endpoint)
        assert response.status_code == 200
        assert response.json()["run_id"] == run_id
        assert response.headers["x-run-id"] == quote(run_id, safe="")


def test_static_build_and_spa_routes_do_not_capture_api_routes(make_client, tmp_path):
    dist = tmp_path / "frontend-dist"
    (dist / "assets").mkdir(parents=True)
    index = "<!doctype html><html><body>Artificial frontend build</body></html>"
    javascript = 'console.log("artificial asset");'
    (dist / "index.html").write_text(index, encoding="utf-8")
    (dist / "assets" / "app.js").write_text(javascript, encoding="utf-8")
    client = make_client(frontend_dist=dist)
    assert client.get("/").text == index
    assert client.get("/nodes/0007").text == index
    asset = client.get("/assets/app.js")
    assert asset.status_code == 200 and asset.text == javascript
    assert client.get("/assets/missing.js").status_code == 404
    missing_api = client.get("/api/not-a-real-route")
    assert_error(missing_api, 404, run_id=None)
    assert missing_api.headers["content-type"].startswith("application/json")


def test_export_and_card_keep_loaded_snapshot_after_artifact_changes(make_client, run_dir):
    original = (run_dir / "nodes.json").read_bytes()
    client = make_client(run_dir)
    (run_dir / "nodes.json").write_text("[]", encoding="utf-8")
    exported = client.get("/api/exports/nodes")
    assert exported.status_code == 200
    assert exported.content == original
    assert exported.headers["x-run-id"] == ARTIFACT_RUN_ID
    assert client.get("/api/nodes/0007").json()["node"]["gid"] == "0007"


def test_mutating_returned_node_does_not_mutate_store_snapshot(fixture_dir):
    store = ResultStore.from_directory(fixture_dir)
    original = store.get_node("0007").model_dump()
    detached = store.get_node("0007")
    detached.gid = "changed"
    detached.metrics.out_amount_kzt = "999.00"
    detached.quality.reasons.append("Artificial mutation")
    assert store.get_node("0007").model_dump() == original


def test_concentration_delegates_gid_and_detached_edges_to_analytics(fixture_dir, monkeypatch):
    store = ResultStore.from_directory(fixture_dir)
    original_edges = [edge.model_dump() for edge in store.get_neighbors("7").edges]
    supplied_result = {"artificial_analytics_result": "returned unchanged"}
    check = Mock(return_value=supplied_result)
    monkeypatch.setitem(sys.modules, "backend.app.analytics.checks", SimpleNamespace(check_concentration=check))
    assert store.check_concentration("0007") is supplied_result
    check.assert_called_once()
    gid, passed_edges = check.call_args.args
    assert gid == "0007"
    assert [edge.model_dump() for edge in passed_edges] == original_edges
    passed_edges[0].amount_kzt = "999.00"
    assert [edge.model_dump() for edge in store.get_neighbors("7").edges] == original_edges


def test_openapi_snapshot_matches_current_canonical_models():
    snapshot = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"
    assert read_json(snapshot) == create_app(Settings()).openapi()
