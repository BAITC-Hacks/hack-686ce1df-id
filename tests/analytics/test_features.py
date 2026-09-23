"""Hand-calculated synthetic cases; these are not production results."""

from decimal import Decimal, Inexact, Rounded, localcontext
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.app.analytics.features import build_features


def node(gid):
    return {"gid": gid, "quality": {
        "is_seed": None, "hop_depth": None, "outbound_censored": None,
        "inbound_incomplete": None, "reasons": [],
    }}


def edge(source, target, amount, count=1):
    return {"source": source, "target": target, "amount_kzt": amount, "tx_count": count}


class FeatureTests(unittest.TestCase):
    def test_hand_example_counts_unique_degrees_and_ratio_above_one(self):
        values = build_features(
            [node(gid) for gid in ["a", "b", "c"]],
            [edge("a", "b", "30.00", 2), edge("a", "c", "70.00", 3),
             edge("b", "a", "10.00", 4), edge("a", "b", "10.00", 5)],
        )
        self.assertEqual(values["a"], {
            "in_degree_unique": 1, "out_degree_unique": 2,
            "tx_in_count": 4, "tx_out_count": 10,
            "in_amount_kzt": "10.00", "out_amount_kzt": "110.00", "out_in_ratio": 11.0,
        })
        self.assertEqual(values["b"]["in_degree_unique"], 1)
        self.assertEqual(values["b"]["tx_in_count"], 7)
        self.assertEqual(values["c"]["out_in_ratio"], 0.0)

    def test_leading_zero_id_and_isolated_nodes_are_preserved(self):
        values = build_features(
            [node("0007"), node("7"), node("isolated")], [edge("0007", "7", "9.10")]
        )
        self.assertEqual(set(values), {"0007", "7", "isolated"})
        self.assertIsNone(values["0007"]["out_in_ratio"])
        self.assertEqual(values["7"]["in_amount_kzt"], "9.10")
        self.assertEqual(values["isolated"], {
            "in_degree_unique": 0, "out_degree_unique": 0,
            "tx_in_count": 0, "tx_out_count": 0,
            "in_amount_kzt": "0", "out_amount_kzt": "0", "out_in_ratio": None,
        })
        json.dumps(values, allow_nan=False)

    def test_zero_amounts_and_counts_still_describe_observed_edges(self):
        values = build_features([node("a"), node("b")], [edge("a", "b", "0.00", 0)])
        self.assertEqual(values["a"]["out_degree_unique"], 1)
        self.assertEqual(values["b"]["in_degree_unique"], 1)
        self.assertEqual(values["a"]["tx_out_count"], 0)
        self.assertIsNone(values["b"]["out_in_ratio"])

    def test_supplied_self_loop_contributes_to_both_sides(self):
        values = build_features([node("a")], [edge("a", "a", "12.50", 2)])["a"]
        self.assertEqual(values, {
            "in_degree_unique": 1, "out_degree_unique": 1,
            "tx_in_count": 2, "tx_out_count": 2,
            "in_amount_kzt": "12.50", "out_amount_kzt": "12.50", "out_in_ratio": 1.0,
        })

    def test_exact_large_money_and_counts_independent_of_decimal_precision(self):
        count = 10 ** 40
        records = [edge("a", "b", "99999999999999999999999999999999999999.99", count),
                   edge("a", "b", "0.02", count + 1)]
        with localcontext() as context:
            context.prec = 3
            values = build_features([node("a"), node("b")], records)
        self.assertEqual(values["a"]["out_amount_kzt"], "100000000000000000000000000000000000000.01")
        self.assertEqual(values["b"]["in_amount_kzt"], values["a"]["out_amount_kzt"])
        self.assertEqual(values["a"]["tx_out_count"], 2 * count + 1)

    def test_mixed_scales_and_exponents_do_not_round(self):
        values = build_features([node("a"), node("b")], [
            edge("a", "b", "1e3"), edge("a", "b", "0.00000001"), edge("a", "b", "2.10")
        ])
        self.assertEqual(values["a"]["out_amount_kzt"], "1002.10000001")

    def test_attribute_edge_records_are_supported(self):
        values = build_features([node("a"), node("b")], [SimpleNamespace(**edge("a", "b", "1"))])
        self.assertEqual(values["a"]["out_amount_kzt"], "1")

    def test_empty_graph(self):
        self.assertEqual(build_features([], []), {})

    def test_duplicate_or_malformed_nodes_fail(self):
        for nodes in [[node("a"), node("a")], [node(7)], [node("")], [node(" a")], [{}]]:
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                build_features(nodes, [])

    def test_unknown_endpoint_fails(self):
        with self.assertRaisesRegex(ValueError, "unknown endpoint.*b"):
            build_features([node("a")], [edge("a", "b", "1")])

    def test_invalid_money_fails_instead_of_coercing_or_rounding(self):
        for amount in [1.25, 1, Decimal("1"), None, "", "NaN", "Infinity", "-0.01", "1_000", " 1"]:
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                build_features([node("a"), node("b")], [edge("a", "b", amount)])

    def test_invalid_counts_and_endpoints_fail(self):
        records = [edge("a", "b", "1", count) for count in [-1, True, 1.0, "1", None]]
        records += [edge(7, "b", "1"), edge("a", "", "1"), edge("a", None, "1"), {"source": "a"}]
        for record in records:
            with self.subTest(record=record), self.assertRaises(ValueError):
                build_features([node("a"), node("b")], [record])

    def test_float_overflow_ratio_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "finite JSON number"):
            build_features([node("a"), node("b")], [edge("a", "b", "1e400"), edge("b", "a", "1")])

    def test_ratio_ignores_ambient_decimal_rounding_traps(self):
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            values = build_features([node("a"), node("b")], [edge("a", "b", "1"), edge("b", "a", "3")])
            self.assertTrue(context.traps[Inexact])
            self.assertTrue(context.traps[Rounded])
        self.assertEqual(values["a"]["out_in_ratio"], 1 / 3)
        self.assertEqual(values["b"]["out_in_ratio"], 3.0)

    def test_positive_ratio_underflow_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "node 'a'.*underflows to zero"):
            build_features([node("a"), node("b")], [edge("a", "b", "1e-400"), edge("b", "a", "1")])

    def test_huge_exponents_rejected_before_integer_scaling(self):
        for amount in ["1e1000000000", "1e-1000000000", "0e1000000000", "0e-1000000000", "1e1000", "1e-1001"]:
            with self.subTest(amount=amount), patch("backend.app.analytics.features._ExactAmount.add") as add:
                with self.assertRaisesRegex(ValueError, r"edges\[0\].*representation resource limit"):
                    build_features([node("a"), node("b")], [edge("a", "b", amount)])
                add.assert_not_called()

    def test_expanded_representation_limits_allow_boundary_values(self):
        for amount, expected in [("1e999", "1" + "0" * 999), ("1e-1000", "0." + "0" * 999 + "1")]:
            with self.subTest(amount=amount):
                values = build_features([node("a"), node("b")], [edge("a", "b", amount)])
                self.assertEqual(values["a"]["out_amount_kzt"], expected)


if __name__ == "__main__":
    unittest.main()
