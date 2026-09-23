"""Optional AI configuration must not prevent serving the local run."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.config import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AI_TEST_ENV = {
    "AML_FIXTURE_MODE": "true",
    "AML_RUN_DIR": "",
    "AML_FRONTEND_ORIGIN": "http://localhost:5173",
    "AML_FRONTEND_DIST": "",
    "AI_PROVIDER": "openai",
    "AI_MODEL": "synthetic-model",
    "OPENAI_API_KEY": "synthetic-key",
    "AI_MAX_TOOL_CALLS": "3",
}


@pytest.mark.parametrize("timeout", ["oops", "", "0", "-1", "31", "nan", "inf"])
def test_invalid_ai_timeout_keeps_app_import_and_local_api_available(timeout):
    # A fresh interpreter exercises the module-level app = create_app() too.
    script = """
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from backend.app.main import app

assert app.state.settings.ai_timeout_seconds == 30
with patch("backend.app.ai.provider.OpenAIProvider.respond", new_callable=AsyncMock) as provider:
    provider.side_effect = AssertionError("Invalid AI configuration must not reach a provider")
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200 and health.json()["data_ready"]
        run_id = health.json()["run_id"]
        card = client.get("/api/nodes/0007")
        assert card.status_code == 200
        assert client.get("/api/graph", params={"gid": "0007"}).status_code == 200
        assert client.get("/api/exports/roles").status_code == 200
        for endpoint in ("explain", "investigate"):
            body = {"run_id": run_id, "target": {"kind": "node", "id": "0007"}}
            if endpoint == "investigate":
                body["question"] = "Explain the observed data"
            response = client.post("/api/ai/" + endpoint, json=body)
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["status"] == "fallback"
            assert data["fallback_reason"] == "invalid_configuration"
            assert data["run_id"] == run_id
            assert card.json()["node"]["role_explanation"] in data["summary"]
    provider.assert_not_awaited()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env={**os.environ, **AI_TEST_ENV, "AI_TIMEOUT_SECONDS": timeout},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("timeout", ["30", "12.5", "0.05"])
def test_valid_ai_timeout_is_preserved(monkeypatch, timeout):
    for name, value in {**AI_TEST_ENV, "AI_TIMEOUT_SECONDS": timeout}.items():
        monkeypatch.setenv(name, value)
    assert Settings.from_environment().ai_timeout_seconds == float(timeout)
