"""The seven v1 metrics, calculated over the complete supplied graph.

Self-transfer policy belongs to the caller: an edge in this input contributes to
both its source and target, including when they are the same node. Monetary sums
use integer coefficients extracted from Decimal so ambient Decimal precision
cannot round an otherwise valid amount.
"""

from collections.abc import Mapping
from decimal import Context, Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN
import math
import re
from typing import Any


_DECIMAL_STRING = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
# A resource limit on expanded representation, not a business monetary cutoff.
# Checking before coefficient scaling prevents compact inputs such as 1e1000000000
# from requesting unbounded integer allocation. Each side permits 1000 digits.
_MAX_INTEGER_DIGITS = 1000
_MAX_FRACTIONAL_SCALE = 1000


def _field(record: Any, name: str, location: str) -> Any:
    """Read either a mapping or an attribute-based contract record."""
    try:
        return record[name] if isinstance(record, Mapping) else getattr(record, name)
    except (KeyError, AttributeError, TypeError) as exc:
        raise ValueError(f"{location}: missing required field {name!r}") from exc


def _gid(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{location}: gid must be a nonempty string without surrounding whitespace")
    return value


def _amount(value: Any, location: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL_STRING.fullmatch(value):
        raise ValueError(f"{location}: amount_kzt must be a finite nonnegative decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{location}: invalid decimal amount_kzt") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{location}: amount_kzt must be finite and nonnegative")
    parts = amount.as_tuple()
    integer_digits = max(1, len(parts.digits) + parts.exponent)
    fractional_scale = max(0, -parts.exponent)
    if integer_digits > _MAX_INTEGER_DIGITS or fractional_scale > _MAX_FRACTIONAL_SCALE:
        raise ValueError(
            f"{location}: amount_kzt exceeds representation resource limit "
            f"({_MAX_INTEGER_DIGITS} expanded integer digits, "
            f"{_MAX_FRACTIONAL_SCALE} fractional digits)"
        )
    return amount


def _edge(record: Any, index: int) -> tuple[str, str, Decimal, int]:
    location = f"edges[{index}]"
    source = _gid(_field(record, "source", location), f"{location}.source")
    target = _gid(_field(record, "target", location), f"{location}.target")
    amount = _amount(_field(record, "amount_kzt", location), location)
    tx_count = _field(record, "tx_count", location)
    if isinstance(tx_count, bool) or not isinstance(tx_count, int) or tx_count < 0:
        raise ValueError(f"{location}: tx_count must be a nonnegative integer")
    return source, target, amount, tx_count


class _ExactAmount:
    """An arbitrary precision sum retaining the most precise input scale."""

    __slots__ = ("coefficient", "exponent")

    def __init__(self) -> None:
        self.coefficient = 0
        self.exponent = 0

    def add(self, amount: Decimal) -> None:
        parts = amount.as_tuple()
        coefficient = 0
        for digit in parts.digits:
            coefficient = coefficient * 10 + digit
        exponent = parts.exponent
        # _amount has already excluded infinities, NaN and negative values.
        if exponent < self.exponent:
            self.coefficient *= 10 ** (self.exponent - exponent)
            self.exponent = exponent
        self.coefficient += coefficient * 10 ** (exponent - self.exponent)

    def decimal(self) -> Decimal:
        digits = tuple(int(digit) for digit in str(self.coefficient))
        return Decimal((0, digits, self.exponent))

    def text(self) -> str:
        return format(self.decimal(), "f")


def _finite_ratio(numerator: Decimal, denominator: Decimal, location: str) -> float | None:
    if denominator == 0:
        return None
    # Ratios are JSON numbers, not the exact monetary source of truth. Keep
    # enough significant digits for a float, independent of caller context.
    context = Context(
        prec=28, rounding=ROUND_HALF_EVEN, Emax=MAX_EMAX, Emin=MIN_EMIN,
        capitals=1, clamp=0, flags=[], traps=[],
    )
    result = float(context.divide(numerator, denominator))
    if not math.isfinite(result):
        raise ValueError(f"{location}: ratio cannot be represented as a finite JSON number")
    if numerator > 0 and result == 0:
        raise ValueError(f"{location}: positive ratio underflows to zero as a JSON number")
    return result


def build_features(nodes: list[dict], edges: list[Any]) -> dict[str, dict]:
    """Return exactly the seven contract metrics for every supplied node.

    Counts sum ``tx_count`` rather than counting aggregate rows. Degrees count
    distinct counterparties, including zero-amount and self-loop edges supplied
    by the caller. Missing endpoints and duplicate node IDs are explicit errors.
    ``quality`` is deliberately not used to guess unobserved transactions.
    """
    state: dict[str, dict] = {}
    for index, node in enumerate(nodes):
        gid = _gid(_field(node, "gid", f"nodes[{index}]"), f"nodes[{index}].gid")
        if gid in state:
            raise ValueError(f"nodes[{index}]: duplicate gid {gid!r}")
        state[gid] = {
            "senders": set(),
            "receivers": set(),
            "tx_in_count": 0,
            "tx_out_count": 0,
            "in_amount": _ExactAmount(),
            "out_amount": _ExactAmount(),
        }

    for index, record in enumerate(edges):
        source, target, amount, tx_count = _edge(record, index)
        if source not in state or target not in state:
            unknown = source if source not in state else target
            raise ValueError(f"edges[{index}]: unknown endpoint gid {unknown!r}")
        outgoing, incoming = state[source], state[target]
        outgoing["receivers"].add(target)
        outgoing["tx_out_count"] += tx_count
        outgoing["out_amount"].add(amount)
        incoming["senders"].add(source)
        incoming["tx_in_count"] += tx_count
        incoming["in_amount"].add(amount)

    result: dict[str, dict] = {}
    for gid, entry in state.items():
        result[gid] = {
            "in_degree_unique": len(entry["senders"]),
            "out_degree_unique": len(entry["receivers"]),
            "tx_in_count": entry["tx_in_count"],
            "tx_out_count": entry["tx_out_count"],
            "in_amount_kzt": entry["in_amount"].text(),
            "out_amount_kzt": entry["out_amount"].text(),
            "out_in_ratio": _finite_ratio(
                entry["out_amount"].decimal(), entry["in_amount"].decimal(), f"node {gid!r}"
            ),
        }
    return result
