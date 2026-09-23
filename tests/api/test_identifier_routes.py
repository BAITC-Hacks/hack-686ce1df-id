"""Exact card lookups preserve encoded slashes in contract identifiers."""

from copy import deepcopy
from urllib.parse import quote

import pytest

from conftest import ARTIFACT_RUN_ID
from test_http_contract import assert_error


IDENTIFIERS = [
    "bank7",
    "bank/7",
    "/bank/7",
    "bank/7/",
    "bank//7",
    "bank/branch/0007",
    "банк/七",
    "/",
]


@pytest.fixture
def identifier_client(make_client, write_run, node_template):
    nodes, clusters = [], []
    for index, identifier in enumerate(IDENTIFIERS):
        component_id = f"component-{index}"
        node = deepcopy(node_template)
        node.update(gid=identifier, cluster_id=identifier, component_id=component_id)
        nodes.append(node)
        clusters.append({
            "cluster_id": identifier,
            "component_id": component_id,
            "gids": [identifier],
            "hypothesis": {"text": "Artificial identifier fixture", "basis_rule_ids": [], "limitations": []},
        })
    return make_client(write_run(nodes, [], clusters))


@pytest.mark.parametrize("identifier", IDENTIFIERS)
@pytest.mark.parametrize("resource,record,key", [
    ("nodes", "node", "gid"),
    ("clusters", "cluster", "cluster_id"),
])
def test_card_lookup_preserves_exact_encoded_identifier(identifier_client, resource, record, key, identifier):
    response = identifier_client.get(f"/api/{resource}/{quote(identifier, safe='')}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["contract_version"] == "1.0"
    assert payload["run_id"] == ARTIFACT_RUN_ID
    assert payload[record][key] == identifier


@pytest.mark.parametrize("resource,key", [("nodes", "gid"), ("clusters", "cluster_id")])
@pytest.mark.parametrize("identifier", ["/unknown", "bank/7//", "bank/branch/007"])
def test_unknown_slash_identifier_is_an_exact_record_miss(identifier_client, resource, key, identifier):
    response = identifier_client.get(f"/api/{resource}/{quote(identifier, safe='')}")
    assert_error(response, 404, run_id=ARTIFACT_RUN_ID)
    assert response.json()["error"]["message"] == f"Unknown {key}: {identifier}"


@pytest.mark.parametrize("resource", ["nodes", "clusters"])
def test_card_lookup_rejects_empty_identifier(identifier_client, resource):
    assert_error(identifier_client.get(f"/api/{resource}/"), 422, run_id=ARTIFACT_RUN_ID)


def test_cluster_collection_remains_available(identifier_client):
    response = identifier_client.get("/api/clusters")
    assert response.status_code == 200
    assert {cluster["cluster_id"] for cluster in response.json()["items"]} == set(IDENTIFIERS)
