"""Contract acceptance tests against explicitly artificial result artifacts."""

import csv
import io

import pytest

from backend.app.contracts import AIResponse, ClusterRecord, NodeRecord

from conftest import RUN_ID


def assert_error(response, status: int, run_id=RUN_ID):
    assert response.status_code == status, response.text
    payload = response.json()
    assert set(payload) == {"error", "run_id"}
    assert payload["run_id"] == run_id
    assert set(payload["error"]) == {"code", "message"}
    assert isinstance(payload["error"]["code"], str) and payload["error"]["code"]
    assert isinstance(payload["error"]["message"], str) and payload["error"]["message"]


def assert_envelope(payload):
    assert payload["contract_version"] == "1.0"
    assert payload["run_id"] == RUN_ID


def test_health_marks_explicit_fixture_source(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "contract_version": "1.0", "run_id": RUN_ID, "data_ready": True
    }
    assert response.headers["x-data-source"] == "fixtures"


def test_exact_gid_preserves_leading_zeros_and_nulls(client, fixture_data):
    padded = client.get("/api/nodes/0007")
    plain = client.get("/api/nodes/7")
    assert padded.status_code == plain.status_code == 200
    assert_envelope(padded.json())
    assert padded.json()["node"] == fixture_data["nodes"][0]
    assert plain.json()["node"]["gid"] == "7"
    assert padded.json()["node"]["gid"] != plain.json()["node"]["gid"]
    assert padded.json()["node"]["metrics"]["out_in_ratio"] is None
    assert padded.json()["node"]["quality"]["is_seed"] is True
    assert padded.json()["node"]["quality"]["inbound_incomplete"] is True
    NodeRecord.model_validate(padded.json()["node"])


@pytest.mark.parametrize("gid", ["000", "07", "00070", "not-in-fixture"])
def test_unknown_and_partial_gid_are_not_matches(client, gid):
    assert_error(client.get(f"/api/nodes/{gid}"), 404)


def test_priorities_are_paginated_in_stable_score_order(client):
    response = client.get("/api/priorities")
    assert response.status_code == 200
    payload = response.json()
    assert_envelope(payload)
    assert payload["total"] == 4
    assert [node["gid"] for node in payload["items"]] == ["7", "0012", "0007", "isolated"]
    page = client.get("/api/priorities", params={"limit": 2, "offset": 1}).json()
    assert [node["gid"] for node in page["items"]] == ["0012", "0007"]
    assert page["total"] == 4
    assert client.get("/api/priorities", params={"offset": 100}).json()["items"] == []


@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": -1}, {"offset": -1}, {"limit": "oops"}])
def test_invalid_priority_pagination_uses_error_contract(client, query):
    assert_error(client.get("/api/priorities", params=query), 422)


def test_clusters_list_and_exact_card(client):
    listing = client.get("/api/clusters")
    assert listing.status_code == 200
    assert_envelope(listing.json())
    assert {item["cluster_id"] for item in listing.json()["items"]} == {"cluster-main", "cluster-isolated"}
    card = client.get("/api/clusters/cluster-main")
    assert card.status_code == 200
    assert_envelope(card.json())
    assert set(card.json()["cluster"]["gids"]) == {"0007", "7", "0012"}
    ClusterRecord.model_validate(card.json()["cluster"])
    assert_error(client.get("/api/clusters/absent"), 404)


def test_graph_walks_both_directions_and_preserves_directed_edges(client, fixture_data):
    response = client.get("/api/graph", params={"gid": "7", "radius": 1})
    assert response.status_code == 200
    graph = response.json()
    assert_envelope(graph)
    assert {node["gid"] for node in graph["nodes"]} == {"0007", "7", "0012"}
    assert {(edge["source"], edge["target"]) for edge in graph["edges"]} == {("0007", "7"), ("7", "0012")}
    assert graph["truncated"] is False
    assert graph["total_nodes"] == graph["shown_nodes"] == 3
    original = {node["gid"]: node for node in fixture_data["nodes"]}
    assert all(node == original[node["gid"]] for node in graph["nodes"])


