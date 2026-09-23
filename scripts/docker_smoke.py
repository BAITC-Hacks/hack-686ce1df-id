"""Smoke-check the built web app with built-in fixtures and AI keys disabled.

Run inside the container, or pass its base URL from a host/CI Python process.
Only Python's standard library is required.
"""

import argparse
import csv
from html.parser import HTMLParser
import io
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


RUN_ID = "fixture-contract-v1"
FIXTURE_GIDS = {"0007", "7", "0012", "isolated"}


class SmokeFailure(Exception):
    """A short, actionable smoke-check failure."""


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.paths.add(attrs["src"])
        if tag == "link" and set(attrs.get("rel", "").split()) & {"stylesheet", "modulepreload"}:
            if attrs.get("href"):
                self.paths.add(attrs["href"])


class Client:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))
        self.requests = 0

    def get(self, path, *, body=None, status=200):
        headers = {"Accept": "*/*"}
        encoded = None
        if body is not None:
            encoded = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=encoded, headers=headers)
        try:
            response = self.opener.open(request, timeout=40)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError) as error:
            raise SmokeFailure(f"{path}: request failed ({error})") from None
        with response:
            self.requests += 1
            require(response.status == status, f"{path}: expected HTTP {status}, got {response.status}")
            content = response.read()
            headers = response.headers
        if path == "/api" or path.startswith("/api/"):
            for name, value in {
                "X-Data-Source": "fixtures",
                "X-Contract-Version": "1.0",
                "X-Run-Id": RUN_ID,
                "Cache-Control": "no-store",
            }.items():
                require(headers.get(name) == value, f"{path}: expected {name}: {value}")
        return content, headers

    def json(self, path, *, body=None, status=200):
        content, headers = self.get(path, body=body, status=status)
        require(headers.get_content_type() == "application/json", f"{path}: expected JSON content type")
        try:
            payload = json.loads(content)
        except (ValueError, UnicodeError):
            raise SmokeFailure(f"{path}: invalid JSON") from None
        require(isinstance(payload, dict), f"{path}: expected a JSON object")
        require(payload.get("run_id") == RUN_ID, f"{path}: expected fixture run {RUN_ID}")
        if status == 200:
            require(payload.get("contract_version") == "1.0", f"{path}: expected contract 1.0")
        return payload


def smoke(client):
    health = client.json("/api/health")
    require(health.get("data_ready") is True and health.get("status") == "ok", "health: fixture data is not ready")
    print("PASS: fixture health and API headers")

    html, headers = client.get("/")
    require(headers.get_content_type() == "text/html", "/: expected HTML")
    require(b'id="root"' in html, "/: missing React root")
    nested, nested_headers = client.get("/smoke/deep-link")
    require(nested_headers.get_content_type() == "text/html" and nested == html, "SPA: deep link did not return index.html")
    assets = Assets()
    assets.feed(html.decode("utf-8"))
    require(any(urlsplit(path).path.endswith(".js") for path in assets.paths), "HTML: no built JavaScript asset")
    require(any(urlsplit(path).path.endswith(".css") for path in assets.paths), "HTML: no built CSS asset")
    for asset in sorted(assets.paths):
        url = urlsplit(urljoin(client.base_url + "/", asset))
        origin = urlsplit(client.base_url)
        require((url.scheme, url.netloc) == (origin.scheme, origin.netloc), "HTML: expected bundled same-origin assets")
        content, asset_headers = client.get(url.path + ("?" + url.query if url.query else ""))
        expected_types = {"text/css"} if url.path.endswith(".css") else {"application/javascript", "text/javascript"}
        require(content and asset_headers.get_content_type() in expected_types, f"{url.path}: asset missing or wrong content type")
    print(f"PASS: built UI, SPA route, and {len(assets.paths)} assets")

    padded = client.json("/api/nodes/0007")["node"]
    plain = client.json("/api/nodes/7")["node"]
    require(padded.get("gid") == "0007" and plain.get("gid") == "7", "nodes: leading-zero IDs were not preserved")
    priorities = client.json("/api/priorities?limit=20&offset=0")
    require(priorities.get("total") == 4, "priorities: expected four fixture nodes")
    require({node["gid"] for node in priorities["items"]} == FIXTURE_GIDS, "priorities: unexpected fixture IDs")
    clusters = client.json("/api/clusters")
    require({cluster["cluster_id"] for cluster in clusters["items"]} == {"cluster-main", "cluster-isolated"}, "clusters: unexpected fixture IDs")
    print("PASS: exact node IDs, priorities, and clusters")

    graph = client.json("/api/graph?gid=0007&radius=2&limit=300")
    require({node["gid"] for node in graph["nodes"]} == FIXTURE_GIDS - {"isolated"}, "graph: missing fixture neighbors")
    require({(edge["source"], edge["target"]) for edge in graph["edges"]} == {("0007", "7"), ("7", "0012")}, "graph: wrong directed edges")
    require(graph.get("shown_nodes") == graph.get("total_nodes") == 3 and graph.get("truncated") is False, "graph: wrong counts or truncation")
    isolated = client.json("/api/graph?gid=isolated")
    require([node["gid"] for node in isolated["nodes"]] == ["isolated"] and isolated["edges"] == [], "graph: isolated node was not preserved")
    print("PASS: connected and isolated graphs")

    content, headers = client.get("/api/exports/priorities")
    require(headers.get_content_type() == "text/csv", "export: expected CSV content type")
    require("attachment" in headers.get("Content-Disposition", ""), "export: missing download header")
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    require(len(rows) == 4 and {row["gid"] for row in rows} == FIXTURE_GIDS, "export: wrong fixture rows or IDs")
    print("PASS: CSV download")

    for mode in ("explain", "investigate"):
        body = {"run_id": RUN_ID, "target": {"kind": "node", "id": "0007"}}
        if mode == "investigate":
            body["question"] = "Объясни наблюдаемые связи этого узла."
        result = client.json(f"/api/ai/{mode}", body=body)
        require(result.get("status") == "fallback", f"AI {mode}: expected local fallback; disable AI keys for this smoke check")
        require(result.get("fallback_reason") in {"missing_api_key", "missing_model"}, f"AI {mode}: expected unconfigured provider fallback")
        require(result.get("summary") and result.get("claims") and result.get("limitations"), f"AI {mode}: missing local explanation or limitations")
    print("PASS: local AI explain and investigate fallbacks")

    missing = client.json("/api/smoke-unknown-route", status=404)
    require(isinstance(missing.get("error"), dict) and missing["error"].get("code") == "not_found", "unknown API route: expected JSON not_found, not SPA HTML")
    print(f"PASS: unknown API route; all {client.requests} HTTP checks passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", nargs="?", default="http://127.0.0.1:8000", help="App origin running built-in fixtures with AI keys disabled")
    args = parser.parse_args()
    try:
        url = urlsplit(args.base_url)
        require(url.scheme in {"http", "https"} and url.hostname and url.path in {"", "/"} and not (url.query or url.fragment or url.username or url.password), "base URL must be an HTTP(S) origin without credentials, path, query, or fragment")
        _ = url.port
        smoke(Client(args.base_url))
    except (SmokeFailure, KeyError, TypeError, AttributeError, ValueError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
