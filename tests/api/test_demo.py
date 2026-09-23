"""HTTP capabilities, durable named edits, and exact mutation envelopes."""

from dataclasses import replace
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from backend.app.config import PROJECT_ROOT, Settings


def node_command(run_id, **fields):
    return {"run_id": run_id, "request_id": str(uuid4()), "display_name": "Айдана Садыкова", **fields}


def transfer_command(run_id, **fields):
    return {
        "run_id": run_id, "request_id": str(uuid4()), "source": "0007", "target": "7",
        "amount_kzt": "5000.01", "date": "2026-07-12", **fields,
    }


def assert_demo_error(response, status, code, run_id):
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["run_id"] == run_id
    if run_id is not None:
        assert response.headers["x-run-id"] == run_id


def test_add_named_node_and_transfer(make_client, tmp_path):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    info = client.get("/api/demo").json()
    assert info["enabled"] is True
    assert info["node_count"] == 500 and info["transfer_count"] == 6000
    assert len(info["nodes"]) == 500
    assert info["limits"]["date_to"] == "2026-07-31"
    response = client.post("/api/demo/nodes", json=node_command(info["run_id"], gid="0000007"))
    assert response.status_code == 200
    created = response.json()
    assert created["node_count"] == 501 and created["select_gid"] == "0000007"
    assert created["transfer_count"] == 6000 and created["created_id"] == "0000007"
    assert response.headers["x-run-id"] == created["run_id"]
    assert response.headers["x-data-source"] == "fixtures"
    assert created["run_id"] != info["run_id"]
    assert {"gid": "0000007", "display_name": "Айдана Садыкова"} in client.get("/api/demo").json()["nodes"]
    node = client.get("/api/nodes/0000007").json()["node"]
    assert "display_name" not in node
    assert node["metrics"]["tx_in_count"] == 0 and node["role"] == "peripheral"
    assert node["role_score"] == 0 and node["assignment_status"] == "insufficient_evidence"
    transferred = client.post("/api/demo/transfers", json=transfer_command(created["run_id"], target="0000007"))
    assert transferred.status_code == 200
    assert transferred.json()["transfer_count"] == 6001
    assert transferred.json()["select_gid"] == "0007"
    assert transferred.headers["x-run-id"] == transferred.json()["run_id"]
    node = client.get("/api/nodes/0000007").json()["node"]
    assert node["metrics"]["in_amount_kzt"] == "5000.01" and node["metrics"]["tx_in_count"] == 1
    assert client.app.state.store.run_id == transferred.json()["run_id"]


@pytest.mark.parametrize("mode", ["fixtures", "artifacts", "empty"])
def test_ordinary_modes_are_read_only(make_client, run_dir, mode):
    client = make_client(fixture_mode=True) if mode == "fixtures" else make_client(run_dir if mode == "artifacts" else None)
    info = client.get("/api/demo").json()
    assert info["enabled"] is False
    assert info["nodes"] == [] and info["groups"] == [] and info["transfer_count"] is None
    assert info["node_count"] == (0 if mode == "empty" else 4)
    assert_demo_error(client.post("/api/demo/nodes", json=node_command(info["run_id"] or "absent")), 403, "demo_read_only", info["run_id"])
    assert_demo_error(client.post("/api/demo/transfers", json=transfer_command(info["run_id"] or "absent")), 403, "demo_read_only", info["run_id"])


def test_rejected_edits_leave_snapshot_unchanged(make_client, tmp_path):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    before = client.get("/api/demo").json()
    run_id = before["run_id"]
    assert_demo_error(client.post("/api/demo/nodes", json=node_command(run_id, gid="0007")), 409, "duplicate_node", run_id)
    assert_demo_error(client.post("/api/demo/nodes", json=node_command(run_id, group={"kind": "existing", "id": "missing"})), 404, "not_found", run_id)
    assert_demo_error(client.post("/api/demo/transfers", json=transfer_command(run_id, target="missing")), 404, "not_found", run_id)
    assert_demo_error(client.post("/api/demo/nodes", json=node_command("old-run")), 409, "stale_run", run_id)
    assert client.get("/api/demo").json() == before


@pytest.mark.parametrize("endpoint,fields", [
    ("nodes", {"display_name": ""}), ("nodes", {"display_name": "x\nY"}),
    ("nodes", {"gid": "a/b"}), ("nodes", {"quality": {"hop_depth": True}}),
    ("nodes", {"request_id": "not-a-uuid"}), ("nodes", {"unexpected": True}),
    ("transfers", {"date": "2026-08-01"}), ("transfers", {"amount_kzt": "4999.99"}),
    ("transfers", {"amount_kzt": 5000}), ("transfers", {"source": "7", "target": "7"}),
])
def test_invalid_demo_command_is_422(make_client, tmp_path, endpoint, fields):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    run_id = client.get("/api/health").json()["run_id"]
    command = node_command(run_id, **fields) if endpoint == "nodes" else transfer_command(run_id, **fields)
    assert_demo_error(client.post(f"/api/demo/{endpoint}", json=command), 422, "invalid_request", run_id)


