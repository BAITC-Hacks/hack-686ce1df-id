"""The API must never promote partial or malformed artifacts to a ready run."""

import json
import shutil

import pytest

from backend.app.main import create_app
from fastapi.testclient import TestClient

from conftest import ARTIFACT_RUN_ID, RUN_ID, read_json, write_json
from test_http_contract import assert_error


def assert_unavailable(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok", "contract_version": "1.0", "run_id": None, "data_ready": False
    }
    assert health.headers["x-data-source"] == "unavailable"
    assert_error(client.get("/api/nodes/0007"), 503, run_id=None)
    assert_error(client.get("/api/priorities"), 503, run_id=None)
    assert_error(client.get("/api/graph", params={"gid": "0007"}), 503, run_id=None)
    assert_error(client.get("/api/exports/nodes"), 503, run_id=None)
    assert_error(client.post("/api/ai/explain", json={
        "run_id": RUN_ID, "target": {"kind": "node", "id": "0007"}
    }), 503, run_id=None)


def test_default_configuration_never_silently_loads_development_data(monkeypatch):
    monkeypatch.delenv("AML_RUN_DIR", raising=False)
    monkeypatch.delenv("AML_FIXTURE_MODE", raising=False)
    with TestClient(create_app()) as client:
        assert_unavailable(client)


def test_explicit_empty_configuration_is_not_ready(make_client):
    assert_unavailable(make_client())


def test_explicit_fixture_mode_loads_artificial_run(make_client):
    client = make_client(fixture_mode=True)
    assert client.get("/api/health").json()["run_id"] == RUN_ID
    assert client.get("/api/health").headers["x-data-source"] == "fixtures"


def test_configured_complete_directory_loads_one_run(make_client, run_dir):
    client = make_client(run_dir)
    health = client.get("/api/health")
    assert health.json()["data_ready"] is True
    assert health.json()["run_id"] == ARTIFACT_RUN_ID
    assert health.headers["x-data-source"] == "artifacts"
    assert client.get("/api/nodes/0007").headers["x-run-id"] == ARTIFACT_RUN_ID


def test_canonical_fixture_directory_requires_explicit_fixture_mode(make_client, fixture_dir):
    assert_unavailable(make_client(fixture_dir))


def test_copied_fixture_run_requires_explicit_fixture_mode(make_client, fixture_dir, tmp_path):
    copied = tmp_path / "copied-fixture"
    shutil.copytree(fixture_dir, copied)
    assert_unavailable(make_client(copied))


@pytest.mark.parametrize("file_name", ["manifest.json", "nodes.json", "edges.json", "clusters.json", "roles.csv", "priorities.csv", "rules.json", "audit.json"])
def test_missing_required_artifact_is_not_ready(make_client, run_dir, file_name):
    (run_dir / file_name).unlink()
    assert_unavailable(make_client(run_dir))


@pytest.mark.parametrize("file_name", ["manifest.json", "nodes.json", "edges.json", "clusters.json", "rules.json", "audit.json"])
def test_malformed_json_is_not_ready(make_client, run_dir, file_name):
    (run_dir / file_name).write_text("{not valid json", encoding="utf-8")
    assert_unavailable(make_client(run_dir))


def test_incomplete_manifest_is_not_ready(make_client, run_dir):
    manifest = read_json(run_dir / "manifest.json")
    manifest["status"] = "running"
    write_json(run_dir / "manifest.json", manifest)
    assert_unavailable(make_client(run_dir))


def test_manifest_cannot_point_outside_run_directory(make_client, run_dir):
    outside = run_dir.parent / "outside.json"
    outside.write_bytes((run_dir / "nodes.json").read_bytes())
    manifest = read_json(run_dir / "manifest.json")
    manifest["files"]["nodes"] = "../outside.json"
    write_json(run_dir / "manifest.json", manifest)
    assert_unavailable(make_client(run_dir))


def test_duplicate_node_id_is_not_ready(make_client, run_dir):
    nodes = read_json(run_dir / "nodes.json")
    nodes.append(nodes[0])
    write_json(run_dir / "nodes.json", nodes)
    assert_unavailable(make_client(run_dir))


def test_edge_with_unknown_endpoint_is_not_ready(make_client, run_dir):
    edges = read_json(run_dir / "edges.json")
    edges[0]["target"] = "not-in-run"
    write_json(run_dir / "edges.json", edges)
    assert_unavailable(make_client(run_dir))


def test_required_null_field_cannot_be_omitted(make_client, run_dir):
    nodes = read_json(run_dir / "nodes.json")
    del nodes[0]["metrics"]["out_in_ratio"]
    write_json(run_dir / "nodes.json", nodes)
    assert_unavailable(make_client(run_dir))


def test_non_finite_json_cannot_be_served(make_client, run_dir):
    nodes = read_json(run_dir / "nodes.json")
    nodes[0]["metrics"]["out_in_ratio"] = float("nan")
    (run_dir / "nodes.json").write_text(json.dumps(nodes), encoding="utf-8")
    assert_unavailable(make_client(run_dir))


def test_manifest_counts_must_match_actual_results(make_client, run_dir):
    manifest = read_json(run_dir / "manifest.json")
    manifest["counts"]["nodes"] += 1
    write_json(run_dir / "manifest.json", manifest)
    assert_unavailable(make_client(run_dir))


def test_rules_hash_must_match_actual_bytes(make_client, run_dir):
    with (run_dir / "rules.json").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert_unavailable(make_client(run_dir))


@pytest.mark.parametrize("name", ["roles.csv", "priorities.csv"])
def test_incomplete_csv_coverage_is_not_ready(make_client, run_dir, name):
    path = run_dir / name
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    assert_unavailable(make_client(run_dir))
