"""Contract artifacts and the separate CSV schema supplied with the task."""

import csv
import io
import json

from .audit import money_string


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def csv_bytes(columns, rows) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _minor_units(value: str) -> int:
    whole, _, fraction = value.partition(".")
    if len(fraction) > 2:
        raise ValueError("Expected canonical two-decimal KZT values")
    return int(whole) * 100 + int(fraction.ljust(2, "0"))


def _short_evidence(node) -> str:
    m, q = node.metrics, node.quality
    ratio = "нет входа" if m.out_in_ratio is None else f"{m.out_in_ratio:.4g}"
    status = "правило" if node.assignment_status == "rule_matched" else "недостаточно данных"
    return (f"вход={m.in_degree_unique}/{m.in_amount_kzt} KZT; "
            f"выход={m.out_degree_unique}/{m.out_amount_kzt} KZT; "
            f"O/I={ratio}; глубина={q.hop_depth}; "
            f"обрезка={q.outbound_censored}; {status}")[:200]


def build_exports(nodes, edges, clusters, temporal) -> dict[str, bytes]:
    """Return deterministic bytes; score rescaling happens only in task CSVs."""
    ordered = sorted(nodes, key=lambda n: (-n.priority_score, n.gid))
    contents = {
        "nodes.json": json_bytes([node.model_dump(mode="json") for node in nodes]),
        "edges.json": json_bytes(edges),
        "clusters.json": json_bytes([cluster.model_dump(mode="json") for cluster in clusters]),
        "temporal.json": json_bytes(temporal),
    }
    fields = ("rank", "gid", "role", "role_score", "priority_score", "priority_explanation")
    contents["priorities.csv"] = csv_bytes(fields, (
        {"rank": rank, **{key: getattr(node, key) for key in fields[1:]}}
        for rank, node in enumerate(ordered, 1)))
    fields = ("gid", "role", "role_score", "assignment_status", "role_explanation")
    contents["roles.csv"] = csv_bytes(fields, ({key: getattr(node, key) for key in fields} for node in nodes))

    cluster_ids = {cluster.cluster_id: index for index, cluster in enumerate(clusters, 1)}
    contents["nodes_roles.csv"] = csv_bytes(
        ("gid", "role", "role_score", "cluster_id", "priority_score", "evidence", "assignment_status"),
        ({"gid": node.gid, "role": node.role, "role_score": node.role_score / 100,
          "cluster_id": cluster_ids[node.cluster_id], "priority_score": node.priority_score / 100,
          "evidence": _short_evidence(node), "assignment_status": node.assignment_status} for node in nodes))
    by_gid = {node.gid: node for node in nodes}
    amounts = {cluster.cluster_id: 0 for cluster in clusters}
    for edge in edges:
        cluster = by_gid[edge["source"]].cluster_id
        if cluster == by_gid[edge["target"]].cluster_id:
            amounts[cluster] += _minor_units(edge["amount_kzt"])
    contents["clusters.csv"] = csv_bytes(
        ("cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis", "internal_cluster_id"),
        ({"cluster_id": cluster_ids[cluster.cluster_id], "n_nodes": len(cluster.gids),
          "n_seed": sum(by_gid[gid].quality.is_seed is True for gid in cluster.gids),
          "sum_kzt_internal": money_string(amounts[cluster.cluster_id]),
          "top_gids": ";".join(sorted(cluster.gids, key=lambda gid: (-by_gid[gid].priority_score, gid))[:5]),
          "hypothesis": cluster.hypothesis.text, "internal_cluster_id": cluster.cluster_id} for cluster in clusters))
    contents["top_nodes.csv"] = csv_bytes(("rank", "gid", "role", "priority_score", "why"), (
        {"rank": rank, "gid": node.gid, "role": node.role, "priority_score": node.priority_score / 100,
         "why": (f"Приоритет={node.priority_score / 100:.6f} (0–1); "
                 f"вход={node.metrics.in_amount_kzt} KZT, выход={node.metrics.out_amount_kzt} KZT; "
                 f"связей={node.metrics.in_degree_unique + node.metrics.out_degree_unique}; "
                 f"операций={node.metrics.tx_in_count + node.metrics.tx_out_count}. "
                 "Формула и нормировки: rules.json и diagnostics.json; это порядок исследования.")}
        for rank, node in enumerate(ordered[:20], 1)))
    return contents
