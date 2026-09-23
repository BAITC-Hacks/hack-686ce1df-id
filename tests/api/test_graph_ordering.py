"""Exercise graph caps and tie-breaking with synthetic supplied result records."""

from copy import deepcopy

from conftest import ARTIFACT_RUN_ID


def make_node(template, gid, score=0):
    node = deepcopy(template)
    node.update(gid=gid, priority_score=score, cluster_id="cluster-test", component_id="component-test")
    return node


def edge(source, target):
    return {"source": source, "target": target, "amount_kzt": "1.00", "tx_count": 1}


def cluster(nodes):
    return {
        "cluster_id": "cluster-test", "component_id": "component-test",
        "gids": [node["gid"] for node in nodes],
        "hypothesis": {"text": "Artificial contract test cluster; no analytics performed.", "basis_rule_ids": [], "limitations": ["Hand-authored fixture."]},
    }


def test_graph_order_is_distance_then_descending_score_then_gid(make_client, write_run, node_template):
    nodes = [make_node(node_template, gid, score) for gid, score in [
        ("selected", 0), ("direct-low", 10), ("direct-a", 50), ("direct-b", 50), ("distant-high", 100)
    ]]
    edges = [edge("selected", "direct-low"), edge("direct-a", "selected"), edge("selected", "direct-b"), edge("direct-low", "distant-high")]
    client = make_client(write_run(nodes, edges, [cluster(nodes)]))
    graph = client.get("/api/graph", params={"gid": "selected", "radius": 2, "limit": 4}).json()
    assert graph["contract_version"] == "1.0" and graph["run_id"] == ARTIFACT_RUN_ID
    assert [node["gid"] for node in graph["nodes"]] == ["selected", "direct-a", "direct-b", "direct-low"]
    assert graph["total_nodes"] == 5 and graph["shown_nodes"] == 4
    assert graph["truncated"] is True
    assert len(graph["edges"]) == 3
    assert all(e["source"] != "distant-high" and e["target"] != "distant-high" for e in graph["edges"])


def test_default_graph_cap_is_300_and_always_keeps_selected_node(make_client, write_run, node_template):
    center = make_node(node_template, "zz-selected", 0)
    neighbors = [make_node(node_template, f"n{index:03d}", 50) for index in range(305)]
    nodes = [center, *reversed(neighbors)]
    edges = [edge("zz-selected", node["gid"]) for node in neighbors]
    client = make_client(write_run(nodes, edges, [cluster(nodes)]))
    response = client.get("/api/graph", params={"gid": "zz-selected"})
    assert response.status_code == 200
    graph = response.json()
    assert graph["contract_version"] == "1.0" and graph["run_id"] == ARTIFACT_RUN_ID
    assert [node["gid"] for node in graph["nodes"]] == ["zz-selected", *[f"n{i:03d}" for i in range(299)]]
    assert graph["total_nodes"] == 306 and graph["shown_nodes"] == 300
    assert graph["truncated"] is True and len(graph["edges"]) == 299
    assert graph == client.get("/api/graph", params={"gid": "zz-selected"}).json()
    priorities = client.get("/api/priorities", params={"limit": 3}).json()
    assert [node["gid"] for node in priorities["items"]] == ["n000", "n001", "n002"]


def test_cluster_and_component_caps_use_score_then_gid(make_client, write_run, node_template):
    nodes = [make_node(node_template, gid, score) for gid, score in [("z", 10), ("b", 80), ("a", 80)]]
    client = make_client(write_run(nodes, [edge("z", "b"), edge("b", "a")], [cluster(nodes)]))
    for selection in ({"cluster_id": "cluster-test"}, {"component_id": "component-test"}):
        graph = client.get("/api/graph", params={**selection, "limit": 2}).json()
        assert graph["contract_version"] == "1.0" and graph["run_id"] == ARTIFACT_RUN_ID
        assert [node["gid"] for node in graph["nodes"]] == ["a", "b"]
        assert graph["total_nodes"] == 3 and graph["shown_nodes"] == 2
        assert graph["truncated"] is True
        assert graph["edges"] == [edge("b", "a")]
