"""Read-only HTTP acceptance check of a running real-data application."""

import argparse
import csv
import io
import json
import re
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("base-url must be a local HTTP server")
    opener = build_opener(ProxyHandler({}))
    run_id = None

    def get(path, as_json=True, expect_run=True):
        with opener.open(base + path, timeout=30) as response:
            if expect_run and run_id:
                assert unquote(response.headers["X-Run-Id"]) == run_id, f"Mixed run at {path}"
                assert response.headers["X-Data-Source"] == "artifacts"
            content = response.read()
            return json.loads(content) if as_json else content

    health = get("/api/health")
    assert health["data_ready"] and health["run_id"].startswith("real-")
    run_id = health["run_id"]
    html = get("/", False, False).decode()
    assert '<div id="root">' in html
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
    assert assets
    for path in assets:
        assert get(path, False, False)
    manifest = get("/api/exports/manifest")
    nodes = get("/api/exports/nodes")
    assert len(nodes) == manifest["counts"]["nodes"]
    priorities = get("/api/priorities?limit=20")
    assert priorities["total"] == len(nodes)
    assert len(priorities["items"]) == min(20, len(nodes))
    cases = {
        "priority": priorities["items"][0],
        "boundary": next(node for node in nodes if node["quality"]["outbound_censored"]),
        "seed": next(node for node in nodes if node["quality"]["is_seed"]),
        "isolated": next(node for node in nodes if node["metrics"]["in_degree_unique"] + node["metrics"]["out_degree_unique"] == 0),
    }
    for label, node in cases.items():
        gid = quote(node["gid"], safe="")
        assert get(f"/api/nodes/{gid}")["node"]["gid"] == node["gid"]
        graph = get(f"/api/graph?gid={gid}&radius=2&limit=300")
        assert node["gid"] in {item["gid"] for item in graph["nodes"]}
        if label == "isolated":
            assert graph["shown_nodes"] == 1 and graph["edges"] == []
    assert cases["boundary"]["role"] != "terminal"
    cluster = quote(cases["priority"]["cluster_id"], safe="")
    assert get(f"/api/clusters/{cluster}")["cluster"]["gids"]
    demo = get("/api/demo")
    assert demo["enabled"] is False
    try:
        get("/api/nodes/unknown-real-smoke-id")
    except HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError("Unknown gid must return 404")
    for name in manifest["files"]:
        assert get("/api/exports/" + quote(name, safe=""), False)
    for key, expected in (("nodes_roles", len(nodes)), ("top_nodes", min(20, len(nodes))), ("clusters_csv", manifest["counts"]["clusters"])):
        rows = list(csv.DictReader(io.StringIO(get(f"/api/exports/{key}", False).decode())))
        assert len(rows) == expected
        if key != "clusters_csv":
            assert all(0 <= float(row["priority_score"]) <= 1 for row in rows)
    assert get("/api/health")["run_id"] == run_id
    print(json.dumps({"status": "passed", "run_id": run_id, "counts": manifest["counts"],
                      "exports_checked": len(manifest["files"]),
                      "examples": {label: node["gid"] for label, node in cases.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
