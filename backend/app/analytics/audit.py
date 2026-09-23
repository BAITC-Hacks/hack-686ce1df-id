"""JSON-safe diagnostics for the explicitly mapped observed graph."""

from __future__ import annotations

from collections import Counter
from typing import Any

import networkx as nx


def money_string(minor_units: int) -> str:
    """Format exact KZT minor units without Decimal context rounding."""
    whole, fractional = divmod(minor_units, 100)
    return f"{whole}.{fractional:02d}"


def reconcile_pairs(
    edges: dict[tuple[str, str], tuple[int, int]],
    transactions: dict[tuple[str, str], tuple[int, int]],
) -> dict[str, Any]:
    """Compare both independently aggregated tables before any filtering."""
    differences = []
    for source, target in sorted(edges.keys() | transactions.keys()):
        left = edges.get((source, target))
        right = transactions.get((source, target))
        if left != right:
            differences.append({
                "source": source,
                "target": target,
                "edges": None if left is None else {
                    "amount_kzt": money_string(left[0]), "tx_count": left[1],
                },
                "transactions": None if right is None else {
                    "amount_kzt": money_string(right[0]), "tx_count": right[1],
                },
                "amount_difference_kzt": _signed_money(
                    (left[0] if left else 0) - (right[0] if right else 0)
                ),
                "tx_count_difference": (left[1] if left else 0) - (right[1] if right else 0),
            })
    return {
        "status": "match" if not differences else "mismatch",
        "compared_before_self_transfer_policy": True,
        "compared_pairs": len(edges.keys() | transactions.keys()),
        "mismatch_count": len(differences),
        "differences": differences,
    }


def _signed_money(value: int) -> str:
    return ("-" if value < 0 else "") + money_string(abs(value))


def pair_totals(pairs: dict[tuple[str, str], tuple[int, int]]) -> dict[str, Any]:
    return {
        "directed_pairs": len(pairs),
        "amount_kzt": money_string(sum(amount for amount, _ in pairs.values())),
        "tx_count": sum(count for _, count in pairs.values()),
    }


def graph_counts(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    graph = nx.Graph()
    graph.add_nodes_from(node["gid"] for node in nodes)
    graph.add_edges_from((edge["source"], edge["target"]) for edge in edges)
    return {
        "nodes": len(nodes),
        "effective_directed_pairs": len(edges),
        "effective_transactions": sum(edge["tx_count"] for edge in edges),
        "weak_components": nx.number_connected_components(graph),
        "isolated_nodes": nx.number_of_isolates(graph),
    }


def quality_summary(nodes: list[dict]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for field in ("is_seed", "outbound_censored", "inbound_incomplete"):
        counts = Counter(node["quality"][field] for node in nodes)
        summary[field] = {"true": counts[True], "false": counts[False], "unknown": counts[None]}
    depths = Counter(node["quality"]["hop_depth"] for node in nodes)
    summary["hop_depth"] = {
        "unknown": depths.pop(None, 0),
        "distribution": {str(depth): count for depth, count in sorted(depths.items())},
    }
    summary["inference"] = "none; only explicitly mapped input columns are used"
    return summary
