from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.demo.models import AddNodeRequest, AddTransferRequest


def transfer(**changes):
    return dict(run_id="fixture-demo-test", request_id=str(uuid4()), source="0007",
                target="7", amount_kzt="5000.01", date="2026-07-01") | changes


@pytest.mark.parametrize("amount", ["4999.99", "1e10", "NaN", "5000.001", "1000000000000.01", 5000, True])
def test_invalid_money(amount):
    with pytest.raises(ValidationError):
        AddTransferRequest.model_validate(transfer(amount_kzt=amount))


@pytest.mark.parametrize("changes", [{"date": "2026-07-32"}, {"date": "2026-08-01"},
                                    {"target": "0007"}, {"request_id": "bad"}, {"extra": 1}])
def test_invalid_transfer(changes):
    with pytest.raises(ValidationError):
        AddTransferRequest.model_validate(transfer(**changes))


@pytest.mark.parametrize("name", ["", " " * 5, "a" * 121, "Айдана\nСадыкова", "A\x00B"])
def test_invalid_name(name):
    with pytest.raises(ValidationError):
        AddNodeRequest(run_id="fixture-demo-test", request_id=str(uuid4()), display_name=name)


def test_unicode_names_and_strict_quality():
    command = AddNodeRequest(run_id="fixture-demo-test", request_id=str(uuid4()),
                             display_name="  Айдана О'Нил-Садыкова  ", gid="0007")
    assert command.display_name == "Айдана О'Нил-Садыкова" and command.gid == "0007"
    with pytest.raises(ValidationError):
        command.quality.outbound_censored = False
    with pytest.raises(ValidationError):
        AddNodeRequest(run_id="x", request_id=str(uuid4()), display_name="Имя", quality={"hop_depth": True})
