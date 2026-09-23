"""Integration tests use generated synthetic parquet, never production data."""

from decimal import Decimal
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.app.analytics import cli


@pytest.fixture
def source(tmp_path):
    raw = tmp_path / "synthetic"
    raw.mkdir()
    pq.write_table(pa.Table.from_pylist([
        {"client": "0007"}, {"client": "7"}, {"client": "isolated"},
    ]), raw / "clients.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"sender": "0007", "receiver": "7", "value": Decimal("100.00"), "n": 2},
    ]), raw / "links.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"sender": "0007", "receiver": "7", "value": Decimal("30.00")},
        {"sender": "0007", "receiver": "7", "value": Decimal("70.00")},
    ]), raw / "events.parquet")
    mapping = {
        "status": "approved", "dataset_kind": "synthetic",
        "money": {"unit": "KZT", "scale": 2},
        "amount_source": "transactions", "reconciliation": "error",
        "tables": {
            "nodes": {"file": "clients.parquet", "gid_type": "string",
                      "columns": {"gid": "client"}},
            "edges": {"file": "links.parquet", "gid_type": "string", "kind": "aggregate",
                      "columns": {"source": "sender", "target": "receiver", "amount": "value", "tx_count": "n"}},
            "transactions": {"file": "events.parquet", "gid_type": "string", "kind": "event",
                             "columns": {"source": "sender", "target": "receiver", "amount": "value"}},
        },
    }
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    return raw, mapping_path, mapping


def test_inspect_reads_only_metadata(source, monkeypatch, capsys):
    raw, _, _ = source

    def forbidden(*args, **kwargs):
        raise AssertionError("Schema inspection must not read rows")

    monkeypatch.setattr(pq, "read_table", forbidden)
    assert cli.main(["inspect", "--input-dir", str(raw)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "schema_only"
    assert report["files"]["events.parquet"]["row_count"] == 2


def test_audit_publishes_metrics_and_never_claims_complete(source, tmp_path, capsys):
    raw, mapping_path, _ = source
    output = tmp_path / "audit.json"
    assert cli.main(["audit", "--input-dir", str(raw), "--mapping", str(mapping_path),
                     "--self-transfers", "include", "--output", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    report = json.loads(output.read_text())
    assert summary["elapsed_seconds"] >= report["elapsed_seconds_before_report"]
    assert report["status"] == "audit_only"
    assert report["dataset_kind"] == "synthetic"
    assert report["audit"]["reconciliation"]["status"] == "match"
    assert report["audit"]["counts"]["isolated_nodes"] == 1
    assert len(report["input_hashes"]) == 3
    nodes = {node["gid"]: node for node in report["observed_nodes"]}
    assert set(nodes) == {"0007", "7", "isolated"}
    assert nodes["0007"]["metrics"]["out_amount_kzt"] == "100.00"
    assert nodes["0007"]["metrics"]["out_in_ratio"] is None
    assert nodes["7"]["quality"]["outbound_censored"] is None
    assert "role" not in nodes["0007"]
    assert not (tmp_path / "manifest.json").exists()


def test_reconciliation_error_persists_diagnostics(source, tmp_path, capsys):
    raw, mapping_path, _ = source
    pq.write_table(pa.Table.from_pylist([
        {"sender": "0007", "receiver": "7", "value": Decimal("99.00"), "n": 2},
    ]), raw / "links.parquet")
    output = tmp_path / "failed-audit.json"
    assert cli.main(["audit", "--input-dir", str(raw), "--mapping", str(mapping_path),
                     "--self-transfers", "include", "--output", str(output)]) == 2
    failure = json.loads(output.read_text())
    assert failure["status"] == "failed"
    assert failure["audit"]["reconciliation"]["mismatch_count"] == 1
    assert json.loads(capsys.readouterr().err)["status"] == "failed"


def test_existing_output_is_never_replaced(source, tmp_path, capsys):
    raw, _, _ = source
    output = tmp_path / "existing.json"
    output.write_text("user content", encoding="utf-8")
    assert cli.main(["inspect", "--input-dir", str(raw), "--output", str(output)]) == 2
    assert output.read_text() == "user content"
    assert json.loads(capsys.readouterr().err)["status"] == "failed"
    assert list(tmp_path.glob(".existing.json.*")) == []


def test_unapproved_mapping_stops_before_row_reads(source, monkeypatch, capsys):
    raw, mapping_path, mapping = source
    mapping["status"] = "pending_real_schema"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("Unapproved mapping must not read rows")

    monkeypatch.setattr(pq, "read_table", forbidden)
    assert cli.main(["audit", "--input-dir", str(raw), "--mapping", str(mapping_path),
                     "--self-transfers", "include"]) == 2
    assert "approved" in json.loads(capsys.readouterr().err)["error"]


@pytest.mark.parametrize("text", ['{"status":"approved","status":"pending"}', '{"scale":NaN}'])
def test_malformed_json_config_is_rejected(tmp_path, text):
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        cli._read_json(mapping_path)


def test_input_change_during_audit_is_rejected(source, monkeypatch):
    raw, mapping_path, _ = source
    original_load = cli.load_inputs

    def load_then_change(*args):
        result = original_load(*args)
        with (raw / "events.parquet").open("ab") as stream:
            stream.write(b"changed")
        return result

    monkeypatch.setattr(cli, "load_inputs", load_then_change)
    with pytest.raises(cli.InputValidationError, match="изменился"):
        cli.audit_inputs(raw, mapping_path, "include")


def test_audit_repeats_identically_except_timing(source):
    raw, mapping_path, _ = source
    first = cli.audit_inputs(raw, mapping_path, "include")
    second = cli.audit_inputs(raw, mapping_path, "include")
    first.pop("elapsed_seconds_before_report")
    second.pop("elapsed_seconds_before_report")
    assert first == second
