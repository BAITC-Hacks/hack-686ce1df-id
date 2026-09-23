"""Exact aggregation and explicit, inspectable synthetic-only rules."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
import operator
from pathlib import Path

from ..analytics.features import build_features
from ..contracts import ClusterRecord, EdgeRecord, Evidence, NodeRecord
from .models import DemoSource, money_text, money_tiyn

RULES_PATH = Path(__file__).with_name("rules.json")


@dataclass(frozen=True)
class DerivedRecords:
    nodes: tuple[NodeRecord, ...]
    edges: tuple[EdgeRecord, ...]
    clusters: tuple[ClusterRecord, ...]


def stable_id(prefix: str, values: list[str]) -> str:
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    return prefix + hashlib.sha256(encoded).hexdigest()


def _condition(rule_id, field, operation, threshold, values):
    actual = values[field]
    left, right = actual, threshold
    if field.endswith("_amount_kzt"):
        left, right = Decimal(actual), Decimal(threshold)
    passed = None if actual is None else getattr(operator, {"gte": "ge", "lte": "le"}.get(operation, operation))(left, right)
    return Evidence(rule_id=rule_id + "." + field + "." + operation, metric=field, operator=operation,
                    actual=actual, threshold=threshold, passed=passed)


def _role(metrics, quality, rules):
    if rules["isolated_override"] and metrics["tx_in_count"] + metrics["tx_out_count"] == 0:
        evidence = [Evidence(rule_id="demo.isolated", metric="tx_in_count", operator="eq", actual=0.0, threshold=0.0, passed=True),
                    Evidence(rule_id="demo.isolated.out", metric="tx_out_count", operator="eq", actual=0.0, threshold=0.0, passed=True)]
        return "peripheral", 0.0, "insufficient_evidence", evidence, "Демонстрационный узел без переводов: оснований для роли недостаточно."
    values = metrics | quality.model_dump()
    failed = []
    for rule in rules["roles"]:
        for alternative in rule["any"]:
            evidence = [_condition("demo.roles." + rule["role"], field, operation, threshold, values)
                        for field, operation, threshold in alternative]
            if all(e.passed is True for e in evidence):
                return rule["role"], rules["role_score"], "rule_matched", evidence, f"Демонстрационное правило {rule['role']}: все перечисленные условия выполнены. Балл 100 — соответствие правилу, не вероятность."
            failed.extend(evidence)
    return "peripheral", 0.0, "insufficient_evidence", failed, "Демонстрационные правила не дали достаточных оснований; неизвестная полнота не заменена предположением."


def derive(source: DemoSource) -> DerivedRecords:
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    pairs = defaultdict(lambda: [0, 0])
    adjacent = {node.gid: set() for node in source.nodes}
    for transfer in source.transfers:
        entry = pairs[(transfer.source, transfer.target)]
        entry[0] += money_tiyn(transfer.amount_kzt)
        entry[1] += 1
        adjacent[transfer.source].add(transfer.target)
        adjacent[transfer.target].add(transfer.source)
    edges = tuple(EdgeRecord(source=a, target=b, amount_kzt=money_text(amount), tx_count=count)
                  for (a, b), (amount, count) in sorted(pairs.items()))
    metrics = build_features([{"gid": n.gid} for n in source.nodes], list(edges))
    component_ids = {}
    for gid in sorted(adjacent):
        if gid in component_ids:
            continue
        members, frontier = {gid}, [gid]
        while frontier:
            current = frontier.pop()
            for neighbor in adjacent[current] - members:
                members.add(neighbor)
                frontier.append(neighbor)
        identity = stable_id("component-", sorted(members))
        component_ids.update((member, identity) for member in members)
    grouped = defaultdict(list)
    for node in source.nodes:
        grouped[(component_ids[node.gid], node.group_id)].append(node.gid)
    groups = {group.id: group.name for group in source.groups}
    cluster_ids, clusters = {}, []
    for (component, group), members in sorted(grouped.items()):
        members.sort()
        identity = stable_id("cluster-", [group] + members)
        cluster_ids.update((gid, identity) for gid in members)
        clusters.append(ClusterRecord(cluster_id=identity, component_id=component, gids=members,
                                     hypothesis={"text": f"Демонстрационная группа «{groups[group]}»: {len(members)} узлов в одной наблюдаемой компоненте.",
                                                 "basis_rule_ids": [rules["clustering"]["rule_id"]],
                                                 "limitations": [rules["clustering"]["limitation"], "Все люди и переводы вымышлены; полнота части наблюдений ограничена или неизвестна."]}))
    # Preserve monetary evidence as exact tiyn too; float conversion belongs only
    # to log normalization, never to an exported monetary sum.
    scales = {gid: {"volume_kzt": money_tiyn(m["in_amount_kzt"]) + money_tiyn(m["out_amount_kzt"]),
                    "degree": m["in_degree_unique"] + m["out_degree_unique"],
                    "transactions": m["tx_in_count"] + m["tx_out_count"]} for gid, m in metrics.items()}
    maxima = {field: max((s[field] for s in scales.values()), default=0) for field in rules["priority"]["weights"]}
    nodes = []
    for node in sorted(source.nodes, key=lambda n: n.gid):
        m = metrics[node.gid]
        role, score, status, role_evidence, explanation = _role(m, node.quality, rules)
        priority = rules["priority"]
        q = priority["quality_base"] + priority["quality_bonus"] * sum(getattr(node.quality, f) is False for f in ("outbound_censored", "inbound_incomplete"))
        weighted = sum(weight * (math.log1p(scales[node.gid][field] / (100 if field == "volume_kzt" else 1)) /
                                 math.log1p(maxima[field] / (100 if field == "volume_kzt" else 1)) if maxima[field] else 0)
                       for field, weight in priority["weights"].items())
        priority_score = round(100 * q * weighted, priority["round_digits"])
        evidence = [Evidence(rule_id="demo.priority." + field, metric=field, operator="lte",
                             actual=money_text(scales[node.gid][field]) if field == "volume_kzt" else str(scales[node.gid][field]),
                             threshold=money_text(maxima[field]) if field == "volume_kzt" else str(maxima[field]), passed=True) for field in maxima]
        evidence.extend(Evidence(rule_id="demo.priority." + field, metric=field, operator="eq", actual=getattr(node.quality, field),
                                 threshold=False, passed=None if getattr(node.quality, field) is None else getattr(node.quality, field) is False)
                        for field in ("outbound_censored", "inbound_incomplete"))
        quality = node.quality.model_dump()
        quality["reasons"] = list(quality["reasons"])
        nodes.append(NodeRecord(gid=node.gid, component_id=component_ids[node.gid], cluster_id=cluster_ids[node.gid], role=role,
                                role_score=score, priority_score=priority_score, assignment_status=status, metrics=m, quality=quality,
                                role_evidence=role_evidence, priority_evidence=evidence, role_explanation=explanation,
                                priority_explanation=f"Демонстрационный приоритет {priority_score}: наблюдаемый объём, связи и число переводов; Q={q:.1f}. Это порядок исследования, не оценка нарушения."))
    return DerivedRecords(nodes=tuple(nodes), edges=edges, clusters=tuple(clusters))
