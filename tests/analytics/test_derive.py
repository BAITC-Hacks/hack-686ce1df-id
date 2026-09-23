"""Hand-calculated boundaries and invariants for observed-graph-v1."""

from copy import deepcopy
from decimal import Inexact, Rounded, localcontext
import json
import math
from pathlib import Path

import pytest

from backend.app.analytics.derive import derive_records, validate_rules
from backend.app.analytics.priority import build_priorities
from backend.app.analytics.roles import assign_role, evaluate_condition

RULES = json.loads((Path(__file__).parents[2] / "config/rules.json").read_text())


def quality(**overrides):
    return {"is_seed": False, "hop_depth": 1, "outbound_censored": False,
            "inbound_incomplete": None, "reasons": [], **overrides}


def node(gid, **overrides):
    return {"gid": gid, "quality": quality(**overrides)}


def edge(source, target, amount="100.00", count=1):
    return {"source": source, "target": target, "amount_kzt": amount, "tx_count": count}


def metrics(ki=1, ko=1, incoming="100", outgoing="100"):
    return {"in_degree_unique": ki, "out_degree_unique": ko, "tx_in_count": ki, "tx_out_count": ko,
            "in_amount_kzt": incoming, "out_amount_kzt": outgoing,
            "out_in_ratio": float(outgoing) / float(incoming) if float(incoming) else None}


def role(m, **flags):
    return assign_role(m, quality(**flags), RULES["roles"])


@pytest.mark.parametrize("ki,ko,incoming,outgoing,expected", [
    (3, 3, "100", "100", "coordinator"),
    (3, 2, "100", "50", "consolidator"),
    (3, 2, "100", "50.01", "peripheral"),
    (3, 1, "100", "0", "peripheral"),
    (2, 3, "100", "150", "distributor"),
    (2, 3, "100", "149.99", "peripheral"),
    (1, 1, "100", "80", "transit"),
    (2, 2, "100", "125", "transit"),
    (1, 1, "100", "79.99", "peripheral"),
    (1, 1, "100", "125.01", "peripheral"),
    (1, 0, "100", "0", "terminal"),
    (1, 0, "0", "0", "peripheral"),
    (0, 0, "0", "0", "peripheral"),
])
def test_role_thresholds(ki, ko, incoming, outgoing, expected):
    actual = role(metrics(ki, ko, incoming, outgoing))
    assert actual["role"] == expected
    assert actual["role_score"] == (0 if expected == "peripheral" else 100)
    assert all(item.passed is True for item in actual["role_evidence"]) if expected != "peripheral" else True


@pytest.mark.parametrize("censored", [True, None])
def test_censored_or_unknown_outgoing_cannot_create_terminal_or_balance_role(censored):
    for m in (metrics(1, 0, "100", "0"), metrics(), metrics(3, 1, "100", "50"), metrics(1, 3, "100", "200")):
        actual = role(m, outbound_censored=censored)
        assert actual["assignment_status"] == "insufficient_evidence"
        assert actual["role_score"] == 0


def test_seed_uses_structural_distributor_without_inventing_balance():
    actual = role(metrics(0, 3, "0", "300"), is_seed=True, inbound_incomplete=True)
    assert actual["role"] == "distributor"
    assert actual["role_score"] == 100
    assert {item.rule_id for item in actual["role_evidence"]} == {"role.distributor.seed_structure"}
    assert "out_in_ratio" not in {item.metric for item in actual["role_evidence"]}
    assert role(metrics(), is_seed=True)["assignment_status"] == "insufficient_evidence"
    assert role(metrics(), is_seed=None)["assignment_status"] == "insufficient_evidence"


def test_unknown_inbound_is_explicit_limit_but_observed_nonseed_balance_can_match():
    actual = role(metrics(), inbound_incomplete=None)
    assert actual["role"] == "transit"
    assert "полнота входящих не установлена" in actual["role_explanation"].lower()


@pytest.mark.parametrize("threshold,operator,below,equal,above", [
    (.5, "lte", "499999999999999999.99", "500000000000000000.00", "500000000000000000.01"),
    (.8, "gte", "799999999999999999.99", "800000000000000000.00", "800000000000000000.01"),
    (1.25, "lte", "1249999999999999999.99", "1250000000000000000.00", "1250000000000000000.01"),
    (1.5, "gte", "1499999999999999999.99", "1500000000000000000.00", "1500000000000000000.01"),
])
def test_ratio_decisions_and_evidence_preserve_exact_money_at_float_rounding_boundary(threshold, operator, below, equal, above):
    from fractions import Fraction
    from decimal import Decimal
    outcomes = []
    for outgoing in (below, equal, above):
        m = metrics(incoming="1000000000000000000.00", outgoing=outgoing)
        # All three display values round to the same binary float.
        assert m["out_in_ratio"] == threshold
        actual = evaluate_condition("exact", {"metric": "out_in_ratio", "operator": operator,
                                               "threshold": threshold}, m)
        assert isinstance(actual.actual, str)
        exact = Fraction(Decimal(outgoing)) / Fraction(Decimal(m["in_amount_kzt"]))
        assert Fraction(actual.actual) == exact
        assert Fraction(actual.threshold) == Fraction(str(threshold))
        outcomes.append(actual.passed)
    assert outcomes == ([True, True, False] if operator == "lte" else [False, True, True])


