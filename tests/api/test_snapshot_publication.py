"""Publication during a request cannot mix old response bodies with new headers."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Request

from backend.app.api import demo
from backend.app.api.ai import local_reference
from backend.app.api.dependencies import get_store
from backend.app import main


def publish_node(client, run_id):
    response = client.post("/api/demo/nodes", json={
        "run_id": run_id, "request_id": str(uuid4()),
        "gid": "later-node", "display_name": "Новый участник",
    })
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


@pytest.mark.parametrize("path,status", [("/api/priorities", 200), ("/api/nodes/absent", 404)])
def test_pinned_read_and_error_keep_original_run(make_client, tmp_path, path, status):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    original = client.get("/api/health").json()["run_id"]
    entered, release = Event(), Event()

    def paused_store(request: Request):
        store = get_store(request)
        entered.set()
        assert release.wait(10), "Publication test did not release the old request"
        return store

    client.app.dependency_overrides[get_store] = paused_store
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.get, path)
        try:
            assert entered.wait(10)
            current = publish_node(client, original)
        finally:
            release.set()
        response = pending.result(timeout=10)
    assert response.status_code == status
    assert response.json()["run_id"] == original
    assert response.headers["x-run-id"] == original
    assert client.get("/api/health").json()["run_id"] == current


def test_pinned_names_remain_with_their_run(make_client, tmp_path, monkeypatch):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    original = client.get("/api/demo").json()
    entered, release = Event(), Event()
    real_node_name = demo.NodeName

    def paused_name(**kwargs):
        entered.set()
        assert release.wait(10), "Publication test did not release the name response"
        return real_node_name(**kwargs)

    monkeypatch.setattr(demo, "NodeName", paused_name)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.get, "/api/demo")
        try:
            assert entered.wait(10)
            current = publish_node(client, original["run_id"])
        finally:
            release.set()
        response = pending.result(timeout=10)
    assert response.json() == original
    assert response.headers["x-run-id"] == original["run_id"]
    info = client.get("/api/demo").json()
    assert info["run_id"] == current and info["node_count"] == 501
    assert {"gid": "later-node", "display_name": "Новый участник"} in info["nodes"]


def test_ai_finishing_after_publication_keeps_original_snapshot(make_client, tmp_path):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    original = client.get("/api/health").json()["run_id"]
    entered, release = Event(), Event()

    async def delayed_explanation(body, store):
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        assert "later-node" not in store.nodes_by_gid
        return local_reference(body, store, "test_local_reference")

    client.app.state.ai_service = SimpleNamespace(explain=delayed_explanation)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/api/ai/explain", json={
            "run_id": original, "target": {"kind": "node", "id": "0007"},
        })
        try:
            assert entered.wait(10)
            current = publish_node(client, original)
        finally:
            release.set()
        response = pending.result(timeout=10)
    assert response.status_code == 200
    assert response.json()["run_id"] == original
    assert response.headers["x-run-id"] == original
    assert client.get("/api/health").json()["run_id"] == current


def test_health_finishing_after_publication_keeps_original_snapshot(make_client, tmp_path, monkeypatch):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    original = client.get("/api/health").json()["run_id"]
    entered, release = Event(), Event()
    real_response = main.HealthResponse

    def paused_response(**kwargs):
        entered.set()
        assert release.wait(10)
        return real_response(**kwargs)

    monkeypatch.setattr(main, "HealthResponse", paused_response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.get, "/api/health")
        try:
            assert entered.wait(10)
            current = publish_node(client, original)
        finally:
            release.set()
        response = pending.result(timeout=10)
    assert response.json()["run_id"] == original
    assert response.headers["x-run-id"] == original
    assert client.get("/api/health").json()["run_id"] == current


def test_stale_mutation_error_reports_repository_run_not_request_snapshot(make_client, tmp_path, monkeypatch):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    original = client.get("/api/health").json()["run_id"]
    entered, release = Event(), Event()
    original_add = client.app.state.demo_repository.add_node

    def paused_add(command):
        if command.gid == "blocked-node":
            entered.set()
            assert release.wait(10)
        return original_add(command)

    monkeypatch.setattr(client.app.state.demo_repository, "add_node", paused_add)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/api/demo/nodes", json={
            "run_id": original, "request_id": str(uuid4()), "gid": "blocked-node", "display_name": "Ещё участник",
        })
        try:
            assert entered.wait(10)
            current = publish_node(client, original)
        finally:
            release.set()
        response = pending.result(timeout=10)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_run"
    assert response.json()["run_id"] == current
    assert response.headers["x-run-id"] == current