def test_replay_uses_original_response_without_reverting_current_run(make_client, tmp_path):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    original = client.get("/api/health").json()["run_id"]
    command = node_command(original)
    first = client.post("/api/demo/nodes", json=command)
    second = client.post("/api/demo/nodes", json=node_command(first.json()["run_id"]))
    assert first.status_code == second.status_code == 200
    replay = client.post("/api/demo/nodes", json=command)
    assert replay.json() == first.json()
    assert replay.headers["x-run-id"] == first.json()["run_id"]
    current = second.json()["run_id"]
    assert client.get("/api/health").json()["run_id"] == current
    assert_demo_error(client.post("/api/demo/nodes", json={**command, "display_name": "Другое имя"}), 409, "request_id_conflict", current)


def test_failed_publication_returns_503_and_preserves_data(make_client, tmp_path, monkeypatch):
    from backend.app.demo.repository import DemoError

    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    before = client.get("/api/demo").json()

    def fail(_command):
        raise DemoError(503, "demo_write_failed", "Could not save demo revision.", before["run_id"])

    monkeypatch.setattr(client.app.state.demo_repository, "add_node", fail)
    assert_demo_error(client.post("/api/demo/nodes", json=node_command(before["run_id"])), 503, "demo_write_failed", before["run_id"])
    assert client.get("/api/demo").json() == before


def test_corrupt_demo_stays_unavailable(make_client, tmp_path):
    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "current.json").write_text("broken", encoding="utf-8")
    client = make_client(demo_mode=True, demo_dir=directory)
    health = client.get("/api/health")
    assert health.json()["data_ready"] is False
    assert health.headers["x-data-source"] == "unavailable"
    assert client.app.state.load_error
    assert client.get("/api/demo").json()["enabled"] is False
    assert_demo_error(client.post("/api/demo/nodes", json=node_command("unknown")), 503, "data_not_ready", None)
    assert (directory / "current.json").read_text(encoding="utf-8") == "broken"


def test_demo_exports_share_published_run(make_client, tmp_path):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    info = client.get("/api/demo").json()
    for name in ("nodes", "edges", "clusters", "priorities", "roles", "rules", "audit", "manifest", "node_names", "source", "transfers"):
        response = client.get(f"/api/exports/{name}")
        assert response.status_code == 200, name
        assert response.headers["x-run-id"] == info["run_id"]
    assert client.get("/api/exports/node_names").json() == info["nodes"]


def test_demo_configuration_is_explicit(monkeypatch, tmp_path):
    for name in ("AML_RUN_DIR", "AML_FIXTURE_MODE", "AML_DEMO_MODE", "AML_DEMO_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AML_DEMO_MODE", "true")
    monkeypatch.setenv("AML_DEMO_DIR", str(tmp_path / "persist"))
    settings = Settings.from_environment()
    assert settings.demo_mode is True and settings.demo_dir == tmp_path / "persist"
    assert replace(settings, demo_dir=None).demo_dir == PROJECT_ROOT / "data" / "demo"
    for fields in ({"fixture_mode": True}, {"run_dir": tmp_path}, {"demo_mode": False}):
        with pytest.raises(ValueError):
            replace(settings, **fields)
    monkeypatch.setenv("AML_DEMO_MODE", "sometimes")
    with pytest.raises(ValueError, match="AML_DEMO_MODE"):
        Settings.from_environment()


@pytest.mark.parametrize("arguments,expected_mode", [
    (["--demo"], "demo"), (["--fixtures"], "fixtures"), (["--run-dir", "/some/run"], "artifacts"),
])
def test_cli_explicit_mode_overrides_environment(monkeypatch, tmp_path, arguments, expected_mode):
    from backend import __main__ as cli

    monkeypatch.setenv("AML_FIXTURE_MODE", "false")
    monkeypatch.setenv("AML_DEMO_MODE", "true")
    monkeypatch.setenv("AML_DEMO_DIR", str(tmp_path / "demo"))
    monkeypatch.delenv("AML_RUN_DIR", raising=False)
    monkeypatch.setattr("sys.argv", ["backend", *arguments])
    captured = []
    monkeypatch.setattr(cli, "create_app", lambda settings: captured.append(settings))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: None)
    cli.main()
    settings = captured[0]
    assert settings.demo_mode == (expected_mode == "demo")
    assert settings.fixture_mode == (expected_mode == "fixtures")
    assert (settings.run_dir is not None) == (expected_mode == "artifacts")
    assert (settings.demo_dir is not None) == (expected_mode == "demo")


def test_cli_rejects_demo_directory_without_flag(monkeypatch, tmp_path):
    from backend import __main__ as cli

    monkeypatch.setattr("sys.argv", ["backend", "--demo-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2


def test_cli_demo_selects_environment_directory_before_loading_default_app(tmp_path):
    environment = os.environ | {
        "AML_DEMO_MODE": "false", "AML_FIXTURE_MODE": "true", "AML_RUN_DIR": "",
        "AML_DEMO_DIR": str(tmp_path / "demo"),
    }
    code = """
import sys
from backend import __main__ as cli
expected = sys.argv[1]
sys.argv = ['backend', '--demo']
def check(application, **kwargs):
    settings = application.state.settings
    assert settings.demo_mode and not settings.fixture_mode
    assert str(settings.demo_dir) == expected
cli.uvicorn.run = check
cli.main()
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "demo")], env=environment,
                            cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "demo").exists()
