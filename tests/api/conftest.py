"""Shared clients and artifact writers for the public API contract tests."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.store import ResultStore


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "contract-v1"
RUN_ID = "fixture-contract-v1"
ARTIFACT_RUN_ID = "test-artifacts-run"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def mark_artifact_loading_test(run_dir: Path) -> None:
    """Use a separate test run identity while retaining all artificial-data warnings."""
    for name in ("manifest.json", "ai_response.json"):
        data = read_json(run_dir / name)
        data["run_id"] = ARTIFACT_RUN_ID
        write_json(run_dir / name, data)


def write_csvs(run_dir: Path, nodes: list[dict]) -> None:
    """Write supplied scores unchanged; this helper does not calculate analytics."""
    priorities = sorted(nodes, key=lambda node: (-node["priority_score"], node["gid"]))
    with (run_dir / "priorities.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rank", "gid", "role", "role_score", "priority_score", "priority_explanation"
            ],
        )
        writer.writeheader()
        for rank, node in enumerate(priorities, start=1):
            writer.writerow({"rank": rank, **{key: node[key] for key in writer.fieldnames[1:]}})
    with (run_dir / "roles.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["gid", "role", "role_score", "assignment_status", "role_explanation"],
        )
        writer.writeheader()
        writer.writerows({key: node[key] for key in writer.fieldnames} for node in nodes)


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def fixture_data() -> dict:
    return {name: read_json(FIXTURE_DIR / f"{name}.json") for name in ("nodes", "edges", "clusters")}


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    destination = tmp_path / "run"
    shutil.copytree(FIXTURE_DIR, destination)
    mark_artifact_loading_test(destination)
    return destination


@pytest.fixture
def client():
    store = ResultStore.from_directory(FIXTURE_DIR)
    application = create_app(Settings(fixture_mode=True), store=store)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def make_client():
    opened: list[TestClient] = []

    def factory(run_dir: Path | None = None, **settings):
        application = create_app(Settings(run_dir=run_dir, **settings))
        test_client = TestClient(application)
        test_client.__enter__()
        opened.append(test_client)
        return test_client

    yield factory
    for test_client in reversed(opened):
        test_client.__exit__(None, None, None)


@pytest.fixture
def write_run(tmp_path: Path):
    """Build complete synthetic result sets with arbitrary explicitly supplied nodes."""
    counter = 0

    def factory(nodes: list[dict], edges: list[dict], clusters: list[dict]) -> Path:
        nonlocal counter
        counter += 1
        destination = tmp_path / f"synthetic-{counter}"
        shutil.copytree(FIXTURE_DIR, destination)
        mark_artifact_loading_test(destination)
        for name, records in (("nodes", nodes), ("edges", edges), ("clusters", clusters)):
            write_json(destination / f"{name}.json", records)
        write_csvs(destination, nodes)
        manifest = read_json(destination / "manifest.json")
        manifest["counts"] = {"nodes": len(nodes), "edges": len(edges), "clusters": len(clusters)}
        manifest["rules_hash"] = hashlib.sha256((destination / "rules.json").read_bytes()).hexdigest()
        write_json(destination / "manifest.json", manifest)
        return destination

    return factory


@pytest.fixture
def node_template(fixture_data) -> dict:
    return deepcopy(next(node for node in fixture_data["nodes"] if node["gid"] == "isolated"))
