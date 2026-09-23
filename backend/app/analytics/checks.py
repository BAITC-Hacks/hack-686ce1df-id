"""Pure, deterministic checks shared by analytics and the backend tools."""

from typing import Any

from .features import _ExactAmount, _edge, _finite_ratio, _gid


def check_concentration(gid: str, edges: list[Any]) -> dict:
    """Calculate concentration from the supplied, already policy-filtered edges.

    Mapping records and Pydantic-style attribute records are supported. Repeated
    source/target pairs are summed before choosing a receiver. Zero-amount edges
    still establish an observed receiver; no outgoing edges means no receiver.
    """
    gid = _gid(gid, "check_concentration.gid")
    receivers: dict[str, _ExactAmount] = {}
    total = _ExactAmount()
    for index, record in enumerate(edges):
        source, target, amount, _ = _edge(record, index)
        if source != gid:
            continue
        if target not in receivers:
            receivers[target] = _ExactAmount()
        receivers[target].add(amount)
        total.add(amount)

    # Iterate in gid order and replace only for strictly greater amounts, so a
    # tie has the same answer regardless of input order and Decimal precision.
    top_receiver = None
    top_amount = None
    for receiver in sorted(receivers):
        amount = receivers[receiver].decimal()
        if top_amount is None or amount > top_amount:
            top_receiver, top_amount = receiver, amount
    return {
        "gid": gid,
        "total_out_kzt": total.text(),
        "top_receiver_gid": top_receiver,
        "top_receiver_share": (
            _finite_ratio(top_amount, total.decimal(), f"concentration {gid!r}")
            if top_amount is not None else None
        ),
        "receiver_count": len(receivers),
    }
