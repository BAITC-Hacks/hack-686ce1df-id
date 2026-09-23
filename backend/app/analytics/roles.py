"""Transparent, ordered engineering rules on the observed graph only."""

from decimal import Decimal
from fractions import Fraction
import math
from typing import Any

from ..contracts import Evidence

ROLES = ("coordinator", "consolidator", "distributor", "transit", "terminal")
COUNTS = {"in_degree_unique", "out_degree_unique", "tx_in_count", "tx_out_count"}
MONEY = {"in_amount_kzt", "out_amount_kzt"}
FLAGS = {"is_seed", "outbound_censored", "inbound_incomplete"}
METRICS = COUNTS | MONEY | FLAGS | {"out_in_ratio"}
OPERATORS = {"gt", "gte", "lt", "lte", "eq"}
LABELS = {
    "coordinator": "Двусторонний узел сбора и распределения",
    "consolidator": "Преобладает наблюдаемый входящий объём",
    "distributor": "Наблюдается распределение нескольким получателям",
    "transit": "Сопоставимые наблюдаемые входящий и исходящий объёмы",
    "terminal": "В наблюдаемом окне нет исходящих переводов",
}


def validate_role_rules(settings: dict) -> None:
    if set(settings) != {"precedence", "matched_score", "fallback_score", "rules"}:
        raise ValueError("roles: unexpected or missing configuration fields")
    if settings["precedence"] != list(ROLES):
        raise ValueError("roles.precedence must preserve the documented conflict order")
    if settings["matched_score"] != 100 or settings["fallback_score"] != 0:
        raise ValueError("role scores must be binary: matched=100, fallback=0")
    entries = settings["rules"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("roles.rules must be a nonempty list")
    ids = set()
    covered = set()
    for rule in entries:
        if not isinstance(rule, dict) or set(rule) != {"id", "role", "conditions"}:
            raise ValueError("role rule: expected id, role, conditions")
        if not isinstance(rule["id"], str) or not rule["id"] or rule["id"] in ids:
            raise ValueError("role rule ids must be nonempty and unique")
        ids.add(rule["id"])
        if rule["role"] not in ROLES:
            raise ValueError("unknown matched role")
        covered.add(rule["role"])
        if not isinstance(rule["conditions"], list) or not rule["conditions"]:
            raise ValueError("role rule requires conditions")
        for condition in rule["conditions"]:
            if not isinstance(condition, dict) or set(condition) != {"metric", "operator", "threshold"}:
                raise ValueError("condition: expected metric, operator, threshold")
            metric, operator, threshold = (condition[k] for k in ("metric", "operator", "threshold"))
            if metric not in METRICS or operator not in OPERATORS:
                raise ValueError("condition contains unsupported metric or operator")
            if metric in FLAGS:
                if operator != "eq" or type(threshold) is not bool:
                    raise ValueError("quality conditions must compare a boolean using eq")
            elif metric in MONEY:
                if not isinstance(threshold, str):
                    raise ValueError("money thresholds must be exact decimal strings")
                try:
                    value = Decimal(threshold)
                except Exception as exc:
                    raise ValueError("invalid decimal money threshold") from exc
                if not value.is_finite() or value < 0:
                    raise ValueError("money thresholds must be finite and nonnegative")
            elif type(threshold) not in (int, float) or not math.isfinite(threshold) or threshold < 0:
                raise ValueError("numeric thresholds must be finite and nonnegative")
            elif metric in COUNTS and int(threshold) != threshold:
                raise ValueError("degree and count thresholds must be integers")
        triples = {(c["metric"], c["operator"], c["threshold"]) for c in rule["conditions"]}
        uses_balance = any(c["metric"] == "out_in_ratio" for c in rule["conditions"])
        if rule["role"] in {"consolidator", "transit"} and not uses_balance:
            raise ValueError("consolidator and transit require an observed balance condition")
        if rule["role"] == "distributor" and not uses_balance and ("is_seed", "eq", True) not in triples:
            raise ValueError("a structural distributor rule requires an explicit seed condition")
        if uses_balance and not {("is_seed", "eq", False), ("outbound_censored", "eq", False)} <= triples:
            raise ValueError("balance rules require explicit nonseed and uncensored outgoing conditions")
        if rule["role"] == "terminal" and not {
            ("outbound_censored", "eq", False), ("out_degree_unique", "eq", 0),
            ("out_amount_kzt", "eq", "0"), ("in_amount_kzt", "gt", "0"),
        } <= triples:
            raise ValueError("terminal requires observed positive incoming and confirmed zero outgoing")
    if covered != set(ROLES):
        raise ValueError("all five matched roles must have rules")


def evaluate_condition(rule_id: str, condition: dict, values: dict) -> Evidence:
    metric, operator, threshold = (condition[k] for k in ("metric", "operator", "threshold"))
    actual = values[metric]
    # The contract ratio is a float for display. Decisions must retain the
    # exact money precision: e.g. .79999999999999999999 must not pass >= .8.
    if metric == "out_in_ratio":
        incoming = Fraction(Decimal(values["in_amount_kzt"]))
        outgoing = Fraction(Decimal(values["out_amount_kzt"]))
        exact_ratio = outgoing / incoming if incoming else None
        actual = str(exact_ratio) if exact_ratio is not None else None
        threshold = str(threshold)
    if actual is None:
        passed = None
    else:
        if metric == "out_in_ratio":
            left, right = exact_ratio, Fraction(Decimal(threshold))
        else:
            left, right = (Decimal(actual), Decimal(threshold)) if metric in MONEY else (actual, threshold)
        passed = {"gt": lambda: left > right, "gte": lambda: left >= right,
                  "lt": lambda: left < right, "lte": lambda: left <= right,
                  "eq": lambda: left == right}[operator]()
    # Contract evidence represents counts as JSON numbers; exact money stays text.
    if metric in COUNTS:
        actual = float(actual) if actual is not None else None
        threshold = float(threshold)
    return Evidence(rule_id=rule_id, metric=metric, operator=operator,
                    actual=actual, threshold=threshold, passed=passed)


def assign_role(metrics: dict, quality: dict, settings: dict) -> dict[str, Any]:
    values = {**metrics, **{name: quality[name] for name in FLAGS}}
    ordered = sorted(enumerate(settings["rules"]),
                     key=lambda pair: (settings["precedence"].index(pair[1]["role"]), pair[0]))
    attempted = []
    for _, rule in ordered:
        evidence = [evaluate_condition(rule["id"], condition, values) for condition in rule["conditions"]]
        attempted.extend(evidence)
        isolated = metrics["in_degree_unique"] == 0 and metrics["out_degree_unique"] == 0
        if not isolated and all(item.passed is True for item in evidence):
            ki, ko, ratio = metrics["in_degree_unique"], metrics["out_degree_unique"], metrics["out_in_ratio"]
            ratio_text = "не определено" if ratio is None else format(ratio, ".6g")
            explanation = (f"{LABELS[rule['role']]}. Входов={ki}, выходов={ko}; "
                           f"I={metrics['in_amount_kzt']} KZT, O={metrics['out_amount_kzt']} KZT, O/I≈{ratio_text}. "
                           f"Выполнено правило {rule['id']}; соответствие=100/100.")
            if any(item.metric == "out_in_ratio" for item in evidence):
                explanation += (" Условия отношения проверены по точным I и O без округления; "
                                "evidence хранит точную дробь. Полнота входящих не установлена.")
            if quality["is_seed"] is True and rule["role"] == "distributor":
                explanation += " Для seed применена структура связей; отношение сумм не используется."
            if rule["role"] == "coordinator":
                explanation += " Структура не доказывает организационное управление."
            if rule["role"] == "terminal":
                explanation += " Отсутствие исходящих относится только к окну и фильтрам выгрузки."
            if quality["outbound_censored"] is not False:
                explanation += " Исходящая наблюдаемость обрезана или неизвестна."
            return {"role": rule["role"], "role_score": float(settings["matched_score"]),
                    "assignment_status": "rule_matched", "role_evidence": evidence,
                    "role_explanation": explanation}
    return {"role": "peripheral", "role_score": float(settings["fallback_score"]),
            "assignment_status": "insufficient_evidence", "role_evidence": attempted,
            "role_explanation": (
                f"Недостаточно оснований для правил: входов={metrics['in_degree_unique']}, "
                f"выходов={metrics['out_degree_unique']}; I={metrics['in_amount_kzt']} KZT, "
                f"O={metrics['out_amount_kzt']} KZT. peripheral — остаточная метка, "
                "соответствие=0/100; изоляция и неполные наблюдения не определяют назначение."
            )}
