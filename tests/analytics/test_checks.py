"""Synthetic, hand-calculated checks for concentration contract v1."""

from decimal import Inexact, Rounded, localcontext
import json
from types import SimpleNamespace
import unittest

from backend.app.analytics.checks import check_concentration


def edge(source, target, amount, count=1):
    return {"source": source, "target": target, "amount_kzt": amount, "tx_count": count}


class ConcentrationTests(unittest.TestCase):
    def test_contract_hand_example(self):
        self.assertEqual(check_concentration("a", [edge("a", "b", "30.00"), edge("a", "c", "70.00")]), {
            "gid": "a", "total_out_kzt": "100.00", "top_receiver_gid": "c",
            "top_receiver_share": 0.7, "receiver_count": 2,
        })

    def test_aggregate_repeated_pairs_before_choosing_receiver(self):
        result = check_concentration("a", [edge("a", "b", "30"), edge("a", "c", "50"), edge("a", "b", "30")])
        self.assertEqual(result["top_receiver_gid"], "b")
        self.assertEqual(result["total_out_kzt"], "110")
        self.assertAlmostEqual(result["top_receiver_share"], 60 / 110)
        self.assertEqual(result["receiver_count"], 2)

    def test_tie_break_uses_original_lexicographic_gid(self):
        edges = [edge("a", "7", "10.00"), edge("a", "0007", "10.0")]
        for records in [edges, list(reversed(edges))]:
            result = check_concentration("a", records)
            self.assertEqual(result["top_receiver_gid"], "0007")
            self.assertEqual(result["top_receiver_share"], 0.5)

    def test_zero_volume_has_null_share_and_observed_receiver(self):
        result = check_concentration("a", [edge("a", "c", "0.00", 0), edge("a", "b", "0", 2)])
        self.assertEqual(result, {
            "gid": "a", "total_out_kzt": "0.00", "top_receiver_gid": "b",
            "top_receiver_share": None, "receiver_count": 2,
        })
        json.dumps(result, allow_nan=False)

    def test_no_outgoing_edges_has_no_receiver(self):
        self.assertEqual(check_concentration("a", [edge("b", "a", "10")]), {
            "gid": "a", "total_out_kzt": "0", "top_receiver_gid": None,
            "top_receiver_share": None, "receiver_count": 0,
        })

    def test_leading_zero_source_and_self_loop_preserved(self):
        result = check_concentration("0007", [edge("0007", "0007", "12"), edge("7", "b", "100")])
        self.assertEqual(result["total_out_kzt"], "12")
        self.assertEqual(result["top_receiver_gid"], "0007")
        self.assertEqual(result["top_receiver_share"], 1.0)

    def test_attribute_edge_records_are_supported(self):
        result = check_concentration("a", [SimpleNamespace(**edge("a", "b", "8.25"))])
        self.assertEqual(result["total_out_kzt"], "8.25")
        self.assertEqual(result["top_receiver_share"], 1.0)

    def test_exact_large_sum_and_nearly_tied_receivers(self):
        records = [edge("a", "b", "99999999999999999999999999999999.99", 10 ** 40),
                   edge("a", "c", "99999999999999999999999999999999.99"),
                   edge("a", "c", "0.01")]
        with localcontext() as context:
            context.prec = 3
            result = check_concentration("a", records)
        self.assertEqual(result["total_out_kzt"], "199999999999999999999999999999999.99")
        self.assertEqual(result["top_receiver_gid"], "c")
        self.assertEqual(result["top_receiver_share"], 0.5)

    def test_concentration_ignores_ambient_decimal_rounding_traps(self):
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            result = check_concentration("a", [edge("a", gid, "1") for gid in ["b", "c", "d"]])
        self.assertEqual(result["top_receiver_share"], 1 / 3)
        self.assertEqual(result["top_receiver_gid"], "b")

    def test_huge_exponents_rejected(self):
        for amount in ["1e1000000000", "1e-1000000000"]:
            with self.subTest(amount=amount), self.assertRaisesRegex(ValueError, "representation resource limit"):
                check_concentration("a", [edge("a", "b", amount)])

    def test_bad_query_gid_fails(self):
        for gid in [7, None, "", " a"]:
            with self.subTest(gid=gid), self.assertRaises(ValueError):
                check_concentration(gid, [])

    def test_malformed_edges_fail_even_for_different_source(self):
        for record in [edge("b", "c", 3.5), edge("b", "c", "-1"),
                       edge("b", "c", "1", True), edge("b", None, "1"), {}]:
            with self.subTest(record=record), self.assertRaises(ValueError):
                check_concentration("a", [record])


if __name__ == "__main__":
    unittest.main()
