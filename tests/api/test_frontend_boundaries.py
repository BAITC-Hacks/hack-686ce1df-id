"""Regression coverage for browser lookup and the frontend's fixed export menu."""

from urllib.parse import quote

import pytest

from conftest import ARTIFACT_RUN_ID, RUN_ID, read_json, write_json
from test_http_contract import assert_error


ARTIFACTS = {
    "nodes": "nodes.json",
    "edges": "edges.json",
    "clusters": "clusters.json",
    "priorities": "priorities.csv",
    "roles": "roles.csv",
    "rules": "rules.json",
    "audit": "audit.json",
    "manifest": "manifest.json",
}


@pytest.mark.parametrize("name,filename", ARTIFACTS.items())
def test_every_frontend_export_is_available_for_backend_fixtures(client, fixture_dir, name, filename):
    response = client.get(f"/api/exports/{name}")
    assert response.status_code == 200
    assert response.content == (fixture_dir / filename).read_bytes()
    assert response.headers["x-run-id"] == RUN_ID
    assert response.headers["content-disposition"] == f"attachment; filename*=UTF-8''{filename}"


def test_filename_manifest_keys_keep_exact_exports_and_canonical_aliases(make_client, run_dir):
    manifest = read_json(run_dir / "manifest.json")
    manifest["files"] = {filename: filename for filename in manifest["files"].values()}
    write_json(run_dir / "manifest.json", manifest)
    original = {filename: (run_dir / filename).read_bytes() for filename in ARTIFACTS.values()}
    original_extra = (run_dir / "ai_response.json").read_bytes()
    client = make_client(run_dir)
    assert client.get("/api/health").json()["data_ready"] is True

    # The exact manifest and both aliases must keep the same startup snapshot.
    for filename in original:
        (run_dir / filename).write_bytes(b"changed after startup")
    for name, filename in ARTIFACTS.items():
        response = client.get(f"/api/exports/{name}")
        assert response.status_code == 200
        assert response.content == original[filename]
        assert response.headers["x-run-id"] == ARTIFACT_RUN_ID
        if filename in manifest["files"]:
            assert client.get(f"/api/exports/{filename}").content == original[filename]
    extra = client.get("/api/exports/ai_response.json")
    assert extra.status_code == 200 and extra.content == original_extra
    assert_error(client.get("/api/exports/ai_response"), 404, run_id=ARTIFACT_RUN_ID)


@pytest.mark.parametrize("key", ["manifest", "manifest.json"])
def test_manifest_may_explicitly_list_itself(make_client, run_dir, key):
    manifest = read_json(run_dir / "manifest.json")
    manifest["files"][key] = "manifest.json"
    write_json(run_dir / "manifest.json", manifest)
    original = (run_dir / "manifest.json").read_bytes()
    client = make_client(run_dir)
    assert client.get("/api/health").json()["data_ready"] is True
    (run_dir / "manifest.json").write_bytes(b"changed after startup")
    for name in {"manifest", key}:
        response = client.get(f"/api/exports/{name}")
        assert response.status_code == 200 and response.content == original


def test_reserved_manifest_export_cannot_point_to_another_artifact(make_client, run_dir):
    manifest = read_json(run_dir / "manifest.json")
    manifest["files"]["manifest"] = "nodes.json"
    write_json(run_dir / "manifest.json", manifest)
    client = make_client(run_dir)
    assert client.get("/api/health").json()["data_ready"] is False
    assert_error(client.get("/api/exports/manifest"), 503, run_id=None)


@pytest.mark.parametrize("identifier", ["0007", "review/0007", "қазақ/运行 #?%", ".", "..", "a/../b", "a%2Fb"])
def test_query_lookups_preserve_opaque_identifiers(make_client, write_run, node_template, identifier):
    node = {**node_template, "gid": identifier, "cluster_id": identifier, "component_id": "component-test"}
    cluster = {
        "cluster_id": identifier,
        "component_id": "component-test",
        "gids": [identifier],
        "hypothesis": {"text": "Artificial lookup example.", "basis_rule_ids": [], "limitations": []},
    }
    client = make_client(write_run([node], [], [cluster]))
    assert client.get("/api/health").json()["data_ready"] is True
    node_response = client.get("/api/node", params={"gid": identifier})
    cluster_response = client.get("/api/cluster", params={"cluster_id": identifier})
    assert node_response.status_code == cluster_response.status_code == 200
    assert node_response.json()["node"] == node
    assert cluster_response.json()["cluster"] == cluster
    assert node_response.json()["run_id"] == cluster_response.json()["run_id"] == ARTIFACT_RUN_ID


@pytest.mark.parametrize("identifier", ["review/0007", "қазақ/运行"])
def test_legacy_card_paths_accept_slash_and_unicode_ids(make_client, write_run, node_template, identifier):
    node = {**node_template, "gid": identifier, "cluster_id": identifier, "component_id": "component-test"}
    cluster = {
        "cluster_id": identifier,
        "component_id": "component-test",
        "gids": [identifier],
        "hypothesis": {"text": "Artificial lookup example.", "basis_rule_ids": [], "limitations": []},
    }
    client = make_client(write_run([node], [], [cluster]))
    encoded = quote(identifier, safe="")
    node_response = client.get(f"/api/nodes/{encoded}")
    cluster_response = client.get(f"/api/clusters/{encoded}")
    assert node_response.status_code == cluster_response.status_code == 200
    assert node_response.json()["node"]["gid"] == identifier
    assert cluster_response.json()["cluster"]["cluster_id"] == identifier


@pytest.mark.parametrize("endpoint,key", [("/api/node", "gid"), ("/api/cluster", "cluster_id")])
@pytest.mark.parametrize("value,status", [(None, 422), ("", 422), ("missing/id", 404)])
def test_query_lookup_errors_follow_the_api_contract(client, endpoint, key, value, status):
    response = client.get(endpoint, params={} if value is None else {key: value})
    assert_error(response, status)
