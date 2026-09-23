"""Read-only, validated access to the functions supplied by ResultStore.

This module deliberately contains no analytical formulas or replacement Store.
Synchronous Store reads run in a worker thread so the caller can enforce its
single deadline. Cancelling a read does not stop an already running thread.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any


class ToolError(Exception):
    """A safe diagnostic for a rejected model tool or invalid Store result."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        self.message = reason
        super().__init__(reason)


class RequestError(Exception):
    """A request-level failure for B's HTTP error mapper."""

    def __init__(
        self, code: str, status_code: int, message: str, run_id: str | None = None
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.message = message
        self.run_id = run_id
        super().__init__(message)


def require_run(store: Any, run_id: str) -> None:
    """Reject absent data and prevent reads across different completed runs."""
    current = getattr(store, "run_id", None)
    if not isinstance(current, str) or not current.strip():
        raise RequestError("data_not_ready", 503, "Нет законченного запуска данных.", None)
    if not isinstance(run_id, str) or not run_id.strip():
        raise RequestError("invalid_request", 422, "run_id должен быть непустой строкой.", current)
    if current != run_id:
        raise RequestError(
            "stale_run", 409, "Данные обновились. Обновите выбранную карточку.", current
        )


def _json_value(value: Any) -> Any:
    if callable(getattr(value, "model_dump", None)):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolError("invalid_store_result", "Результат содержит нечисловое значение.")
        return float(value)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ToolError("invalid_store_result", "Ключи результата должны быть строками.")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ToolError("invalid_store_result", "Результат не соответствует JSON-контракту.")


def to_data(value: Any) -> dict[str, Any]:
    """Return an independent JSON-compatible object, including Pydantic models."""
    try:
        result = _json_value(value)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError("invalid_store_result", "Не удалось прочитать результат Store.") from exc
    if not isinstance(result, dict):
        raise ToolError("invalid_store_result", "Store должен вернуть объект.")
    return result


_ARGUMENTS = {
    "get_node": ("gid",),
    "get_neighbors": ("gid", "radius", "limit"),
    "get_cluster": ("cluster_id",),
    "check_concentration": ("gid",),
}


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strict_int(value: Any, minimum: int = 0, maximum: int | None = None) -> bool:
    return (
        type(value) is int
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant")


def validate_arguments(name: str, args: dict[str, Any] | str) -> dict[str, Any]:
    """Validate a fixed tool name and its exact argument schema before dispatch."""
    if not isinstance(name, str) or name not in _ARGUMENTS:
        raise ToolError("unknown_tool", "Модель запросила недоступную функцию.")
    if isinstance(args, str):
        try:
            args = json.loads(args, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ToolError("invalid_arguments", "Аргументы функции содержат некорректный JSON.") from exc
    if not isinstance(args, dict) or set(args) - set(_ARGUMENTS[name]):
        raise ToolError("invalid_arguments", "Аргументы функции не соответствуют контракту.")
    result = dict(args)
    id_key = "cluster_id" if name == "get_cluster" else "gid"
    if not _identifier(result.get(id_key)):
        raise ToolError("invalid_arguments", "Идентификатор должен быть непустой строкой.")
    if name == "get_neighbors":
        result.setdefault("radius", 1)
        result.setdefault("limit", 50)
        if not _strict_int(result["radius"], 1, 2):
            raise ToolError("invalid_arguments", "Радиус должен быть целым числом 1 или 2.")
        if not _strict_int(result["limit"], 1, 300):
            raise ToolError("invalid_arguments", "Лимит должен быть целым числом от 1 до 300.")
    return result


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI Responses strict function schemas; all model arguments are required."""
    descriptions = {
        "get_node": "Read the calculated node card in the current observed network.",
        "get_neighbors": "Read a bounded neighborhood; card metrics remain full-graph metrics.",
        "get_cluster": "Read a calculated cluster and its local hypothesis and limitations.",
        "check_concentration": "Run the existing analytical concentration check for a node.",
    }
    schemas = []
    for name, keys in _ARGUMENTS.items():
        properties: dict[str, Any] = {}
        for key in keys:
            if key == "radius":
                properties[key] = {"type": "integer", "enum": [1, 2]}
            elif key == "limit":
                properties[key] = {"type": "integer", "minimum": 1, "maximum": 300}
            else:
                properties[key] = {"type": "string", "minLength": 1}
        schemas.append({
            "type": "function",
            "name": name,
            "description": descriptions[name],
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(keys),
                "additionalProperties": False,
            },
        })
    return schemas


async def _read(store: Any, name: str, args: dict[str, Any], run_id: str) -> dict[str, Any]:
    require_run(store, run_id)
    method = getattr(store, name, None)
    if not callable(method):
        raise ToolError("store_unavailable", "Запрошенная функция Store ещё не подключена.")
    try:
        if inspect.iscoroutinefunction(method):
            result = await method(**args)
        else:
            result = await asyncio.to_thread(method, **args)
            if inspect.isawaitable(result):
                result = await result
    except RequestError:
        raise
    except Exception as exc:
        require_run(store, run_id)
        if isinstance(exc, KeyError) or getattr(exc, "status_code", None) == 404:
            raise ToolError("target_not_found", "Запрошенный объект отсутствует в текущем запуске.") from exc
        if isinstance(exc, ToolError):
            raise
        raise ToolError("store_error", "Не удалось выполнить проверку по данным Store.") from exc
    require_run(store, run_id)
    result = to_data(result)
    require_run(store, run_id)
    if "run_id" in result and result["run_id"] != run_id:
        raise RequestError("stale_run", 409, "Результат относится к другому запуску.", store.run_id)
    if "contract_version" in result and result["contract_version"] != "1.0":
        raise ToolError("invalid_store_result", "Версия результата не соответствует контракту.")
    return _project_result(name, result)


def _fail_result() -> None:
    raise ToolError("invalid_store_result", "Store вернул несовместимый результат проверки.")


def _match_id(result: dict[str, Any], field: str, expected: str) -> None:
    if result.get(field) != expected:
        _fail_result()


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _money(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.strip() != value:
        return False
    try:
        amount = Decimal(value)
        return amount.is_finite() and amount >= 0
    except InvalidOperation:
        return False


_NODE_FIELDS = (
    "gid", "component_id", "cluster_id", "role", "role_score", "priority_score",
    "assignment_status", "metrics", "quality", "role_evidence", "priority_evidence",
    "role_explanation", "priority_explanation",
)
_METRIC_FIELDS = (
    "in_degree_unique", "out_degree_unique", "tx_in_count", "tx_out_count",
    "in_amount_kzt", "out_amount_kzt", "out_in_ratio",
)
_QUALITY_FIELDS = ("is_seed", "hop_depth", "outbound_censored", "inbound_incomplete", "reasons")
_EVIDENCE_FIELDS = ("rule_id", "metric", "operator", "actual", "threshold", "passed")
_CLUSTER_FIELDS = ("cluster_id", "component_id", "gids", "hypothesis")
_HYPOTHESIS_FIELDS = ("text", "basis_rule_ids", "limitations")
_GRAPH_FIELDS = ("contract_version", "run_id", "nodes", "edges", "truncated", "total_nodes", "shown_nodes")
_EDGE_FIELDS = ("source", "target", "amount_kzt", "tx_count")
_CONCENTRATION_FIELDS = ("gid", "total_out_kzt", "top_receiver_gid", "top_receiver_share", "receiver_count")


def _select(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """Project required contract fields; never forward unknown Store attributes."""
    if not isinstance(record, dict) or any(field not in record for field in fields):
        _fail_result()
    return {field: record[field] for field in fields}


def _number(value: Any) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _evidence(record: Any) -> dict[str, Any]:
    result = _select(record, _EVIDENCE_FIELDS)
    if (
        not _identifier(result["rule_id"])
        or not _identifier(result["metric"])
        or not isinstance(result["operator"], str)
        or result["operator"] not in {"gt", "gte", "lt", "lte", "eq"}
        or type(result["actual"]) not in (str, int, float, bool, type(None))
        or type(result["threshold"]) not in (str, int, float, bool)
        or type(result["passed"]) not in (bool, type(None))
    ):
        _fail_result()
    return result


def _node(record: Any) -> dict[str, Any]:
    result = _select(record, _NODE_FIELDS)
    metrics = _select(result["metrics"], _METRIC_FIELDS)
    quality = _select(result["quality"], _QUALITY_FIELDS)
    if (
        any(not _identifier(result[field]) for field in ("gid", "component_id", "cluster_id"))
        or not isinstance(result["role"], str)
        or result["role"] not in {"consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"}
        or not isinstance(result["assignment_status"], str)
        or result["assignment_status"] not in {"rule_matched", "insufficient_evidence"}
        or any(not _number(result[field]) or not 0 <= result[field] <= 100 for field in ("role_score", "priority_score"))
        or any(not isinstance(result[field], str) for field in ("role_explanation", "priority_explanation"))
        or any(not _strict_int(metrics[field]) for field in _METRIC_FIELDS[:4])
        or any(not _money(metrics[field]) for field in ("in_amount_kzt", "out_amount_kzt"))
        or (metrics["out_in_ratio"] is not None and not _number(metrics["out_in_ratio"]))
        or any(type(quality[field]) not in (bool, type(None)) for field in ("is_seed", "outbound_censored", "inbound_incomplete"))
        or (quality["hop_depth"] is not None and not _strict_int(quality["hop_depth"]))
        or not _string_list(quality["reasons"])
    ):
        _fail_result()
    for field in ("role_evidence", "priority_evidence"):
        if not isinstance(result[field], list):
            _fail_result()
        result[field] = [_evidence(item) for item in result[field]]
    result["metrics"] = metrics
    result["quality"] = quality
    return result


def _project_result(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Minimize every nested tool result to the agreed contract v1 fields."""
    if name == "get_node":
        return _node(result)
    if name == "get_cluster":
        result = _select(result, _CLUSTER_FIELDS)
        result["hypothesis"] = _select(result["hypothesis"], _HYPOTHESIS_FIELDS)
        return result
    if name == "get_neighbors":
        result = _select(result, _GRAPH_FIELDS)
        if not isinstance(result["nodes"], list) or not isinstance(result["edges"], list):
            _fail_result()
        result["nodes"] = [_node(node) for node in result["nodes"]]
        result["edges"] = [_select(edge, _EDGE_FIELDS) for edge in result["edges"]]
        return result
    if name == "check_concentration":
        return _select(result, _CONCENTRATION_FIELDS)
    raise ToolError("unknown_tool", "Модель запросила недоступную функцию.")


def _validate_result(name: str, result: dict[str, Any], args: dict[str, Any], run_id: str) -> None:
    if name == "get_node":
        _match_id(result, "gid", args["gid"])
    elif name == "get_cluster":
        _match_id(result, "cluster_id", args["cluster_id"])
        hypothesis = result.get("hypothesis")
        gids = result.get("gids")
        if (
            not _identifier(result.get("component_id"))
            or not _string_list(gids)
            or any(not _identifier(gid) for gid in gids)
            or len(gids) != len(set(gids))
            or not isinstance(hypothesis, dict)
            or not isinstance(hypothesis.get("text"), str)
            or not _string_list(hypothesis.get("basis_rule_ids"))
            or not _string_list(hypothesis.get("limitations"))
        ):
            _fail_result()
    elif name == "check_concentration":
        _match_id(result, "gid", args["gid"])
        share = result.get("top_receiver_share")
        receiver = result.get("top_receiver_gid")
        if (
            not {"total_out_kzt", "top_receiver_gid", "top_receiver_share", "receiver_count"} <= result.keys()
            or not _money(result.get("total_out_kzt"))
            or (receiver is not None and not _identifier(receiver))
            or (share is not None and (type(share) not in (int, float) or not 0 <= share <= 1))
            or not _strict_int(result.get("receiver_count"))
        ):
            _fail_result()
    elif name == "get_neighbors":
        nodes, edges = result.get("nodes"), result.get("edges")
        if (
            result.get("contract_version") != "1.0"
            or result.get("run_id") != run_id
            or not isinstance(nodes, list)
            or not isinstance(edges, list)
            or type(result.get("truncated")) is not bool
            or not _strict_int(result.get("total_nodes"))
            or not _strict_int(result.get("shown_nodes"))
        ):
            _fail_result()
        if any(not isinstance(node, dict) or not _identifier(node.get("gid")) for node in nodes):
            _fail_result()
        gids = {node["gid"] for node in nodes}
        if (
            len(gids) != len(nodes)
            or args["gid"] not in gids
            or len(nodes) != result["shown_nodes"]
            or len(nodes) > args["limit"]
            or result["total_nodes"] < len(nodes)
            or result["truncated"] != (result["total_nodes"] > len(nodes))
        ):
            _fail_result()
        pairs = set()
        for edge in edges:
            if not isinstance(edge, dict):
                _fail_result()
            source, target = edge.get("source"), edge.get("target")
            if (
                not _identifier(source)
                or not _identifier(target)
                or source not in gids
                or target not in gids
                or not _money(edge.get("amount_kzt"))
                or not _strict_int(edge.get("tx_count"))
                or (source, target) in pairs
            ):
                _fail_result()
            pairs.add((source, target))


async def execute_tool(
    name: str, args: dict[str, Any] | str, store: Any, run_id: str
) -> dict[str, Any]:
    """Execute one model-selected tool after validation; never mutate the Store."""
    arguments = validate_arguments(name, args)
    require_run(store, run_id)
    if name in {"get_neighbors", "check_concentration"}:
        # Existence validation is a Store read, not an additional model tool call.
        node = await _read(store, "get_node", {"gid": arguments["gid"]}, run_id)
        _match_id(node, "gid", arguments["gid"])
    result = await _read(store, name, arguments, run_id)
    _validate_result(name, result, arguments, run_id)
    require_run(store, run_id)
    return result