def test_gid_radius_limits_observed_neighborhood(client):
    radius_one = client.get("/api/graph", params={"gid": "0007", "radius": 1}).json()
    radius_two = client.get("/api/graph", params={"gid": "0007", "radius": 2}).json()
    assert {node["gid"] for node in radius_one["nodes"]} == {"0007", "7"}
    assert {node["gid"] for node in radius_two["nodes"]} == {"0007", "7", "0012"}


def test_isolated_node_remains_available(client):
    card = client.get("/api/nodes/isolated")
    assert card.status_code == 200
    assert card.json()["node"]["quality"]["hop_depth"] is None
    response = client.get("/api/graph", params={"gid": "isolated", "radius": 2})
    assert response.status_code == 200
    graph = response.json()
    assert [node["gid"] for node in graph["nodes"]] == ["isolated"]
    assert graph["edges"] == []
    assert graph["shown_nodes"] == graph["total_nodes"] == 1
    assert graph["truncated"] is False


@pytest.mark.parametrize("selection", [{"cluster_id": "cluster-main"}, {"component_id": "component-main"}])
def test_graph_supports_cluster_and_component_selection(client, selection):
    response = client.get("/api/graph", params=selection)
    assert response.status_code == 200
    assert {node["gid"] for node in response.json()["nodes"]} == {"0007", "7", "0012"}


@pytest.mark.parametrize("query", [
    {}, {"gid": "7", "cluster_id": "cluster-main"},
    {"gid": "7", "component_id": "component-main"},
    {"cluster_id": "cluster-main", "component_id": "component-main"},
    {"gid": "7", "radius": 0}, {"gid": "7", "radius": 3},
    {"gid": "7", "radius": "x"}, {"gid": "7", "limit": 0},
    {"gid": "7", "limit": 301}, {"gid": "7", "limit": -1},
])
def test_invalid_graph_selection_radius_and_limit(client, query):
    assert_error(client.get("/api/graph", params=query), 422)


@pytest.mark.parametrize("selection", [{"gid": "absent"}, {"cluster_id": "absent"}, {"component_id": "absent"}])
def test_unknown_graph_target(client, selection):
    assert_error(client.get("/api/graph", params=selection), 404)


def test_truncation_keeps_selected_node_and_full_card_metrics(client, fixture_data):
    response = client.get("/api/graph", params={"gid": "0007", "radius": 2, "limit": 1})
    assert response.status_code == 200
    graph = response.json()
    assert graph["nodes"] == [fixture_data["nodes"][0]]
    assert graph["edges"] == []
    assert graph["truncated"] is True
    assert graph["total_nodes"] == 3 and graph["shown_nodes"] == 1


def test_export_uses_manifest_logical_name_and_current_run(client, fixture_dir):
    response = client.get("/api/exports/priorities")
    assert response.status_code == 200
    assert response.content == (fixture_dir / "priorities.csv").read_bytes()
    assert response.headers["x-run-id"] == RUN_ID
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[2]["gid"] == "0007"
    assert client.get("/api/exports/nodes").json()[0]["gid"] == "0007"


@pytest.mark.parametrize("name", ["priorities.csv", "manifest.json", "README.md", "unknown", "..%5CREADME.md", "..%2FREADME.md"])
def test_export_rejects_paths_and_unlisted_names(client, name):
    assert_error(client.get(f"/api/exports/{name}"), 404)


def test_cors_allows_only_configured_local_frontend(client):
    allowed = client.options("/api/ai/explain", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.get("/api/health", headers={"Origin": "https://untrusted.invalid"})
    assert "access-control-allow-origin" not in denied.headers


@pytest.mark.parametrize("endpoint", ["explain", "investigate"])
def test_local_fallback_remains_available_without_ai(client, monkeypatch, endpoint):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    client.app.state.ai_service = None
    body = {"run_id": RUN_ID, "target": {"kind": "node", "id": "0007"}}
    if endpoint == "investigate":
        body["question"] = "Что известно из наблюдаемых данных?"
    response = client.post(f"/api/ai/{endpoint}", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert_envelope(payload)
    AIResponse.model_validate(payload)
    assert payload["status"] == "fallback"
    assert payload["summary"] and payload["fallback_reason"]
    assert client.get("/api/nodes/0007").status_code == 200
