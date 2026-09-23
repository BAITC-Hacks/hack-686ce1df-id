"""Reproducible review ordering, not a statistical risk estimate."""

from decimal import Context, Decimal, MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, localcontext
import math

from ..contracts import Evidence

_CONTEXT = Context(prec=64, rounding=ROUND_HALF_EVEN, Emax=MAX_EMAX, Emin=MIN_EMIN)


def _sum_exact(a: str, b: str) -> Decimal:
    left, right = Decimal(a), Decimal(b)
    parts = [left.as_tuple(), right.as_tuple()]
    precision = max(len(p.digits) + p.exponent for p in parts) - min(p.exponent for p in parts) + 2
    with localcontext(_CONTEXT) as context:
        context.prec = max(64, precision)
        return left + right


def _normalized(value: Decimal, maximum: Decimal) -> float:
    if maximum == 0:
        return 0.0
    # Decimal avoids float overflow of valid exact money. Only the final score
    # is approximate. Context is local, independent of the caller's settings.
    with localcontext(_CONTEXT) as context:
        context.prec = max(64, 3 - min(value.adjusted(), maximum.adjusted()))
        return float((value + 1).ln() / (maximum + 1).ln())


def build_priorities(features: dict[str, dict], qualities: dict[str, dict], settings: dict) -> tuple[dict, dict]:
    values = {gid: {
        "volume_kzt": _sum_exact(m["in_amount_kzt"], m["out_amount_kzt"]),
        "degree": Decimal(m["in_degree_unique"] + m["out_degree_unique"]),
        "transactions": Decimal(m["tx_in_count"] + m["tx_out_count"]),
    } for gid, m in features.items()}
    maxima = {key: max((entry[key] for entry in values.values()), default=Decimal(0))
              for key in settings["weights"]}
    result, details = {}, {}
    for gid in sorted(features):
        normalized = {key: _normalized(values[gid][key], maxima[key]) for key in maxima}
        quality = qualities[gid]
        q = settings["quality"]["base"]
        evidence = []
        for flag, bonus in (("outbound_censored", "outgoing_complete_bonus"),
                            ("inbound_incomplete", "incoming_complete_bonus")):
            actual = quality[flag]
            if actual is False:
                q += settings["quality"][bonus]
            evidence.append(Evidence(rule_id=settings["rule_ids"]["quality"], metric=flag, operator="eq",
                                     actual=actual, threshold=False,
                                     passed=None if actual is None else actual is False))
        weighted = sum(settings["weights"][key] * normalized[key] for key in maxima)
        score = round(min(100.0, max(0.0, 100 * q * weighted)), settings["decimals"])
        for key in maxima:
            actual = format(values[gid][key], "f")
            threshold = format(maxima[key], "f")
            evidence.append(Evidence(rule_id=settings["rule_ids"]["normalization"], metric=key,
                                     operator="lte", actual=actual, threshold=threshold, passed=True))
        evidence.append(Evidence(rule_id=settings["rule_ids"]["formula"], metric="priority_score", operator="eq",
                                 actual=score, threshold=score, passed=True))
        expression = " + ".join(f"{settings['weights'][key]:g}×{normalized[key]:.6f}" for key in maxima)
        result[gid] = {"priority_score": score, "priority_evidence": evidence,
                       "priority_explanation": (
                           f"Приоритет={score:.4f}: 100×{q:.2f}×({expression}) (Q={q:.2f}). "
                           f"Объём I+O={format(values[gid]['volume_kzt'], 'f')} KZT; "
                           f"степень={values[gid]['degree']}; операций={values[gid]['transactions']}. "
                           "Нормировка ln(1+x)/ln(1+max) по всему запуску; "
                           "это порядок проверки масштаба, не вероятность нарушения."
                       )}
        details[gid] = {"values": {key: format(value, "f") for key, value in values[gid].items()},
                        "normalized": normalized, "quality_factor": q, "weighted_sum": weighted}
    return result, {"maxima": {key: format(value, "f") for key, value in maxima.items()},
                    "weights": dict(settings["weights"]), "formula": settings["formula"], "by_gid": details}
