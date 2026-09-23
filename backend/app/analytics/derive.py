"""Versioned derivation of contract records from validated local input."""

from collections import Counter
import math

from ..contracts import NodeQuality, NodeRecord
from .clusters import build_clusters
from .features import build_features
from .priority import build_priorities
from .roles import assign_role, validate_role_rules

ALGORITHM_VERSION = "observed-graph-v1"


def _number(value, name: str, low: float, high: float | None = None) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < low or (high is not None and value > high):
        raise ValueError(f"{name}: invalid finite numeric value")


def validate_rules(rules: dict) -> dict:
    """Reject incomplete, misspelled, unsupported or unsafe configurations."""
    expected = {"schema_version", "status", "rules_version", "algorithm_version", "description", "self_transfers",
                "random_seed", "roles", "clustering", "cluster_hypotheses", "priority"}
    if not isinstance(rules, dict) or set(rules) != expected:
        raise ValueError("rules: unexpected or missing configuration fields")
    if rules["schema_version"] != 1 or rules["status"] != "configured":
        raise ValueError("rules must be configured schema version 1")
    if rules["algorithm_version"] != ALGORITHM_VERSION:
        raise ValueError("rules algorithm_version does not match executable implementation")
    if not isinstance(rules["rules_version"], str) or not rules["rules_version"] or not isinstance(rules["description"], str):
        raise ValueError("rules_version and description must be strings")
    if rules["self_transfers"] != "exclude":
        raise ValueError("observed-graph-v1 requires self_transfers=exclude")
    if type(rules["random_seed"]) is not int or rules["random_seed"] < 0:
        raise ValueError("random_seed must be a nonnegative integer")
    for name in ("roles", "clustering", "cluster_hypotheses", "priority"):
        if not isinstance(rules[name], dict):
            raise ValueError(f"{name} must be an object")
    validate_role_rules(rules["roles"])
    clustering = rules["clustering"]
    if set(clustering) != {"algorithm", "projection", "weight", "resolution", "threshold", "id_hash"}:
        raise ValueError("clustering: unexpected or missing configuration fields")
    if (clustering["algorithm"], clustering["projection"], clustering["weight"], clustering["id_hash"]) != (
            "louvain", "undirected_sum", "tx_count", "sha256_first16"):
        raise ValueError("unsupported clustering algorithm, projection, weight or id hash")
    _number(clustering["resolution"], "resolution", 1e-12)
    _number(clustering["threshold"], "threshold", 0)
    hypotheses = rules["cluster_hypotheses"]
    if set(hypotheses) != {"coordinator_min_count", "transit_min_fraction", "terminal_min_fraction", "rule_ids"}:
        raise ValueError("cluster_hypotheses: unexpected or missing fields")
    if not isinstance(hypotheses["rule_ids"], dict) or set(hypotheses["rule_ids"]) != {
            "singleton", "coordinator", "transit", "terminal", "unresolved"}:
        raise ValueError("cluster_hypotheses.rule_ids must name all five hypotheses")
    if type(hypotheses["coordinator_min_count"]) is not int or hypotheses["coordinator_min_count"] < 1:
        raise ValueError("coordinator_min_count must be a positive integer")
    for key in ("transit_min_fraction", "terminal_min_fraction"):
        _number(hypotheses[key], key, 0, 1)
    priority = rules["priority"]
    if set(priority) != {"formula", "weights", "quality", "decimals", "rule_ids"} or priority["formula"] != "log1p_max_weighted":
        raise ValueError("unsupported priority settings or formula")
    if not isinstance(priority["rule_ids"], dict) or set(priority["rule_ids"]) != {"quality", "normalization", "formula"}:
        raise ValueError("priority.rule_ids must name quality, normalization and formula")
    if not isinstance(priority["weights"], dict) or set(priority["weights"]) != {"volume_kzt", "degree", "transactions"}:
        raise ValueError("priority weights require volume_kzt, degree and transactions")
    for name, value in priority["weights"].items():
        _number(value, name, 0, 1)
    if not math.isclose(sum(priority["weights"].values()), 1, rel_tol=0, abs_tol=1e-12):
        raise ValueError("priority weights must sum to 1")
    if not isinstance(priority["quality"], dict) or set(priority["quality"]) != {
            "base", "outgoing_complete_bonus", "incoming_complete_bonus"}:
        raise ValueError("priority quality requires base and two explicit completeness bonuses")
    for name, value in priority["quality"].items():
        _number(value, name, 0, 1)
    if not math.isclose(sum(priority["quality"].values()), 1, rel_tol=0, abs_tol=1e-12):
        raise ValueError("priority quality factors must sum to 1")
    if type(priority["decimals"]) is not int or not 0 <= priority["decimals"] <= 8:
        raise ValueError("priority decimals must be an integer from 0 to 8")
    ids = ([rule["id"] for rule in rules["roles"]["rules"]]
           + list(hypotheses["rule_ids"].values()) + list(priority["rule_ids"].values()))
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("all evidence rule IDs must be nonempty unique strings")
    return rules


def derive_records(nodes: list[dict], edges: list[dict], rules: dict, temporal: dict | None = None) -> tuple[list[NodeRecord], list, dict]:
    """Preserve every input gid and derive deterministic, strictly typed records.

    Self-transfers must already have been excluded by input loading; fail on a
    mismatch so artifacts cannot silently diverge from the audit or input edges.
    """
    validate_rules(rules)
    features = build_features(nodes, edges)
    if any(edge["source"] == edge["target"] for edge in edges):
        raise ValueError("self-transfers must be excluded by load_inputs before derivation")
    qualities = {node["gid"]: NodeQuality.model_validate(node["quality"]).model_dump() for node in nodes}
    roles = {gid: assign_role(features[gid], qualities[gid], rules["roles"]) for gid in sorted(features)}
    priorities, priority_details = build_priorities(features, qualities, rules["priority"])
    membership, clusters, clustering = build_clusters(nodes, edges, roles, rules)
    temporal = temporal or {}
    if not isinstance(temporal, dict) or set(temporal) - set(features):
        raise ValueError("temporal features must be keyed by existing gid")
    records = []
    for gid in sorted(features):
        records.append(NodeRecord(gid=gid, **membership[gid], **roles[gid], **priorities[gid],
                                  metrics=features[gid], quality=qualities[gid]))
    return records, clusters, {
        "algorithm_version": ALGORITHM_VERSION,
        "features": {gid: features[gid] for gid in sorted(features)},
        "temporal": {gid: temporal[gid] for gid in sorted(temporal)},
        "temporal_limitation": "Activity dates describe observations only; they do not establish movement of the same funds.",
        "role_counts": dict(sorted(Counter(item.role for item in records).items())),
        "assignment_counts": dict(sorted(Counter(item.assignment_status for item in records).items())),
        "clustering": clustering, "priority": priority_details,
    }
