"""Time evidence is observed calendar-day activity, with strict source semantics."""

from copy import deepcopy
from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.app.analytics.io import InputValidationError, load_inputs
from backend.app.analytics.temporal import build_temporal_features


@pytest.fixture
def dated_inputs(tmp_path):
    mapping = {
        "status": "approved", "dataset_kind": "synthetic",
        "money": {"unit": "KZT", "scale": 2},
        "amount_source": "transactions", "reconciliation": "error",
        "observation": {"start_date": "2026-07-01", "end_date": "2026-07-31", "date_semantics": "calendar_date", "minimum_transaction_kzt": "1.00"},
        "tables": {
            "nodes": {"file": "n.parquet", "gid_type": "string", "columns": {"gid": "gid"}},
            "edges": {"file": "e.parquet", "gid_type": "string", "kind": "aggregate", "columns": {"source": "src", "target": "dst", "amount": "kzt", "tx_count": "n"}},
            "transactions": {"file": "t.parquet", "gid_type": "string", "kind": "event", "columns": {"source": "src", "target": "dst", "amount": "kzt", "date": "day"}},
        },
    }
    data = {
        "nodes": {"gid": ["a", "b", "isolate"]},
        "edges": {"src": ["a", "b", "a"], "dst": ["b", "a", "a"], "kzt": ["3.00", "1.00", "1.00"], "n": [3, 1, 1]},
        "transactions": {"src": ["a", "a", "a", "b", "a"], "dst": ["b", "b", "b", "a", "a"], "kzt": ["1.00"] * 5, "day": [date(2026, 7, 1)] * 3 + [date(2026, 7, 31)] * 2},
    }

    def write(changes=None):
        current = deepcopy(data)
        if changes:
            current.update(changes)
        for name, columns in current.items():
            pq.write_table(pa.table(columns), tmp_path / mapping["tables"][name]["file"])
        return tmp_path, deepcopy(mapping)

    write.data = data
    return write


def test_days_repeats_and_isolates_are_preserved(dated_inputs):
    directory, mapping = dated_inputs()
    result = build_temporal_features(directory, mapping, "include")
    assert result["a"] == {"active_days": 2, "in_active_days": 1, "out_active_days": 2, "peak_daily_tx": 3, "first_date": "2026-07-01", "last_date": "2026-07-31"}
    assert result["b"] == {"active_days": 2, "in_active_days": 1, "out_active_days": 1, "peak_daily_tx": 3, "first_date": "2026-07-01", "last_date": "2026-07-31"}
    assert result["isolate"] == {"active_days": 0, "in_active_days": 0, "out_active_days": 0, "peak_daily_tx": 0, "first_date": None, "last_date": None}
    nodes, edges, audit = load_inputs(directory, mapping, "include")
    assert audit["tables"]["transactions"]["repeated_canonical_events_without_id"] == 2
    assert audit["tables"]["transactions"]["dates"]["validated_rows"] == 5
    assert all(any("2026-07" in reason for reason in node["quality"]["reasons"]) for node in nodes)
    assert all(any(">= 1.00 KZT" in reason for reason in node["quality"]["reasons"]) for node in nodes)


def test_self_transfer_policy_and_single_count(dated_inputs):
    data = deepcopy(dated_inputs.data)
    data["transactions"]["day"] = [date(2026, 7, 1)] * 5
    directory, mapping = dated_inputs(data)
    included = build_temporal_features(directory, mapping, "include")
    excluded = build_temporal_features(directory, mapping, "exclude")
    assert included["a"]["peak_daily_tx"] == 5
    assert excluded["a"]["peak_daily_tx"] == 4
    assert included["b"] == excluded["b"]


def test_selected_money_source_does_not_change_observed_time_features(dated_inputs):
    directory, mapping = dated_inputs()
    transactions = build_temporal_features(directory, mapping, "include")
    mapping["amount_source"] = "edges"
    assert transactions == build_temporal_features(directory, mapping, "include")


def test_missing_dates_produce_no_temporal_evidence(dated_inputs):
    directory, mapping = dated_inputs()
    del mapping["tables"]["transactions"]["columns"]["date"]
    del mapping["observation"]
    assert build_temporal_features(directory, mapping, "include") == {}


@pytest.mark.parametrize("value", [None, "2026-02-30", "2026-7-01", " 2026-07-01", 1234, datetime(2026, 7, 1), datetime(2026, 7, 1, tzinfo=timezone.utc)])
def test_invalid_calendar_values_are_rejected(dated_inputs, value):
    transactions = deepcopy(dated_inputs.data["transactions"])
    transactions["day"] = [value] * 5
    directory, mapping = dated_inputs({"transactions": transactions})
    with pytest.raises(InputValidationError, match=r"t.parquet: row 1, field date"):
        build_temporal_features(directory, mapping, "include")


@pytest.mark.parametrize("value", ["2026-06-30", "2026-08-01"])
def test_period_bounds_are_inclusive_but_outside_dates_fail(dated_inputs, value):
    transactions = deepcopy(dated_inputs.data["transactions"])
    transactions["day"] = [value] * 5
    directory, mapping = dated_inputs({"transactions": transactions})
    with pytest.raises(InputValidationError, match="outside the approved observation period"):
        load_inputs(directory, mapping, "include")


@pytest.mark.parametrize(("field", "value"), [("date_semantics", "utc"), ("start_date", None), ("start_date", "2026-08-01"), ("minimum_transaction_kzt", 1.001)])
def test_declared_date_semantics_and_filters_are_validated(dated_inputs, field, value):
    directory, mapping = dated_inputs()
    mapping["observation"][field] = value
    with pytest.raises(InputValidationError, match="input_mapping.observation"):
        load_inputs(directory, mapping, "include")


def test_transaction_minimum_is_enforced(dated_inputs):
    directory, mapping = dated_inputs()
    mapping["observation"]["minimum_transaction_kzt"] = "1.01"
    with pytest.raises(InputValidationError, match="below the declared minimum transaction filter"):
        load_inputs(directory, mapping, "include")


def test_dates_require_explicit_semantics(dated_inputs):
    directory, mapping = dated_inputs()
    del mapping["observation"]
    with pytest.raises(InputValidationError, match="date_semantics"):
        build_temporal_features(directory, mapping, "include")


def test_time_features_reject_inconsistent_aggregates(dated_inputs):
    data = deepcopy(dated_inputs.data)
    data["edges"]["kzt"][0] = "3.01"
    directory, mapping = dated_inputs(data)
    with pytest.raises(InputValidationError, match="directed pair.*differ"):
        build_temporal_features(directory, mapping, "include")


def test_repeated_event_definition_includes_date(dated_inputs):
    data = deepcopy(dated_inputs.data)
    data["transactions"]["day"] = ["2026-07-01", "2026-07-02", "2026-07-02", "2026-07-31", "2026-07-31"]
    directory, mapping = dated_inputs(data)
    _, _, audit = load_inputs(directory, mapping, "include")
    assert audit["tables"]["transactions"]["repeated_canonical_events_without_id"] == 1
    assert build_temporal_features(directory, mapping, "include")["a"]["active_days"] == 3
