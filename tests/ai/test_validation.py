import copy
import unittest
from decimal import Decimal

from backend.app.ai.validation import ValidationError, response_schema, validate_response


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {
            "node:0007:metrics": {
                "gid": "0007",
                "metrics": {"tx_out_count": 2, "out_amount_kzt": "100.00", "out_in_ratio": None},
                "quality": {"outbound_censored": True},
            },
            "check:concentration": {
                "gid": "0007", "total_out_kzt": "100.00", "top_receiver_gid": "999",
                "top_receiver_share": 0.7, "receiver_count": 2,
            },
            "node:0007:role_evidence:0": {
                "rule_id": "rule-999", "metric": "out_amount_kzt", "operator": "gte",
                "actual": "70.00", "threshold": "30.00", "passed": True,
            },
        }

    def fragment(self, text="Наблюдаемые данные допускают гипотезу.", refs=None):
        return {
            "summary": "Нужно учитывать границы наблюдения.",
            "claims": [{"text": text, "evidence_ids": refs or ["check:concentration"]}],
            "limitations": ["Реальное движение за границей выборки неизвестно."],
        }

    def rejected(self, payload, code=None, evidence=None):
        with self.assertRaises(ValidationError) as raised:
            validate_response(payload, self.evidence if evidence is None else evidence)
        if code:
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), raised.exception.code)

    def test_schema_excludes_server_envelope_and_forbids_extra_fields(self):
        schema = response_schema("run-001")
        self.assertEqual(set(schema["properties"]), {"summary", "claims", "limitations"})
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["claims"]["items"]["additionalProperties"])
        self.assertEqual(schema["properties"]["claims"]["items"]["properties"]["evidence_ids"]["minItems"], 1)
        with self.assertRaises(ValueError):
            response_schema("")

    def test_success_returns_detached_fragment_without_mutating_input(self):
        payload = self.fragment("Доля ведущего получателя — 70%, всего 2 получателя.")
        original = copy.deepcopy(payload)
        result = validate_response(payload, self.evidence)
        self.assertEqual(result, original)
        result["claims"][0]["evidence_ids"].append("changed")
        self.assertEqual(payload, original)

    def test_empty_claims_are_permitted(self):
        payload = self.fragment()
        payload["claims"] = []
        self.assertEqual(validate_response(payload, {}), payload)

    def test_model_cannot_override_server_fields(self):
        for name in ("run_id", "status", "checks", "contract_version", "fallback_reason"):
            with self.subTest(name=name):
                payload = self.fragment()
                payload[name] = "injected"
                self.rejected(payload, "invalid_response_fields")

    def test_missing_and_extra_claim_fields(self):
        payload = self.fragment()
        del payload["claims"][0]["text"]
        self.rejected(payload, "invalid_claim_fields")
        payload = self.fragment()
        payload["claims"][0]["confidence"] = 1
        self.rejected(payload, "invalid_claim_fields")

    def test_text_and_array_bounds(self):
        for value in ("", "  ", "x" * 2001, False, None):
            with self.subTest(value=repr(value)[:30]):
                payload = self.fragment()
                payload["summary"] = value
                self.rejected(payload, "invalid_text")
        payload = self.fragment()
        payload["claims"] *= 21
        self.rejected(payload, "invalid_claims")
        payload = self.fragment()
        payload["limitations"] *= 21
        self.rejected(payload, "invalid_limitations")

    def test_refs_required_known_distinct_and_bounded(self):
        for refs in ([], ["missing"], [1], [""], ["x" * 501], ["check:concentration"] * 2,
                     ["check:concentration"] * 13, "check:concentration"):
            with self.subTest(refs=repr(refs)[:50]):
                payload = self.fragment()
                payload["claims"][0]["evidence_ids"] = refs
                self.rejected(payload, "invalid_evidence_refs")

    def test_unsupported_literal_rejected(self):
        self.rejected(self.fragment("Переведено 999 KZT."), "unsupported_number")

    def test_numeric_summary_and_limitations_have_no_citations(self):
        for field in ("summary", "limitations"):
            payload = self.fragment()
            payload[field] = "Всего 2 узла." if field == "summary" else ["Радиус 2."]
            self.rejected(payload, "uncited_number")

    def test_numbers_are_scoped_to_the_individual_claim_references(self):
        self.rejected(self.fragment("Сумма 70 KZT.", ["node:0007:metrics"]), "unsupported_number")
        payload = self.fragment("Сумма 70 KZT.", ["node:0007:role_evidence:0"])
        self.assertEqual(validate_response(payload, self.evidence), payload)

    def test_id_and_prose_digits_are_not_numeric_evidence(self):
        evidence = {"e": {
            "gid": "999", "run_id": "run-999", "rule_id": 999, "source": 999,
            "top_receiver_gid": "999", "role_explanation": "Всего 999 переводов",
            "reason": "999", "metric": "999", "actual": "999", "passed": True,
        }}
        self.rejected(self.fragment("Всего 999 переводов.", ["e"]), "unsupported_number", evidence)

    def test_null_and_boolean_never_support_zero_or_one(self):
        evidence = {"e": {"out_in_ratio": None, "outbound_censored": False, "passed": True}}
        for number in (0, 1):
            self.rejected(self.fragment(f"Число: {number}.", ["e"]), "unsupported_number", evidence)
        evidence["e"]["tx_out_count"] = 0
        payload = self.fragment("Исходящих транзакций: 0.", ["e"])
        self.assertEqual(validate_response(payload, evidence), payload)

    def test_percentage_only_supported_by_ratio_or_share(self):
        for text in ("Доля 70%.", "Доля 70 процентов.", "Share: 70 percent.", "Доля 70％."):
            with self.subTest(text=text):
                payload = self.fragment(text)
                self.assertEqual(validate_response(payload, self.evidence), payload)
        self.rejected(self.fragment("Доля 70%.", ["node:0007:role_evidence:0"]), "unsupported_percentage")
        self.rejected(self.fragment("Доля 0.7%."), "unsupported_percentage")
        self.rejected(self.fragment("Доля 100%."), "unsupported_percentage")

    def test_evidence_metric_label_supports_percentage(self):
        evidence = {"e": {"metric": "out_in_ratio", "actual": 0.7, "threshold": 0.5}}
        payload = self.fragment("Отношение составляет 70%.", ["e"])
        self.assertEqual(validate_response(payload, evidence), payload)

    def test_decimal_comma_space_grouping_negative_and_exponent(self):
        evidence = {"e": {"amount_kzt": "-1234.50", "count": 70, "share": 0.7}}
        for text in ("Сумма -1 234,50 KZT.", "Сумма −1\u202f234.50 KZT.",
                     "Сумма -1\u00a0234,50 KZT.", "Число: 7e1.", "Доля: 0,7."):
            with self.subTest(text=text):
                payload = self.fragment(text, ["e"])
                self.assertEqual(validate_response(payload, evidence), payload)

    def test_ambiguous_numbers_fail_closed(self):
        evidence = {"e": {"amount_kzt": "1234.00", "other": 1.234, "count": 1, "another": 2}}
        for token in ("1,234", "1,234.00", "1 23", "1..2", "1e+", "９９９", "1e999999"):
            with self.subTest(token=token):
                self.rejected(self.fragment(f"Значение: {token}.", ["e"]), "ambiguous_number", evidence)

    def test_scaled_units_fractions_and_percentage_points_fail_closed(self):
        for text in ("Сумма 70 тыс. KZT", "Сумма 70 million KZT", "Сумма 70k KZT",
                     "Доля 70 процентных пунктов", "70 percentage points",
                     "70/70", "70_70", "70'70", "70:70"):
            with self.subTest(text=text):
                self.rejected(self.fragment(text, ["node:0007:role_evidence:0"]), "ambiguous_number")

    def test_nonfinite_values_in_payload_and_cited_evidence_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            payload = self.fragment()
            payload["unexpected"] = value
            self.rejected(payload, "non_finite_number")
            self.rejected(self.fragment(refs=["e"]), "non_finite_number", {"e": {"share": value}})

    def test_invalid_non_json_types_are_rejected(self):
        for value in (Decimal("0.7"), {1: "value"}, ("tuple",)):
            self.rejected(self.fragment(refs=["e"]), "invalid_json_value", {"e": {"value": value}})

    def test_money_string_must_follow_contract_decimal_notation(self):
        for value in ("NaN", "Infinity", "1,00", "one hundred"):
            self.rejected(self.fragment(refs=["e"]), "invalid_evidence_number", {"e": {"amount_kzt": value}})

    def test_obvious_real_terminal_assertion_is_rejected(self):
        for text in ("Это реальный конечный получатель.",
                     "Узел является установленным конечным получателем.",
                     "The node is the actual final recipient."):
            self.rejected(self.fragment(text), "unsupported_terminal_certainty")
        payload = self.fragment("Узел не является установленным конечным получателем.")
        self.assertEqual(validate_response(payload, self.evidence), payload)


if __name__ == "__main__":
    unittest.main()
