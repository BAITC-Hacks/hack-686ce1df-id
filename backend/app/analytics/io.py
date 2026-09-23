"""Strict Parquet input, with a reviewed mapping and exact monetary values.

No production column names or traversal semantics are assumed here. Reading
schemas is possible before an input mapping is approved.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any

import pyarrow.parquet as pq

from .audit import graph_counts, money_string, pair_totals, quality_summary, reconcile_pairs


_DECIMAL_STRING = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_MAX_MONEY_DIGITS = 1000  # Representation/resource bound, never an analytical threshold.


class InputValidationError(ValueError):
    """A failed input check with the available audit attached for persistence."""

    def __init__(self, message: str, audit: dict | None = None):
        super().__init__(message)
        self.audit = audit if audit is not None else {"errors": [message]}


def inspect_schemas(input_dir: Path) -> dict:
    """Read metadata of top-level *.parquet files; never load row data."""
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise InputValidationError(f"Input directory does not exist: {input_dir}")
    result: dict[str, Any] = {"files": {}, "errors": []}
    for path in sorted(input_dir.glob("*.parquet")):
        if not path.is_file():
            continue
        try:
            parquet = pq.ParquetFile(path)
            try:
                result["files"][path.name] = {
                    "row_count": parquet.metadata.num_rows,
                    "row_groups": parquet.metadata.num_row_groups,
                    "columns": [
                        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
                        for field in parquet.schema_arrow
                    ],
                }
            finally:
                parquet.close()
        except Exception as exc:
            message = f"{path.name}: cannot read Parquet schema: {exc}"
            result["errors"].append(message)
            raise InputValidationError(message, result) from exc
    return result


def _fail(audit: dict, message: str) -> None:
    audit["status"] = "failed"
    audit["errors"].append(message)
    raise InputValidationError(message, audit)


def _mapping_tables(mapping: dict, schemas: dict, audit: dict) -> dict:
    if not isinstance(mapping, dict):
        _fail(audit, "input_mapping: expected a JSON object")
    if mapping.get("status") != "approved":
        _fail(audit, "input_mapping.status: must be 'approved' after schema review")
    if mapping.get("dataset_kind") not in ("real", "synthetic"):
        _fail(audit, "input_mapping.dataset_kind: must be 'real' or 'synthetic'")
    money = mapping.get("money")
    if not isinstance(money, dict) or money.get("unit") != "KZT" or type(money.get("scale")) is not int or money.get("scale") != 2:
        _fail(audit, "input_mapping.money: supported exact representation is {'unit': 'KZT', 'scale': 2}")
    if money.get("float_policy", "reject") != "reject":
        _fail(audit, "input_mapping.money.float_policy: only 'reject' is supported; map exact decimal/integer/string source values")
    if mapping.get("amount_source") not in ("edges", "transactions"):
        _fail(audit, "input_mapping.amount_source: explicitly choose 'edges' or 'transactions'")
    if mapping.get("reconciliation") not in ("error", "report"):
        _fail(audit, "input_mapping.reconciliation: explicitly choose 'error' or 'report'")
    tables = mapping.get("tables")
    if not isinstance(tables, dict) or set(tables) != {"nodes", "edges", "transactions"}:
        _fail(audit, "input_mapping.tables: exactly nodes, edges, transactions are required")
    used_files = set()
    for name in ("nodes", "edges", "transactions"):
        table = tables[name]
        if not isinstance(table, dict):
            _fail(audit, f"input_mapping.tables.{name}: expected an object")
        filename = table.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".parquet"):
            _fail(audit, f"input_mapping.tables.{name}.file: expected a top-level .parquet filename")
        if filename in used_files:
            _fail(audit, f"{filename}: each logical table must reference a different Parquet file")
        used_files.add(filename)
        if filename not in schemas["files"]:
            _fail(audit, f"{filename}: mapped Parquet file does not exist in the input directory")
        if table.get("gid_type") not in ("string", "integer"):
            _fail(audit, f"{filename}: gid_type must explicitly be 'string' or 'integer'")
        columns = table.get("columns")
        if not isinstance(columns, dict):
            _fail(audit, f"{filename}: columns must be a canonical-name to source-name object")
        if name == "nodes":
            required = {"gid"}
            allowed = required | {"is_seed", "hop_depth", "outbound_censored", "inbound_incomplete"}
        else:
            kind = table.get("kind")
            if kind not in ("aggregate", "event") or (name == "transactions" and kind != "event"):
                _fail(audit, f"{filename}: edges.kind must be aggregate/event; transactions.kind must be event")
            required = {"source", "target", "amount"}
            if kind == "aggregate":
                required.add("tx_count")
            allowed = required | {"id"}
        if required - columns.keys():
            _fail(audit, f"{filename}: missing canonical mapping field(s): {', '.join(sorted(required - columns.keys()))}")
        if columns.keys() - allowed:
            _fail(audit, f"{filename}: unknown canonical mapping field(s): {', '.join(sorted(columns.keys() - allowed))}")
        if any(not isinstance(column, str) or not column for column in columns.values()):
            _fail(audit, f"{filename}: every mapped source column must be a nonempty string")
        if len(set(columns.values())) != len(columns):
            _fail(audit, f"{filename}: source columns cannot map to multiple canonical fields")
        available = [column["name"] for column in schemas["files"][filename]["columns"]]
        if len(set(available)) != len(available):
            _fail(audit, f"{filename}: duplicate source column names are ambiguous")
        for canonical, source_column in columns.items():
            if source_column not in available:
                _fail(audit, f"{filename}: field '{canonical}' maps to missing source column '{source_column}'")
    return tables


def _gid(value: Any, expected: str, where: str, audit: dict) -> str:
    if expected == "string" and isinstance(value, str) and value and value.strip() == value:
        return value
    if expected == "integer" and type(value) is int:
        return str(value)
    _fail(audit, f"{where}: required {expected} identifier without surrounding whitespace; received {value!r} ({type(value).__name__}); float/bool coercion is forbidden")


def _count(value: Any, where: str, audit: dict) -> int:
    if type(value) is not int or value < 0:
        _fail(audit, f"{where}: expected a nonnegative integer; received {value!r}")
    return value


def _amount(value: Any, where: str, audit: dict) -> int:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (str, int, Decimal)):
        _fail(audit, f"{where}: money must be exact decimal, integer KZT, or decimal string; received {value!r} ({type(value).__name__}); binary floats are rejected")
    if isinstance(value, str) and not _DECIMAL_STRING.fullmatch(value):
        _fail(audit, f"{where}: invalid decimal string {value!r}; whitespace and digit separators are forbidden")
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError):
        _fail(audit, f"{where}: invalid decimal amount {value!r}")
    if not decimal.is_finite() or decimal < 0:
        _fail(audit, f"{where}: amount must be finite and nonnegative; received {value!r}")
    _, digits, exponent = decimal.as_tuple()
    if max(len(digits) + exponent, 0) > _MAX_MONEY_DIGITS or max(-exponent, 0) > _MAX_MONEY_DIGITS:
        _fail(audit, f"{where}: monetary representation exceeds the resource limit of {_MAX_MONEY_DIGITS} integer/fractional digits; this is not an analytical threshold")
    coefficient = int("".join(map(str, digits)))
    power = exponent + 2
    if power >= 0:
        return coefficient * (10 ** power)
    units, remainder = divmod(coefficient, 10 ** -power)
    if remainder:
        _fail(audit, f"{where}: {value!r} exceeds the approved KZT scale of 2; rounding is forbidden")
    return units


def _read_rows(input_dir: Path, table: dict, audit: dict) -> list[dict]:
    filename = table["file"]
    try:
        return pq.read_table(input_dir / filename, columns=list(table["columns"].values())).to_pylist()
    except Exception as exc:
        _fail(audit, f"{filename}: cannot read mapped Parquet columns: {exc}")


def _read_nodes(input_dir: Path, table: dict, audit: dict) -> list[dict]:
    columns = table["columns"]
    nodes = []
    seen: dict[str, int] = {}
    for row_number, row in enumerate(_read_rows(input_dir, table, audit), 1):
        where = f"{table['file']}: row {row_number}"
        gid = _gid(row[columns["gid"]], table["gid_type"], f"{where}, field gid ({columns['gid']})", audit)
        if gid in seen:
            _fail(audit, f"{where}, field gid: duplicate identifier {gid!r}; first seen at row {seen[gid]}; no rows were silently removed")
        seen[gid] = row_number
        quality = {"is_seed": None, "hop_depth": None, "outbound_censored": None, "inbound_incomplete": None, "reasons": []}
        for field in ("is_seed", "hop_depth", "outbound_censored", "inbound_incomplete"):
            if field not in columns:
                quality["reasons"].append(f"{field}: no explicit source column mapped; value is unknown")
                continue
            value = row[columns[field]]
            if value is None:
                quality["reasons"].append(f"{field}: source value is null; value is unknown")
            elif field == "hop_depth":
                quality[field] = _count(value, f"{where}, field {field} ({columns[field]})", audit)
            elif type(value) is not bool:
                _fail(audit, f"{where}, field {field} ({columns[field]}): expected boolean or null, received {value!r}")
            else:
                quality[field] = value
        if quality["outbound_censored"] is True:
            quality["reasons"].append("Outgoing observation is explicitly marked as censored")
        if quality["inbound_incomplete"] is True:
            quality["reasons"].append("Incoming observation is explicitly marked as incomplete")
        nodes.append({"gid": gid, "quality": quality})
    return sorted(nodes, key=lambda node: node["gid"])


def _read_pairs(input_dir: Path, table: dict, gids: set[str], audit: dict, name: str) -> dict[tuple[str, str], tuple[int, int]]:
    columns = table["columns"]
    pairs: dict[tuple[str, str], tuple[int, int]] = {}
    pair_first_rows: dict[tuple[str, str], int] = {}
    ids: dict[str, int] = {}
    repeated: Counter[tuple[str, str, int]] = Counter()
    rows = _read_rows(input_dir, table, audit)
    for row_number, row in enumerate(rows, 1):
        where = f"{table['file']}: row {row_number}"
        source = _gid(row[columns["source"]], table["gid_type"], f"{where}, field source ({columns['source']})", audit)
        target = _gid(row[columns["target"]], table["gid_type"], f"{where}, field target ({columns['target']})", audit)
        for field, gid in (("source", source), ("target", target)):
            if gid not in gids:
                _fail(audit, f"{where}, field {field} ({columns[field]}): unknown node gid {gid!r}")
        amount = _amount(row[columns["amount"]], f"{where}, field amount ({columns['amount']})", audit)
        tx_count = _count(row[columns["tx_count"]], f"{where}, field tx_count ({columns['tx_count']})", audit) if table["kind"] == "aggregate" else 1
        if tx_count == 0 and amount > 0:
            _fail(audit, f"{where}, field tx_count ({columns['tx_count']}): zero transactions cannot have a positive amount")
        if "id" in columns:
            raw_id = row[columns["id"]]
            if not ((isinstance(raw_id, str) and raw_id) or type(raw_id) is int):
                _fail(audit, f"{where}, field id ({columns['id']}): required string/integer event ID; received {raw_id!r}; float/bool coercion is forbidden")
            event_id = str(raw_id)
            if event_id in ids:
                _fail(audit, f"{where}, field id ({columns['id']}): duplicate ID {event_id!r}; first seen at row {ids[event_id]}; no rows were silently removed")
            ids[event_id] = row_number
        elif table["kind"] == "event":
            repeated[(source, target, amount)] += 1
        pair = (source, target)
        if table["kind"] == "aggregate" and pair in pairs:
            _fail(audit, f"{where}: duplicate aggregate directed pair {pair!r}; first seen at row {pair_first_rows[pair]}; declare kind='event' only after source semantics are confirmed")
        old_amount, old_count = pairs.get(pair, (0, 0))
        pairs[pair] = old_amount + amount, old_count + tx_count
        pair_first_rows.setdefault(pair, row_number)
    self_pairs = {pair: values for pair, values in pairs.items() if pair[0] == pair[1]}
    audit["tables"][name] = {
        "file": table["file"], "kind": table["kind"], "row_count": len(rows),
        "totals": pair_totals(pairs), "self_transfers": pair_totals(self_pairs),
        "duplicate_id_count": 0,
        "repeated_canonical_events_without_id": sum(count - 1 for count in repeated.values()),
        "repeated_event_policy": "retain every event; no automatic deduplication",
        "repeated_event_definition": "same mapped source, target and exact amount; other unmapped columns are not compared",
        "rows_removed": 0,
    }
    return pairs


def load_inputs(input_dir: Path, mapping: dict, self_transfers: str) -> tuple[list[dict], list[dict], dict]:
    """Return canonical nodes, one aggregate per directed pair, and input audit.

    Both money tables are validated and reconciled, but only the configured
    source supplies the final edges. Self transfers are then included/excluded
    consistently for every downstream consumer.
    """
    audit: dict[str, Any] = {
        "status": "validating", "schemas": {}, "tables": {}, "counts": {},
        "warnings": [], "errors": [],
    }
    if self_transfers not in ("include", "exclude"):
        _fail(audit, "self_transfers: explicitly choose 'include' or 'exclude'")
    input_dir = Path(input_dir)
    try:
        schemas = inspect_schemas(input_dir)
    except InputValidationError as exc:
        audit["schemas"] = exc.audit
        _fail(audit, str(exc))
    audit["schemas"] = schemas
    tables = _mapping_tables(mapping, schemas, audit)
    audit["dataset_kind"] = mapping["dataset_kind"]
    audit["money"] = {"unit": "KZT", "scale": 2, "calculation": "integer minor units", "float_policy": "reject"}
    audit["amount_source"] = mapping["amount_source"]
    audit["quality_sources"] = {
        field: {"file": tables["nodes"]["file"], "column": tables["nodes"]["columns"].get(field), "inferred": False}
        for field in ("is_seed", "hop_depth", "outbound_censored", "inbound_incomplete")
    }
    nodes = _read_nodes(input_dir, tables["nodes"], audit)
    audit["tables"]["nodes"] = {"file": tables["nodes"]["file"], "row_count": len(nodes), "duplicate_gid_count": 0}
    gids = {node["gid"] for node in nodes}
    edge_pairs = _read_pairs(input_dir, tables["edges"], gids, audit, "edges")
    transaction_pairs = _read_pairs(input_dir, tables["transactions"], gids, audit, "transactions")
    audit["reconciliation"] = reconcile_pairs(edge_pairs, transaction_pairs)
    audit["reconciliation"]["policy"] = mapping["reconciliation"]
    selected = edge_pairs if mapping["amount_source"] == "edges" else transaction_pairs
    self_pairs = {pair: values for pair, values in selected.items() if pair[0] == pair[1]}
    excluded = self_pairs if self_transfers == "exclude" else {}
    audit["self_transfers"] = {
        "policy": self_transfers, "source": mapping["amount_source"],
        "observed": pair_totals(self_pairs), "excluded": pair_totals(excluded),
    }
    edges = [
        {"source": source, "target": target, "amount_kzt": money_string(amount), "tx_count": count}
        for (source, target), (amount, count) in sorted(selected.items())
        if self_transfers == "include" or source != target
    ]
    audit["counts"] = {
        **graph_counts(nodes, edges),
        "edge_rows": audit["tables"]["edges"]["row_count"],
        "transaction_rows": audit["tables"]["transactions"]["row_count"],
    }
    audit["quality"] = quality_summary(nodes)
    audit["brief_comparison"] = {
        key: {"expected": expected, "actual": audit["counts"][key], "matches": audit["counts"][key] == expected}
        for key, expected in {"nodes": 2248, "edge_rows": 3119, "transaction_rows": 4840, "weak_components": 16}.items()
    }
    # These are observed counts, never a reconstruction of traversal flags.
    seed_known = audit["quality"]["is_seed"]["unknown"] == 0
    censored_known = audit["quality"]["outbound_censored"]["unknown"] == 0
    observed_senders = {source for source, _ in selected}
    additional_comparisons = {
        "seed_nodes": (81, audit["quality"]["is_seed"]["true"] if seed_known else None, "is_seed"),
        "outbound_censored_nodes": (444, audit["quality"]["outbound_censored"]["true"] if censored_known else None, "outbound_censored"),
        "seed_without_outgoing": (
            31,
            sum(node["quality"]["is_seed"] is True and node["gid"] not in observed_senders for node in nodes) if seed_known else None,
            "is_seed; outgoing pairs from the selected source before self-transfer policy",
        ),
    }
    for key, (expected, actual, basis) in additional_comparisons.items():
        audit["brief_comparison"][key] = {
            "expected": expected, "actual": actual,
            "matches": None if actual is None else actual == expected,
            "basis": basis,
            "limitation": "required explicit quality flags are not known for every node" if actual is None else None,
        }
        if actual is not None:
            audit["counts"][key] = actual
    for field in ("is_seed", "outbound_censored", "inbound_incomplete"):
        if audit["quality"][field]["unknown"]:
            audit["warnings"].append(f"{field} is unknown for {audit['quality'][field]['unknown']} nodes; no traversal inference was applied")
    if audit["quality"]["hop_depth"]["unknown"]:
        audit["warnings"].append("Some hop_depth values are unknown; no depth reconstruction was applied")
    if mapping["dataset_kind"] == "real" and any(check["matches"] is False for check in audit["brief_comparison"].values()):
        audit["warnings"].append("Actual dataset counts differ from the brief; see brief_comparison; actual data were retained")
    if audit["reconciliation"]["status"] == "mismatch":
        message = f"edges versus transactions: {audit['reconciliation']['mismatch_count']} directed pair(s) differ; see audit.reconciliation.differences; amount source is {mapping['amount_source']}"
        if mapping["reconciliation"] == "error":
            _fail(audit, message)
        audit["warnings"].append(message)
    audit["status"] = "passed"
    return nodes, edges, audit
