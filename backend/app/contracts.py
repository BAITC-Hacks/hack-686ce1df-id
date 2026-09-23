"""Canonical data and HTTP models for docs/contracts/money-graph-v1.md."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

CONTRACT_VERSION = "1.0"
Identifier = Annotated[str, Field(min_length=1, strict=True)]
Count = Annotated[int, Field(ge=0, strict=True)]
FiniteNumber = Annotated[float, Field(allow_inf_nan=False, strict=True)]
Score = Annotated[FiniteNumber, Field(ge=0, le=100)]
Money = Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$", strict=True)]
Role = Literal["consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False,
        json_schema_serialization_defaults_required=True,
    )


class Evidence(ContractModel):
    rule_id: Identifier
    metric: Identifier
    operator: Literal["gt", "gte", "lt", "lte", "eq"]
    actual: FiniteNumber | str | bool | None
    threshold: FiniteNumber | str | bool
    passed: bool | None


class NodeMetrics(ContractModel):
    in_degree_unique: Count
    out_degree_unique: Count
    tx_in_count: Count
    tx_out_count: Count
    in_amount_kzt: Money
    out_amount_kzt: Money
    out_in_ratio: FiniteNumber | None


class NodeQuality(ContractModel):
    is_seed: bool | None
    hop_depth: Count | None
    outbound_censored: bool | None
    inbound_incomplete: bool | None
    reasons: list[str]


class NodeRecord(ContractModel):
    gid: Identifier
    component_id: Identifier
    cluster_id: Identifier
    role: Role
    role_score: Score
    priority_score: Score
    assignment_status: Literal["rule_matched", "insufficient_evidence"]
    metrics: NodeMetrics
    quality: NodeQuality
    role_evidence: list[Evidence]
    priority_evidence: list[Evidence]
    role_explanation: str
    priority_explanation: str


class EdgeRecord(ContractModel):
    source: Identifier
    target: Identifier
    amount_kzt: Money
    tx_count: Count


class ClusterHypothesis(ContractModel):
    text: str
    basis_rule_ids: list[str]
    limitations: list[str]


class ClusterRecord(ContractModel):
    cluster_id: Identifier
    component_id: Identifier
    gids: list[Identifier]
    hypothesis: ClusterHypothesis


class RunManifest(ContractModel):
    contract_version: Literal["1.0"] = CONTRACT_VERSION
    run_id: Identifier
    status: Literal["complete"]
    input_hashes: dict[str, str]
    rules_hash: str
    counts: dict[str, Count]
    elapsed_seconds: Annotated[FiniteNumber, Field(ge=0)]
    stage_seconds: dict[str, Annotated[FiniteNumber, Field(ge=0)]]
    random_seed: int
    versions: dict[str, str]
    files: dict[str, str]
    warnings: list[str]


class RunResponse(ContractModel):
    contract_version: Literal["1.0"] = CONTRACT_VERSION
    run_id: Identifier


class HealthResponse(ContractModel):
    status: Literal["ok"] = "ok"
    contract_version: Literal["1.0"] = CONTRACT_VERSION
    run_id: Identifier | None
    data_ready: bool


class NodeResponse(RunResponse):
    node: NodeRecord


class PrioritiesResponse(RunResponse):
    items: list[NodeRecord]
    total: Count


class ClustersResponse(RunResponse):
    items: list[ClusterRecord]


class ClusterResponse(RunResponse):
    cluster: ClusterRecord


class GraphResponse(RunResponse):
    nodes: list[NodeRecord]
    edges: list[EdgeRecord]
    truncated: bool
    total_nodes: Count
    shown_nodes: Annotated[Count, Field(le=300)]


class Target(ContractModel):
    kind: Literal["node", "cluster"]
    id: Identifier


class ExplainRequest(ContractModel):
    run_id: Identifier
    target: Target


class InvestigateRequest(ExplainRequest):
    question: Annotated[str, Field(min_length=1, pattern=r"\S")]


class AIClaim(ContractModel):
    text: str
    evidence_ids: list[str]


class AICheck(ContractModel):
    tool: str
    args: dict[str, JsonValue]
    result: dict[str, JsonValue]
    evidence_id: str


class AIResponse(RunResponse):
    status: Literal["ok", "fallback"]
    summary: str
    claims: list[AIClaim]
    limitations: list[str]
    checks: list[AICheck]
    fallback_reason: str | None


class ErrorDetail(ContractModel):
    code: str
    message: str


class ErrorResponse(ContractModel):
    error: ErrorDetail
    run_id: Identifier | None
