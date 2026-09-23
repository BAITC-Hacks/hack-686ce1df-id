"""Synthetic-only Parquet examples; no production column assumptions."""

from copy import deepcopy
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.app.analytics.io import InputValidationError, inspect_schemas, load_inputs


@pytest.fixture
def inputs(tmp_path):
    mapping = {
        "status": "approved", "dataset_kind": "synthetic",
        "money": {"unit": "KZT", "scale": 2},
        "amount_source": "transactions", "reconciliation": "error",
        "tables": {
            "nodes": {"file": "n.parquet", "gid_type": "string", "columns": {"gid": "customer"}},
            "edges": {
                "file": "e.parquet", "gid_type": "string", "kind": "aggregate",
                "columns": {"source": "sender", "target": "receiver", "amount": "kzt", "tx_count": "n"},
            },
            "transactions": {
                "file": "t.parquet", "gid_type": "string", "kind": "event",
                "columns": {"source": "sender", "target": "receiver", "amount": "kzt"},
            },
        },
    }
    data = {
        "nodes": {"customer": ["0007", "7", "isolate"]},
        "edges": {"sender": ["0007"], "receiver": ["7"], "kzt": ["2.46"], "n": [2]},
        "transactions": {"sender": ["0007", "0007"], "receiver": ["7", "7"], "kzt": ["1.23", "1.23"]},
    }

    def write(changes=None, config=None):
        current = deepcopy(data)
        if changes:
            current.update(changes)
        chosen_mapping = deepcopy(mapping if config is None else config)
        for name, columns in current.items():
            pq.write_table(pa.table(columns), tmp_path / mapping["tables"][name]["file"])
        return tmp_path, chosen_mapping

    write.mapping = mapping
    return write


def test_schema_inspection_is_metadata_only(inputs, monkeypatch):
    directory, _ = inputs()

    def forbidden(*args, **kwargs):
        raise AssertionError("Row data must not be read during schema inspection")

    monkeypatch.setattr(pq, "read_table", forbidden)
    schema = inspect_schemas(directory)
    assert schema["files"]["n.parquet"]["row_count"] == 3
    assert schema["files"]["n.parquet"]["columns"] == [{"name": "customer", "type": "string", "nullable": True}]


def test_preserves_leading_zeros_isolates_and_identical_events(inputs):
    directory, mapping = inputs()
    nodes, edges, audit = load_inputs(directory, mapping, "include")
    assert [node["gid"] for node in nodes] == ["0007", "7", "isolate"]
    assert edges == [{"source": "0007", "target": "7", "amount_kzt": "2.46", "tx_count": 2}]
    assert audit["counts"]["isolated_nodes"] == 1
    assert audit["counts"]["weak_components"] == 2
    assert audit["tables"]["transactions"]["repeated_canonical_events_without_id"] == 1
    assert audit["tables"]["transactions"]["rows_removed"] == 0
    assert audit["reconciliation"]["status"] == "match"
    assert nodes[0]["quality"]["outbound_censored"] is None
    assert len(nodes[0]["quality"]["reasons"]) == 4


def test_unknown_source_column_stops_before_reading_rows(inputs, monkeypatch):
    directory, mapping = inputs()
    mapping["tables"]["transactions"]["columns"]["amount"] = "missing_money"
    monkeypatch.setattr(pq, "read_table", lambda *args, **kwargs: pytest.fail("Schema errors should precede row loading"))
    with pytest.raises(InputValidationError, match=r"t.parquet.*amount.*missing_money"):
        load_inputs(directory, mapping, "include")


@pytest.mark.parametrize("value", [1.23, -1, "NaN", "Infinity", "-0.01", "0.001", "wrong", None, True, " 1.23", "1_000.00", "1e1000000000", "0e-1000000000"])
def test_invalid_or_inexact_money_is_rejected(inputs, value):
    directory, mapping = inputs({"edges": {"sender": ["0007"], "receiver": ["7"], "kzt": [value], "n": [2]}})
    with pytest.raises(InputValidationError, match=r"e.parquet: row 1, field amount"):
        load_inputs(directory, mapping, "include")


