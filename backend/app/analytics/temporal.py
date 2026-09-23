"""Observed calendar-day activity; no claim that incoming money moved onward."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .io import _event_date, _gid, _observation, _read_rows, load_inputs


def build_temporal_features(input_dir: Path, mapping: dict, self_transfers: str) -> dict[str, dict]:
    """Return per-node transaction activity, retaining isolates and repeat events.

    The transactions table supplies dates even when aggregate edges supply money;
    load_inputs validates both tables and their configured reconciliation first.
    Missing date mapping returns no temporal evidence, rather than false zeros.
    A self transfer counts once in daily activity and once in each direction.
    Dates are inclusive calendar days; timestamps are never silently truncated.
    """
    nodes, _, audit = load_inputs(input_dir, mapping, self_transfers)
    table = mapping["tables"]["transactions"]
    if "date" not in table["columns"]:
        return {}
    observation = _observation(mapping, mapping["tables"], audit)
    columns = table["columns"]
    incoming = {node["gid"]: Counter() for node in nodes}
    outgoing = {node["gid"]: Counter() for node in nodes}
    daily = {node["gid"]: Counter() for node in nodes}
    for row_number, row in enumerate(_read_rows(Path(input_dir), table, audit), 1):
        where = f"{table['file']}: row {row_number}"
        source = _gid(row[columns["source"]], table["gid_type"], f"{where}, field source", audit)
        target = _gid(row[columns["target"]], table["gid_type"], f"{where}, field target", audit)
        day = _event_date(row, table, observation, where, audit)
        if self_transfers == "exclude" and source == target:
            continue
        incoming[target][day] += 1
        outgoing[source][day] += 1
        daily[source][day] += 1
        if target != source:
            daily[target][day] += 1
    return {
        node["gid"]: {
            "active_days": len(daily[node["gid"]]),
            "in_active_days": len(incoming[node["gid"]]),
            "out_active_days": len(outgoing[node["gid"]]),
            "peak_daily_tx": max(daily[node["gid"]].values(), default=0),
            "first_date": min(daily[node["gid"]]).isoformat() if daily[node["gid"]] else None,
            "last_date": max(daily[node["gid"]]).isoformat() if daily[node["gid"]] else None,
        }
        for node in nodes
    }
