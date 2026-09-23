"""Conservative checks for the model-owned fragment of an AI response.

These checks establish schema conformance, reference existence and support for
literal numbers. They do not establish the semantic truth of prose, causation,
the meaning of a cited metric, or numbers written out as words. The server owns
run_id, status, checks and mandatory limitations.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any


MAX_TEXT_LENGTH = 2000
MAX_CLAIMS = 20
MAX_LIMITATIONS = 20
MAX_EVIDENCE_REFS = 12
MAX_EVIDENCE_ID_LENGTH = 500


class ValidationError(Exception):
    """A safe diagnostic code, never model text or private evidence."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def response_schema(run_id: str) -> dict[str, Any]:
    """Return the strict JSON schema; run_id intentionally remains server-owned.

    The provider wraps this schema in its named ``strict: true`` format. Keeping
    the run argument makes the request scope explicit without asking the model
    to reproduce a trusted identifier.
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a nonempty string")
    text_schema = {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "claims", "limitations"],
        "properties": {
            "summary": dict(text_schema),
            "claims": {
                "type": "array",
                "maxItems": MAX_CLAIMS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text", "evidence_ids"],
                    "properties": {
                        "text": dict(text_schema),
                        "evidence_ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": MAX_EVIDENCE_REFS,
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_EVIDENCE_ID_LENGTH,
                            },
                        },
                    },
                },
            },
            "limitations": {
                "type": "array",
                "maxItems": MAX_LIMITATIONS,
                "items": dict(text_schema),
            },
        },
    }


_OPAQUE_KEYS = {
    "gid", "gids", "id", "ids", "source", "target", "run_id", "rule_id",
    "cluster_id", "component_id", "top_receiver_gid", "evidence_id",
    "evidence_ids", "basis_rule_ids", "contract_version", "input_hashes",
    "rules_hash", "versions", "files",
}
_SOURCE_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?\Z")
_NUMBER = re.compile(
    r"[+\-−]?(?:[0-9]+(?:[., \u00a0\u202f][0-9]+)*|[.,][0-9]+)"
    r"(?:[eE][+\-]?[0-9]+)?"
)
_PERCENT = re.compile(
    r"\s*(?:[%％]|percent(?:age)?s?\b|pct\b|"
    r"процент(?:а|ов|ы|у|е|ом|ами|ах)?\b)", re.IGNORECASE
)
_SCALED_OR_POINTS = re.compile(
    r"\s*(?:тыс(?:\.|яч\w*)?|млн\.?|млрд\.?|миллион\w*|миллиард\w*|"
    r"thousand\w*|million\w*|billion\w*|[kкmмb]\b|"
    r"процентн\w*\s+пункт\w*|percentage\s+points?\b|pp\b)", re.IGNORECASE
)
_REAL_TERMINAL = (
    re.compile(
        r"\b(?:это|узел|клиент)\s+(?:является\s+)?"
        r"(?:реальн\w*|доказанн\w*|подтвержд[её]нн\w*|установленн\w*)"
        r"\s+конечн\w*\s+получател\w*", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:this|the node|the recipient)\s+is\s+(?:definitely\s+)?"
        r"(?:the\s+)?(?:real|proven|confirmed|actual)\s+"
        r"(?:final|ultimate)\s+(?:recipient|beneficiary)\b", re.IGNORECASE
    ),
)


def _json_value(value: Any, depth: int = 0) -> None:
    """Reject non-JSON types and non-finite numbers before interpreting data."""
    if depth > 32:
        raise ValidationError("invalid_json_value")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValidationError("non_finite_number")
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise ValidationError("invalid_json_value")


def _text(value: Any) -> str:
    if type(value) is not str or not value.strip() or len(value) > MAX_TEXT_LENGTH:
        raise ValidationError("invalid_text")
    if any(pattern.search(value) for pattern in _REAL_TERMINAL):
        raise ValidationError("unsupported_terminal_certainty")
    return value


def _ratio_field(key: str) -> bool:
    return key in {"ratio", "share"} or key.endswith(("_ratio", "_share"))


def _decimal(value: str) -> Decimal:
    # Bound exponent/digit sizes before arithmetic on provider-controlled text.
    if len(value) > 100:
        raise ValidationError("ambiguous_number")
    try:
        number = Decimal(value)
        if not number.is_finite() or abs(number.adjusted()) > 100:
            raise ValidationError("ambiguous_number")
        return number
    except (InvalidOperation, ValueError):
        raise ValidationError("ambiguous_number") from None


def _evidence_numbers(value: Any) -> tuple[set[Decimal], set[Decimal]]:
    """Collect typed quantities; decimal strings are allowed only for money.

    Evidence.actual/threshold inherit the Evidence.metric label. A metric's
    decimal amount can be cited, but digits inside identifiers or explanations
    cannot accidentally become support for a numeric assertion.
    """
    numbers: set[Decimal] = set()
    ratios: set[Decimal] = set()

    def visit(item: Any, key: str = "", metric: str = "") -> None:
        if key in _OPAQUE_KEYS or key.endswith(("_id", "_ids", "_gid", "_gids")):
            return
        label = metric if key in {"actual", "threshold"} else key
        if type(item) in (int, float):
            number = _decimal(str(item))
            numbers.add(number)
            if _ratio_field(label):
                ratios.add(number)
        elif type(item) is str and label.endswith("_kzt"):
            if not _SOURCE_DECIMAL.fullmatch(item):
                raise ValidationError("invalid_evidence_number")
            numbers.add(_decimal(item))
        elif type(item) is dict:
            local_metric = item.get("metric", "")
            if type(local_metric) is not str:
                local_metric = ""
            for child_key, child in item.items():
                visit(child, child_key, local_metric)
        elif type(item) is list:
            for child in item:
                visit(child, key, metric)

    visit(value)
    return numbers, ratios


def _parse_literal(token: str) -> Decimal:
    token = token.replace("−", "-").replace("\u00a0", " ").replace("\u202f", " ")
    mantissa, *exponent = re.split("[eE]", token)
    if "." in mantissa and "," in mantissa:
        raise ValidationError("ambiguous_number")
    if mantissa.count(".") > 1 or mantissa.count(",") > 1:
        raise ValidationError("ambiguous_number")
    separator = "," if "," in mantissa else "."
    integer, *fraction = mantissa.split(separator)
    if " " in integer:
        if not re.fullmatch(r"[+-]?[0-9]{1,3}(?: [0-9]{3})+", integer):
            raise ValidationError("ambiguous_number")
    if fraction and " " in fraction[0]:
        raise ValidationError("ambiguous_number")
    # A comma followed by three digits could mean a thousands separator.
    # Decimal-dot is the canonical contract notation; decimal-comma otherwise
    # remains useful for Russian prose (e.g. 0,7 or 70,00).
    if separator == "," and fraction and len(fraction[0]) == 3:
        raise ValidationError("ambiguous_number")
    normalized = mantissa.replace(" ", "").replace(",", ".")
    if exponent:
        normalized += "e" + exponent[0]
    return _decimal(normalized)


def _validate_numbers(text: str, numbers: set[Decimal], ratios: set[Decimal]) -> None:
    if any(character.isdigit() and character not in "0123456789" for character in text):
        raise ValidationError("ambiguous_number")
    if re.search(r"[0-9]\s*[/_:']\s*[0-9]", text):
        raise ValidationError("ambiguous_number")
    for match in _NUMBER.finditer(text):
        # Avoid interpreting malformed scientific/decimal notation as several
        # unrelated supported numbers (e.g. 1e+ or 1..2).
        before = text[match.start() - 1:match.start()] if match.start() else ""
        after = text[match.end():match.end() + 1]
        if before in {".", ","} or after in {"e", "E"}:
            raise ValidationError("ambiguous_number")
        number = _parse_literal(match.group())
        if _SCALED_OR_POINTS.match(text, match.end()):
            raise ValidationError("ambiguous_number")
        if _PERCENT.match(text, match.end()):
            # Shift the decimal exponent directly, without rounding quantities
            # that have more digits than Decimal's current arithmetic context.
            parts = number.as_tuple()
            ratio = Decimal((parts.sign, parts.digits, parts.exponent - 2))
            if ratio not in ratios:
                raise ValidationError("unsupported_percentage")
        elif number not in numbers:
            raise ValidationError("unsupported_number")


def validate_response(payload: dict[str, Any], evidence: dict[str, dict]) -> dict[str, Any]:
    """Validate a model fragment and return a fresh, server-safe copy.

    Numbers are scoped to the evidence cited by each individual claim. Summary
    and model-written limitations may not contain digits; numeric observations
    belong in claims, where they can have references. Null and booleans never
    support zero/one, and a percentage needs a ratio/share metric specifically.
    Conservative false negatives deliberately cause the caller's local fallback.
    """
    _json_value(payload)
    if type(payload) is not dict or set(payload) != {"summary", "claims", "limitations"}:
        raise ValidationError("invalid_response_fields")
    if type(evidence) is not dict or any(
        type(key) is not str or type(value) is not dict for key, value in evidence.items()
    ):
        raise ValidationError("invalid_evidence")
    summary = _text(payload["summary"])
    claims = payload["claims"]
    limitations = payload["limitations"]
    if type(claims) is not list or len(claims) > MAX_CLAIMS:
        raise ValidationError("invalid_claims")
    if type(limitations) is not list or len(limitations) > MAX_LIMITATIONS:
        raise ValidationError("invalid_limitations")
    clean_limitations = [_text(item) for item in limitations]
    if any(character.isdigit() for item in [summary, *clean_limitations] for character in item):
        raise ValidationError("uncited_number")

    clean_claims = []
    evidence_cache: dict[str, tuple[set[Decimal], set[Decimal]]] = {}
    for claim in claims:
        if type(claim) is not dict or set(claim) != {"text", "evidence_ids"}:
            raise ValidationError("invalid_claim_fields")
        claim_text = _text(claim["text"])
        refs = claim["evidence_ids"]
        if type(refs) is not list or not 1 <= len(refs) <= MAX_EVIDENCE_REFS:
            raise ValidationError("invalid_evidence_refs")
        if any(type(ref) is not str or not ref or len(ref) > MAX_EVIDENCE_ID_LENGTH for ref in refs):
            raise ValidationError("invalid_evidence_refs")
        if len(set(refs)) != len(refs) or any(ref not in evidence for ref in refs):
            raise ValidationError("invalid_evidence_refs")
        numbers: set[Decimal] = set()
        ratios: set[Decimal] = set()
        for ref in refs:
            if ref not in evidence_cache:
                _json_value(evidence[ref])
                evidence_cache[ref] = _evidence_numbers(evidence[ref])
            ref_numbers, ref_ratios = evidence_cache[ref]
            numbers.update(ref_numbers)
            ratios.update(ref_ratios)
        _validate_numbers(claim_text, numbers, ratios)
        clean_claims.append({"text": claim_text, "evidence_ids": list(refs)})

    return {"summary": summary, "claims": clean_claims, "limitations": clean_limitations}