def test_transit_does_not_match_rounded_up_display_ratio():
    actual = role(metrics(incoming="1000000000000000000.00", outgoing="799999999999999999.99"))
    assert actual["role"] == "peripheral"
    exact_check = next(e for e in actual["role_evidence"] if e.rule_id == "role.transit" and e.metric == "out_in_ratio" and e.operator == "gte")
    assert exact_check.passed is False
    assert exact_check.actual == "79999999999999999999/100000000000000000000"


def test_conflict_order_is_executable_and_evidence_keeps_actual_thresholds():
    modified = deepcopy(RULES)
    # Intentionally overlapping degree limits to exercise precedence, without
    # changing its documented ordering or quality eligibility.
    for condition in modified["roles"]["rules"][1]["conditions"]:
        if condition["metric"] == "out_degree_unique" and condition["operator"] == "lte":
            condition["threshold"] = 3
    validate_rules(modified)
    actual = assign_role(metrics(3, 3, "100", "50"), quality(), modified["roles"])
    assert actual["role"] == "coordinator"
    assert [(e.metric, e.actual, e.threshold, e.passed) for e in actual["role_evidence"]] == [
        ("in_degree_unique", 3, 3, True), ("out_degree_unique", 3, 3, True)]


def test_unknown_flags_remain_null_evidence_and_isolates_are_retained():
    records, clusters, diagnostic = derive_records([node("isolated", is_seed=None, outbound_censored=None)], [], RULES)
    assert records[0].role == "peripheral"
    assert records[0].assignment_status == "insufficient_evidence"
    assert records[0].priority_score == records[0].role_score == 0
    assert any(item.actual is None and item.passed is None for item in records[0].role_evidence)
    assert clusters[0].gids == ["isolated"]
    assert diagnostic["clustering"]["isolated_nodes"] == ["isolated"]
    assert clusters[0].hypothesis.basis_rule_ids == ["cluster.singleton"]


def test_all_content_is_independent_of_input_order_and_decimal_context():
    nodes = [node(gid) for gid in ("0007", "7", "a", "b", "c", "isolate")]
    edges = [edge("0007", "7", "10.10", 4), edge("7", "0007", "2.10", 1),
             edge("a", "b", "30.20", 3), edge("b", "c", "30.20", 3), edge("c", "a", "30.20", 3)]
    first = derive_records(nodes, edges, RULES, {"a": {"active_days": 2}})
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = context.traps[Rounded] = True
        second = derive_records(list(reversed(nodes)), list(reversed(edges)), RULES, {"a": {"active_days": 2}})
    assert first == second
    assert len(first[0]) == 6
    assert first[2]["clustering"]["component_count"] == 3
    assert first[2]["temporal"] == {"a": {"active_days": 2}}
    json.dumps(first[2], allow_nan=False)


def test_zero_weight_edges_preserve_weak_component_but_create_singleton_communities():
    records, clusters, _ = derive_records([node("a"), node("b")], [edge("a", "b", "0", 0)], RULES)
    assert len({item.component_id for item in records}) == 1
    assert len(clusters) == 2
    assert {tuple(item.gids) for item in clusters} == {("a",), ("b",)}


def test_priority_has_hand_calculable_normalization_and_quality_discount():
    features = {"a": metrics(1, 1, "50", "50"), "b": metrics(0, 1, "0", "10"), "c": metrics(0, 0, "0", "0")}
    qualities = {"a": quality(), "b": quality(outbound_censored=True), "c": quality()}
    actual, diagnostic = build_priorities(features, qualities, RULES["priority"])
    assert actual["a"]["priority_score"] == 90
    expected = round(100 * .8 * (.45 * math.log1p(10) / math.log1p(100) + .30 * math.log1p(1) / math.log1p(2) + .25 * math.log1p(1) / math.log1p(2)), 4)
    assert actual["b"]["priority_score"] == expected
    assert actual["c"]["priority_score"] == 0
    assert diagnostic["maxima"] == {"volume_kzt": "100", "degree": "2", "transactions": "2"}
    assert diagnostic["by_gid"]["a"]["quality_factor"] == .9


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(status="pending_approval"),
    lambda r: r.update(random_seed=True),
    lambda r: r.update(self_transfers="include"),
    lambda r: r["priority"]["weights"].update(volume_kzt=.1),
    lambda r: r["priority"]["quality"].update(base=2),
    lambda r: r["roles"]["rules"][1]["conditions"].pop(0),
    lambda r: r["roles"]["rules"][0]["conditions"][0].update(threshold=-1),
    lambda r: r["clustering"].update(threshold=float("nan")),
    lambda r: r.update(misspelled=True),
])
def test_invalid_or_unsafe_rules_are_rejected(mutation):
    broken = deepcopy(RULES)
    mutation(broken)
    with pytest.raises(ValueError):
        validate_rules(broken)


def test_derive_rejects_self_transfers_instead_of_silently_changing_audit_input():
    with pytest.raises(ValueError, match="self-transfers"):
        derive_records([node("a")], [edge("a", "a")], RULES)


def test_empty_graph_is_a_valid_complete_empty_derivation():
    nodes, clusters, diagnostic = derive_records([], [], RULES)
    assert nodes == clusters == []
    assert diagnostic["clustering"]["component_count"] == 0
