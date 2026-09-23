"""Independent synthetic cases for publication and task export acceptance."""

import csv
import hashlib
import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.app.analytics import cli, pipeline
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.store import ResultStore


@pytest.fixture
def calculation(tmp_path):
    raw = tmp_path / "input"
    raw.mkdir()
    nodes = [{"gid": "0007", "seed": True, "depth": 0, "censored": False},
             {"gid": "isolated", "seed": True, "depth": 0, "censored": False}]
    nodes += [{"gid": str(i), "seed": False, "depth": 1, "censored": False} for i in range(1, 23)]
    edges = [{"src": "0007", "dst": str(i), "amount": f"{i * 100}.01", "count": 1} for i in range(1, 23)]
    transactions = [{key: edge[key] for key in ("src", "dst", "amount")} for edge in edges]
    for name, rows in (("nodes", nodes), ("edges", edges), ("transactions", transactions)):
        pq.write_table(pa.Table.from_pylist(rows), raw / f"{name}.parquet")
    mapping = {
        "status": "approved", "dataset_kind": "synthetic", "money": {"unit": "KZT", "scale": 2},
        "amount_source": "transactions", "reconciliation": "error",
        "tables": {
            "nodes": {"file": "nodes.parquet", "gid_type": "string",
                      "columns": {"gid": "gid", "is_seed": "seed", "hop_depth": "depth", "outbound_censored": "censored"}},
            "edges": {"file": "edges.parquet", "gid_type": "string", "kind": "aggregate",
                      "columns": {"source": "src", "target": "dst", "amount": "amount", "tx_count": "count"}},
            "transactions": {"file": "transactions.parquet", "gid_type": "string", "kind": "event",
                             "columns": {"source": "src", "target": "dst", "amount": "amount"}},
        },
    }
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps(mapping))
    rules_path = tmp_path / "rules.json"
    rules_path.write_bytes((Path(__file__).resolve().parents[2] / "config/rules.json").read_bytes())
    return raw, tmp_path / "runs", rules_path, mapping_path


def _csv(path):
    return list(csv.DictReader(io.StringIO(path.read_text())))