def test_decimal_arithmetic_never_rounds_large_values(inputs):
    amount = Decimal("123456789012345678901234567890.12")
    directory, mapping = inputs({
        "edges": {"sender": ["0007"], "receiver": ["7"], "kzt": ["246913578024691357802469135780.24"], "n": [2]},
        "transactions": {"sender": ["0007", "0007"], "receiver": ["7", "7"], "kzt": [amount, amount]},
    })
    _, edges, _ = load_inputs(directory, mapping, "include")
    assert edges[0]["amount_kzt"] == "246913578024691357802469135780.24"


def test_extra_trailing_zeroes_do_not_require_rounding(inputs):
    directory, mapping = inputs({"transactions": {"sender": ["0007", "0007"], "receiver": ["7", "7"], "kzt": ["1.2300", "1.23"]}})
    _, edges, _ = load_inputs(directory, mapping, "include")
    assert edges[0]["amount_kzt"] == "2.46"


@pytest.mark.parametrize("value", [-1, 1.0, True, None, 0])
def test_counts_require_nonnegative_integer(inputs, value):
    directory, mapping = inputs({"edges": {"sender": ["0007"], "receiver": ["7"], "kzt": ["2.46"], "n": [value]}})
    with pytest.raises(InputValidationError, match="field tx_count"):
        load_inputs(directory, mapping, "include")


def test_duplicate_nodes_are_errors(inputs):
    directory, mapping = inputs({"nodes": {"customer": ["0007", "0007", "7"]}})
    with pytest.raises(InputValidationError, match=r"n.parquet: row 2.*duplicate.*0007.*row 1") as error:
        load_inputs(directory, mapping, "include")
    assert error.value.audit["status"] == "failed"
    assert error.value.audit["errors"]


def test_duplicate_ids_are_errors_not_deduplication(inputs):
    directory, mapping = inputs({"transactions": {"sender": ["0007", "0007"], "receiver": ["7", "7"], "kzt": ["1.23", "1.23"], "event": ["a", "a"]}})
    mapping["tables"]["transactions"]["columns"]["id"] = "event"
    with pytest.raises(InputValidationError, match=r"t.parquet: row 2.*duplicate ID.*row 1"):
        load_inputs(directory, mapping, "include")


@pytest.mark.parametrize("value", [1.0, True, None, ""])
def test_invalid_event_id(inputs, value):
    directory, mapping = inputs({"transactions": {"sender": ["0007", "0007"], "receiver": ["7", "7"], "kzt": ["1.23", "1.23"], "event": [value, value]}})
    mapping["tables"]["transactions"]["columns"]["id"] = "event"
    with pytest.raises(InputValidationError, match="field id"):
        load_inputs(directory, mapping, "include")


def test_aggregate_duplicate_pair_errors_but_event_pair_repeats_are_retained(inputs):
    directory, mapping = inputs({"edges": {"sender": ["0007", "0007"], "receiver": ["7", "7"], "kzt": ["1.23", "1.23"], "n": [1, 1]}})
    with pytest.raises(InputValidationError, match="duplicate aggregate directed pair"):
        load_inputs(directory, mapping, "include")
    mapping["tables"]["edges"]["kind"] = "event"
    del mapping["tables"]["edges"]["columns"]["tx_count"]
    _, edges, audit = load_inputs(directory, mapping, "include")
    assert edges[0]["amount_kzt"] == "2.46"
    assert audit["tables"]["edges"]["repeated_canonical_events_without_id"] == 1


@pytest.mark.parametrize("invalid_table", ["edges", "transactions"])
def test_even_the_unused_source_must_have_valid_endpoints(inputs, invalid_table):
    changes = {invalid_table: {"sender": ["missing"], "receiver": ["7"], "kzt": ["2.46"]}}
    if invalid_table == "edges":
        changes[invalid_table]["n"] = [1]
    directory, mapping = inputs(changes)
    mapping["amount_source"] = "transactions" if invalid_table == "edges" else "edges"
    with pytest.raises(InputValidationError, match=r"field source.*unknown node gid 'missing'"):
        load_inputs(directory, mapping, "include")


