"""Strict commands and recursively immutable canonical demo records."""

from datetime import date as calendar_date
from typing import Annotated, Literal
import re
import unicodedata
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Gid = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Count = Annotated[int, Field(ge=0)]
MIN_AMOUNT_TIYN = 500_000
MAX_AMOUNT_TIYN = 100_000_000_000_000


def money_text(tiyn: int) -> str:
    return f"{tiyn // 100}.{tiyn % 100:02d}"


def money_tiyn(value: str) -> int:
    whole, _, fraction = value.partition(".")
    return int(whole) * 100 + int(fraction.ljust(2, "0"))


def printable_name(value: str, limit: int) -> str:
    value = value.strip()
    if not 1 <= len(value) <= limit or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError(f"Введите от 1 до {limit} символов без управляющих знаков")
    return value


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False,
                              json_schema_serialization_defaults_required=True)


class InputQuality(FrozenModel):
    is_seed: bool | None = None
    hop_depth: Count | None = None
    outbound_censored: bool | None = None
    inbound_incomplete: bool | None = None


class SourceQuality(InputQuality):
    reasons: tuple[str, ...] = ()


def source_quality(quality: InputQuality) -> SourceQuality:
    reasons = []
    for field, label in (("outbound_censored", "Полнота исходящих"),
                         ("inbound_incomplete", "Полнота входящих")):
        value = getattr(quality, field)
        reasons.append(f"{label}: " + ("неизвестна" if value is None else "ограничена" if value else "подтверждена в демоокне"))
    if quality.is_seed:
        reasons.append("Исходный узел синтетического обхода")
    if quality.hop_depth is not None:
        reasons.append(f"Глубина синтетического обхода: {quality.hop_depth}")
    return SourceQuality(**quality.model_dump(), reasons=tuple(reasons))


class NodeName(FrozenModel):
    gid: Gid
    display_name: str

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return printable_name(value, 120)


class SourceGroup(FrozenModel):
    id: Identifier
    name: str

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return printable_name(value, 80)


class SourceNode(NodeName):
    group_id: Identifier
    quality: SourceQuality = Field(default_factory=SourceQuality)


class TransferFields(FrozenModel):
    source: Gid
    target: Gid
    amount_kzt: str
    date: str

    @field_validator("amount_kzt")
    @classmethod
    def valid_money(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9]{1,13}(?:\.[0-9]{1,2})?", value):
            raise ValueError("Сумма должна быть десятичной строкой с точностью до двух знаков")
        amount = money_tiyn(value)
        if not MIN_AMOUNT_TIYN <= amount <= MAX_AMOUNT_TIYN:
            raise ValueError("Сумма должна быть от 5000 до 1000000000000 KZT")
        return money_text(amount)

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        if not re.fullmatch(r"2026-07-[0-9]{2}", value):
            raise ValueError("Дата должна быть в июле 2026, формат YYYY-MM-DD")
        calendar_date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def no_self_transfer(self):
        if self.source == self.target:
            raise ValueError("Перевод самому себе не поддерживается в демо")
        return self


class SourceTransfer(TransferFields):
    id: Identifier


class Command(FrozenModel):
    run_id: Identifier
    request_id: str

    @field_validator("request_id")
    @classmethod
    def valid_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value.lower():
            raise ValueError("request_id должен быть UUID в стандартном формате")
        return value.lower()


class ExistingGroup(FrozenModel):
    kind: Literal["existing"]
    id: Identifier


class NewGroup(FrozenModel):
    kind: Literal["new"]
    name: str

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return printable_name(value, 80)


class AddNodeRequest(Command):
    display_name: str
    gid: Gid | None = None
    group: Annotated[ExistingGroup | NewGroup, Field(discriminator="kind")] | None = None
    quality: InputQuality = Field(default_factory=InputQuality)

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return printable_name(value, 120)


class AddTransferRequest(Command, TransferFields):
    pass


class MutationResponse(FrozenModel):
    contract_version: Literal["1.0"] = "1.0"
    run_id: Identifier
    created_id: Identifier
    select_gid: Gid
    node_count: Count
    transfer_count: Count


class AcceptedRequest(FrozenModel):
    request_id: str
    request_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    response: MutationResponse


class DemoSource(FrozenModel):
    seed: int
    nodes: tuple[SourceNode, ...]
    transfers: tuple[SourceTransfer, ...]
    groups: tuple[SourceGroup, ...]
    accepted_requests: tuple[AcceptedRequest, ...] = ()

    @model_validator(mode="after")
    def coherent_source(self):
        gids = {n.gid for n in self.nodes}
        groups = {g.id for g in self.groups}
        if len(gids) != len(self.nodes) or len(groups) != len(self.groups):
            raise ValueError("Duplicate node or group identifier")
        if any(n.group_id not in groups for n in self.nodes):
            raise ValueError("Node references an unknown group")
        if len({t.id for t in self.transfers}) != len(self.transfers):
            raise ValueError("Duplicate transfer identifier")
        if any(t.source not in gids or t.target not in gids for t in self.transfers):
            raise ValueError("Transfer references an unknown node")
        if len({r.request_id for r in self.accepted_requests}) != len(self.accepted_requests):
            raise ValueError("Duplicate accepted request identifier")
        return self


class DemoLimits(FrozenModel):
    date_from: Literal["2026-07-01"] = "2026-07-01"
    date_to: Literal["2026-07-31"] = "2026-07-31"
    min_amount_kzt: Literal["5000.00"] = "5000.00"
    max_amount_kzt: Literal["1000000000000.00"] = "1000000000000.00"


class DemoInfoResponse(FrozenModel):
    contract_version: Literal["1.0"] = "1.0"
    run_id: Identifier | None
    enabled: bool
    node_count: Count = 0
    transfer_count: Count | None = None
    nodes: tuple[NodeName, ...] = ()
    groups: tuple[SourceGroup, ...] = ()
    limits: DemoLimits = Field(default_factory=DemoLimits)