def test_complete_pipeline_preserves_all_nodes_and_exports(calculation):
    manifest = pipeline.run_pipeline(*calculation)
    directory = calculation[1] / manifest.run_id
    store = ResultStore.from_directory(directory)
    assert manifest.counts == {"nodes": 24, "edges": 22, "clusters": manifest.counts["clusters"],
                               "transactions": 22, "components": 2, "isolated_nodes": 1,
                               "seeds": 2, "boundary_nodes": 0}
    assert store.get_node("0007").metrics.out_amount_kzt == "25300.22"
    assert store.get_node("7").gid != store.get_node("0007").gid
    assert store.get_node("isolated").assignment_status == "insufficient_evidence"
    assert store.get_graph(gid="isolated").shown_nodes == 1
    role_rows = _csv(directory / "nodes_roles.csv")
    assert len(role_rows) == 24
    assert len({row["gid"] for row in role_rows}) == 24
    assert all(all(row.values()) and len(row["evidence"]) <= 200 for row in role_rows)
    assert all(float(row["role_score"]) == store.get_node(row["gid"]).role_score / 100 for row in role_rows)
    assert all(float(row["priority_score"]) == store.get_node(row["gid"]).priority_score / 100 for row in role_rows)
    assert all(any(char.isdigit() for char in row["evidence"]) for row in role_rows)
    top = _csv(directory / "top_nodes.csv")
    assert len(top) == len({row["gid"] for row in top}) == 20
    assert [row["gid"] for row in top] == [node.gid for node in store.list_priorities()]
    assert all(f"Приоритет={float(row['priority_score']):.6f} (0–1)" in row["why"] for row in top)
    clusters = _csv(directory / "clusters.csv")
    assert {row["cluster_id"] for row in role_rows} == {row["cluster_id"] for row in clusters}
    assert sum(int(row["n_nodes"]) for row in clusters) == 24
    for key, name in manifest.files.items():
        assert store.get_export(key).content == (directory / name).read_bytes()
    audit = json.loads((directory / "audit.json").read_bytes())
    assert audit["reconciliation"]["mismatch_count"] == 0
    for name, digest in audit["artifact_hashes"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest


def test_fresh_recalculations_are_deterministic_and_existing_run_is_immutable(calculation, tmp_path):
    first = pipeline.run_pipeline(*calculation)
    second_output = tmp_path / "second"
    second = pipeline.run_pipeline(calculation[0], second_output, *calculation[2:])
    assert first.run_id == second.run_id
    first_dir = calculation[1] / first.run_id
    original = (first_dir / "manifest.json").read_bytes()
    for name in set(first.files.values()) - {"manifest.json"}:
        assert (first_dir / name).read_bytes() == (second_output / second.run_id / name).read_bytes()
    pipeline.run_pipeline(*calculation)
    assert (first_dir / "manifest.json").read_bytes() == original
    (first_dir / "nodes_roles.csv").write_text("user-edited file")
    with pytest.raises(ValueError, match="differs"):
        pipeline.run_pipeline(*calculation)
    assert (first_dir / "nodes_roles.csv").read_text() == "user-edited file"
    assert not list(calculation[1].glob(".calculating-*"))


def test_changed_configuration_changes_run_identity(calculation):
    first = pipeline.run_pipeline(*calculation)
    with calculation[2].open("ab") as stream:
        stream.write(b"\n")
    second = pipeline.run_pipeline(*calculation)
    assert first.run_id != second.run_id


def test_extra_parquet_schema_is_bound_to_run_identity(calculation):
    first = pipeline.run_pipeline(*calculation)
    pq.write_table(pa.table({"extra": [1]}), calculation[0] / "unmapped.parquet")
    second = pipeline.run_pipeline(*calculation)
    assert first.run_id != second.run_id


def test_mismatched_input_never_publishes(calculation):
    path = calculation[0] / "edges.parquet"
    rows = pq.read_table(path).to_pylist()
    rows[0]["amount"] = "99.99"
    pq.write_table(pa.Table.from_pylist(rows), path)
    with pytest.raises(ValueError):
        pipeline.run_pipeline(*calculation)
    assert not calculation[1].exists()


def test_input_change_during_calculation_never_publishes(calculation, monkeypatch):
    original = pipeline.derive_records

    def changed(*args):
        records = original(*args)
        with (calculation[0] / "nodes.parquet").open("ab") as stream:
            stream.write(b"changed")
        return records

    monkeypatch.setattr(pipeline, "derive_records", changed)
    with pytest.raises(ValueError, match="changed"):
        pipeline.run_pipeline(*calculation)
    assert not calculation[1].exists()


def test_failed_validation_cleans_staging(calculation, monkeypatch):
    def fail(*args):
        raise ValueError("injected validation failure")
    monkeypatch.setattr(ResultStore, "from_directory", fail)
    with pytest.raises(ValueError, match="injected"):
        pipeline.run_pipeline(*calculation)
    assert list(calculation[1].iterdir()) == []


def test_cli_reports_full_time_and_api_serves_computed_results(calculation, capsys, monkeypatch):
    raw, output, rules, mapping = calculation
    assert cli.main(["run", "--input-dir", str(raw), "--output-dir", str(output),
                     "--rules", str(rules), "--mapping", str(mapping)]) == 0
    summary = json.loads(capsys.readouterr().out)
    directory = Path(summary["run_dir"])
    manifest = json.loads((directory / "manifest.json").read_bytes())
    assert summary["elapsed_seconds"] >= manifest["elapsed_seconds"]
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("AI_MODEL", "")
    with TestClient(create_app(Settings(run_dir=directory))) as client:
        health = client.get("/api/health")
        assert health.json()["data_ready"] is True
        assert health.headers["X-Data-Source"] == "artifacts"
        assert client.get("/api/nodes/0007").json()["node"]["metrics"]["out_amount_kzt"] == "25300.22"
        assert client.get("/api/graph", params={"gid": "isolated"}).json()["shown_nodes"] == 1
        assert client.get("/api/nodes/does-not-exist").status_code == 404
        for name in manifest["files"]:
            assert client.get(f"/api/exports/{name}").status_code == 200
        response = client.post("/api/ai/explain", json={"run_id": summary["run_id"], "target": {"kind": "node", "id": "0007"}})
        assert response.status_code == 200
        assert response.json()["status"] == "fallback"


def test_cli_failure_is_structured(calculation, capsys):
    assert cli.main(["run", "--input-dir", str(calculation[0] / "missing"),
                     "--rules", str(calculation[2]), "--mapping", str(calculation[3])]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "failed"