def test_reconciliation_is_audited_before_failure_and_source_choice_never_adds_tables(inputs):
    directory, mapping = inputs({"edges": {"sender": ["0007"], "receiver": ["7"], "kzt": ["5.00"], "n": [3]}})
    with pytest.raises(InputValidationError, match="1 directed pair") as error:
        load_inputs(directory, mapping, "include")
    mismatch = error.value.audit["reconciliation"]["differences"][0]
    assert mismatch["amount_difference_kzt"] == "2.54"
    assert mismatch["tx_count_difference"] == 1
    assert error.value.audit["counts"]["nodes"] == 3
    mapping["reconciliation"] = "report"
    _, edges, audit = load_inputs(directory, mapping, "include")
    assert edges[0]["amount_kzt"] == "2.46"
    assert audit["warnings"]
    mapping["amount_source"] = "edges"
    _, edges, _ = load_inputs(directory, mapping, "include")
    assert edges[0]["amount_kzt"] == "5.00"
    assert edges[0]["tx_count"] == 3


def test_self_transfer_policy_and_reconciliation_scope(inputs):
    directory, mapping = inputs({
        "edges": {"sender": ["0007", "0007"], "receiver": ["7", "0007"], "kzt": ["2.46", "10.00"], "n": [2, 1]},
        "transactions": {"sender": ["0007", "0007", "0007"], "receiver": ["7", "7", "0007"], "kzt": ["1.23", "1.23", "10.00"]},
    })
    _, included, included_audit = load_inputs(directory, mapping, "include")
    _, excluded, excluded_audit = load_inputs(directory, mapping, "exclude")
    assert len(included) == 2 and len(excluded) == 1
    assert included_audit["self_transfers"]["excluded"]["amount_kzt"] == "0.00"
    assert excluded_audit["self_transfers"]["excluded"] == {"directed_pairs": 1, "amount_kzt": "10.00", "tx_count": 1}
    assert included_audit["reconciliation"] == excluded_audit["reconciliation"]


def test_quality_only_uses_explicit_columns_no_seed_depth_inference(inputs):
    directory, mapping = inputs({"nodes": {"customer": ["0007", "7", "isolate"], "seed": [True, False, None], "depth": [0, 4, None], "censored": [False, True, None]}})
    mapping["tables"]["nodes"]["columns"].update({"is_seed": "seed", "hop_depth": "depth", "outbound_censored": "censored"})
    nodes, _, audit = load_inputs(directory, mapping, "include")
    assert nodes[0]["quality"]["inbound_incomplete"] is None
    assert nodes[1]["quality"]["outbound_censored"] is True
    assert nodes[2]["quality"]["hop_depth"] is None
    assert audit["quality"]["is_seed"] == {"true": 1, "false": 1, "unknown": 1}
    assert audit["quality_sources"]["hop_depth"]["inferred"] is False
    assert audit["brief_comparison"]["seed_nodes"]["actual"] is None
    assert audit["brief_comparison"]["seed_without_outgoing"]["matches"] is None


def test_brief_quality_comparison_only_with_fully_known_flags(inputs):
    directory, mapping = inputs({"nodes": {"customer": ["0007", "7", "isolate"], "seed": [True, False, True], "censored": [False, True, True]}})
    mapping["tables"]["nodes"]["columns"].update({"is_seed": "seed", "outbound_censored": "censored"})
    _, _, audit = load_inputs(directory, mapping, "include")
    assert audit["brief_comparison"]["seed_nodes"]["actual"] == 2
    assert audit["brief_comparison"]["outbound_censored_nodes"]["actual"] == 2
    assert audit["brief_comparison"]["seed_without_outgoing"]["actual"] == 1


