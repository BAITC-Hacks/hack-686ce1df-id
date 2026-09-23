"""Independent expected PageRank solutions and starter CSV semantics."""

import csv
from fractions import Fraction
import io
import math

import pytest

from backend.app.analytics.exports import _weighted_pagerank, build_exports
from backend.app.analytics.features import build_features
from backend.app.contracts import ClusterRecord, NodeRecord


def edge(source, target, amount):
    return {"source": source, "target": target, "amount_kzt": amount, "tx_count": 1}


def test_pagerank_matches_hand_solved_weighted_cycle_with_isolate():
    edges = [edge("a", "b", "90.00"), edge("a", "c", "10.00"),
             edge("b", "a", "10.00"), edge("c", "a", "10.00")]
    actual = _weighted_pagerank(["a", "b", "c", "isolated"], edges)
    # Stationary equations: d=(1-alpha+alpha*d)/4; a=d+alpha*(b+c);
    # b=d+alpha*0.9*a; c=d+alpha*0.1*a. d is the isolated node's rank.
    alpha = Fraction(17, 20)
    d = (1 - alpha) / (4 - alpha)
    a = d * (1 + 2 * alpha) / (1 - alpha * alpha)
    expected = {"a": a, "b": d + alpha * Fraction(9, 10) * a,
                "c": d + alpha * Fraction(1, 10) * a, "isolated": d}
    assert actual == pytest.approx({gid: float(rank) for gid, rank in expected.items()}, abs=1e-11)
    assert math.fsum(actual.values()) == pytest.approx(1.0, abs=1e-15)


def test_pagerank_preserves_direction_and_exact_identifiers():
    actual = _weighted_pagerank(["0007", "7"], [edge("0007", "7", "0.01")])
    assert actual == pytest.approx({"0007": 20 / 57, "7": 37 / 57}, abs=1e-11)
    reversed_graph = _weighted_pagerank(["0007", "7"], [edge("7", "0007", "0.01")])
    assert reversed_graph == pytest.approx({"0007": 37 / 57, "7": 20 / 57}, abs=1e-11)


def test_pagerank_is_order_and_amount_scale_invariant_without_float_overflow():
    gids = ["0007", "7", "收款", "isolated"]
    edges = [edge("0007", "7", "9.00"), edge("0007", "收款", "1.00"),
             edge("7", "0007", "1.00"), edge("收款", "0007", "1.00")]
    actual = _weighted_pagerank(gids, edges)
    assert actual == _weighted_pagerank(list(reversed(gids)), list(reversed(edges)))
    huge_edges = [dict(item, amount_kzt=item["amount_kzt"].split(".")[0] + "0" * 400 + ".00")
                  for item in edges]
    assert actual == _weighted_pagerank(gids, huge_edges)
    assert all(math.isfinite(rank) and rank > 0 for rank in actual.values())


def test_pagerank_empty_isolated_and_zero_weight_graphs():
    assert _weighted_pagerank([], []) == {}
    assert _weighted_pagerank(["only"], []) == {"only": 1.0}
    expected = {"a": 0.5, "b": 0.5}
    assert _weighted_pagerank(["a", "b"], []) == expected
    assert _weighted_pagerank(["a", "b"], [edge("a", "b", "0.00")]) == expected


def test_csv_keeps_money_ratios_and_mapped_unknown_quality():
    qualities = {
        "0007": {"is_seed": True, "hop_depth": 0, "outbound_censored": True},
        "7": {"is_seed": False, "hop_depth": 1, "outbound_censored": False},
        # The configured observation boundary is not necessarily depth 4.
        "boundary": {"is_seed": False, "hop_depth": 7, "outbound_censored": True},
        "unknown": {"is_seed": None, "hop_depth": None, "outbound_censored": None},
    }
    edges = [edge("0007", "7", "12.34"), edge("7", "boundary", "6.17")]
    features = build_features([{"gid": gid} for gid in qualities], edges)
    nodes = [NodeRecord(
        gid=gid, component_id="component", cluster_id="cluster", role="peripheral",
        role_score=0.0, priority_score=0.0, assignment_status="insufficient_evidence",
        metrics=features[gid], quality={**quality, "inbound_incomplete": None, "reasons": []},
        role_evidence=[], priority_evidence=[], role_explanation="Synthetic test",
        priority_explanation="Synthetic test",
    ) for gid, quality in qualities.items()]
    cluster = ClusterRecord(
        cluster_id="cluster", component_id="component", gids=list(qualities),
        hypothesis={"text": "Synthetic test", "basis_rule_ids": [], "limitations": []},
    )
    content = build_exports(nodes, edges, [cluster], {})["nodes_roles.csv"]
    rows = {row["gid"]: row for row in csv.DictReader(io.StringIO(content.decode()))}
    assert rows["7"]["in_kzt"] == "12.34"
    assert rows["7"]["out_kzt"] == "6.17"
    assert rows["7"]["in_deg"] == rows["7"]["out_deg"] == "1"
    assert rows["7"]["pass_through"] == "0.5"
    assert rows["0007"]["pass_through"] == rows["unknown"]["pass_through"] == "NA"
    assert rows["boundary"]["pass_through"] == "0.0"
    assert rows["0007"]["truncated_by_depth"] == "False"
    assert rows["boundary"]["truncated_by_depth"] == "True"
    assert rows["boundary"]["depth"] == "7"
    assert rows["0007"]["is_seed"] == "True"
    assert rows["7"]["is_seed"] == "False"
    assert {rows["unknown"][field] for field in ("depth", "is_seed", "truncated_by_depth")} == {"NA"}