def test_integer_identifiers_require_explicit_mapping(inputs):
    directory, mapping = inputs({
        "nodes": {"customer": [7, 8, 9]},
        "edges": {"sender": [7], "receiver": [8], "kzt": ["2.46"], "n": [2]},
        "transactions": {"sender": [7, 7], "receiver": [8, 8], "kzt": ["1.23", "1.23"]},
    })
    with pytest.raises(InputValidationError, match="required string identifier"):
        load_inputs(directory, mapping, "include")
    for table in mapping["tables"].values():
        table["gid_type"] = "integer"
    nodes, edges, _ = load_inputs(directory, mapping, "include")
    assert nodes[0]["gid"] == "7" and edges[0]["target"] == "8"


@pytest.mark.parametrize("value", [7.0, True, None, "", " 0007", "7 "])
def test_required_gid_never_coerces_float_bool_or_null(inputs, value):
    directory, mapping = inputs({"nodes": {"customer": [value]}})
    with pytest.raises(InputValidationError, match="field gid"):
        load_inputs(directory, mapping, "include")


def test_mapping_rejects_unapproved_and_unknown_fields(inputs):
    directory, mapping = inputs()
    mapping["status"] = "draft"
    with pytest.raises(InputValidationError, match="approved"):
        load_inputs(directory, mapping, "include")
    mapping["status"] = "approved"
    mapping["tables"]["nodes"]["columns"]["guess"] = "customer"
    with pytest.raises(InputValidationError, match="unknown canonical mapping field.*guess"):
        load_inputs(directory, mapping, "include")


@pytest.mark.parametrize("path", [
    ("dataset_kind",), ("amount_source",), ("reconciliation",),
    ("tables", "nodes", "gid_type"), ("tables", "edges", "kind"),
])
def test_wrong_json_config_type_has_input_validation_error(inputs, path):
    directory, mapping = inputs()
    current = mapping
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = []
    with pytest.raises(InputValidationError):
        load_inputs(directory, mapping, "include")


def test_empty_parquet_tables_are_valid_and_audited(inputs):
    directory, mapping = inputs({
        "nodes": {"customer": []},
        "edges": {"sender": [], "receiver": [], "kzt": [], "n": []},
        "transactions": {"sender": [], "receiver": [], "kzt": []},
    })
    nodes, edges, audit = load_inputs(directory, mapping, "include")
    assert nodes == edges == []
    assert audit["counts"]["weak_components"] == 0


def test_missing_input_directory_has_human_error(tmp_path):
    with pytest.raises(InputValidationError, match="Input directory does not exist"):
        inspect_schemas(tmp_path / "missing")


def test_explicit_float_policy_preserves_decimal_cents_and_input_bytes(inputs):
    directory, mapping = inputs({
        "edges": {"sender": ["0007"], "receiver": ["7"], "kzt": [0.3], "n": [2]},
        "transactions": {"sender": ["0007", "0007"], "receiver": ["7", "7"], "kzt": [0.1, 0.2]},
    })
    before = {path.name: path.read_bytes() for path in directory.glob("*.parquet")}
    mapping["money"]["float_policy"] = "decimal_string"
    _, edges, audit = load_inputs(directory, mapping, "include")
    assert edges[0]["amount_kzt"] == "0.30"
    assert audit["reconciliation"]["status"] == "match"
    assert audit["money"]["float_policy"] == "decimal_string"
    assert before == {path.name: path.read_bytes() for path in directory.glob("*.parquet")}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.01, 0.001, 0.1 + 0.2, float(2**53), float(2**46)])
def test_float_policy_never_rounds_or_accepts_unsafe_precision(inputs, value):
    directory, mapping = inputs({"edges": {"sender": ["0007"], "receiver": ["7"], "kzt": [value], "n": [2]}})
    mapping["money"]["float_policy"] = "decimal_string"
    with pytest.raises(InputValidationError, match=r"e.parquet: row 1, field amount"):
        load_inputs(directory, mapping, "include")


@pytest.mark.parametrize("policy", ["round", "allow", [], None])
def test_invalid_float_policy_is_rejected(inputs, policy):
    directory, mapping = inputs()
    mapping["money"]["float_policy"] = policy
    with pytest.raises(InputValidationError, match="float_policy"):
        load_inputs(directory, mapping, "include")


def _reviewed_traversal(mapping, max_depth=1):
    mapping["traversal"] = {"strategy": "outgoing_bfs", "max_depth": max_depth, "source": "synthetic collection documentation"}
    mapping["tables"]["nodes"]["columns"].update({"is_seed": "seed", "hop_depth": "depth"})


def test_reviewed_traversal_distinguishes_boundary_and_unknown_incoming(inputs):
    directory, mapping = inputs({"nodes": {"customer": ["0007", "7", "isolate"], "seed": [True, False, True], "depth": [0, 1, 0]}})
    _reviewed_traversal(mapping)
    nodes, _, audit = load_inputs(directory, mapping, "include")
    qualities = {node["gid"]: node["quality"] for node in nodes}
    assert qualities["0007"]["outbound_censored"] is False
    assert qualities["0007"]["inbound_incomplete"] is True
    assert qualities["7"]["outbound_censored"] is True
    assert qualities["7"]["inbound_incomplete"] is None
    assert audit["quality_sources"]["outbound_censored"]["inferred"] is True
    assert audit["quality_sources"]["hop_depth"]["inferred"] is False
    assert audit["counts"]["isolated_nodes"] == 1
    assert "неизвестна" in " ".join(qualities["7"]["reasons"])


@pytest.mark.parametrize(("seeds", "depths", "message"), [
    ([False, False, True], [0, 1, 0], "is_seed"),
    ([True, True, True], [0, 1, 0], "is_seed"),
    ([True, False, True], [0, 2, 0], "max_depth"),
    ([True, False, True], [0, None, 0], "non-null"),
    ([True, None, True], [0, 1, 0], "non-null"),
    ([True, False, False], [0, 1, 1], "observed outgoing BFS distance"),
])
def test_reviewed_traversal_rejects_inconsistent_node_depths(inputs, seeds, depths, message):
    directory, mapping = inputs({"nodes": {"customer": ["0007", "7", "isolate"], "seed": seeds, "depth": depths}})
    _reviewed_traversal(mapping)
    with pytest.raises(InputValidationError, match=message):
        load_inputs(directory, mapping, "include")


@pytest.mark.parametrize(("field", "value"), [("strategy", "incoming_bfs"), ("max_depth", 0), ("max_depth", True), ("max_depth", 1.0), ("source", "")])
def test_invalid_traversal_metadata_is_rejected(inputs, field, value):
    directory, mapping = inputs({"nodes": {"customer": ["0007", "7", "isolate"], "seed": [True, False, True], "depth": [0, 1, 0]}})
    _reviewed_traversal(mapping)
    mapping["traversal"][field] = value
    with pytest.raises(InputValidationError, match=f"traversal.{field}"):
        load_inputs(directory, mapping, "include")


def test_reviewed_traversal_rejects_outgoing_boundary_pair(inputs):
    directory, mapping = inputs({
        "nodes": {"customer": ["0007", "7", "isolate"], "seed": [False, True, True], "depth": [1, 0, 0]},
    })
    _reviewed_traversal(mapping)
    with pytest.raises(InputValidationError, match="outgoing pair from boundary"):
        load_inputs(directory, mapping, "include")


def test_reviewed_traversal_rejects_shortcut_depth_and_conflicting_quality(inputs):
    directory, mapping = inputs({"nodes": {"customer": ["0007", "7", "isolate"], "seed": [True, False, True], "depth": [0, 2, 0], "censored": [True, True, False]}})
    _reviewed_traversal(mapping, max_depth=2)
    with pytest.raises(InputValidationError, match="minimum BFS hop_depth"):
        load_inputs(directory, mapping, "include")
    mapping["tables"]["nodes"]["columns"]["outbound_censored"] = "censored"
    with pytest.raises(InputValidationError, match="explicit flag conflicts"):
        load_inputs(directory, mapping, "include")
