"""Bounded card context, reversible evidence IDs and deterministic local fallback."""

from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import quote


BASE_LIMITATIONS = [
    "Выводы относятся только к наблюдаемой сети, окну и фильтрам исходного набора.",
    "Роли и назначение узлов — аналитические гипотезы; score не является вероятностью виновности.",
    "Суммы переводов не являются остатком на счёте и не доказывают движение тех же денег.",
]

SEMANTIC_LIMITATION = (
    "Проверены ссылки и числовые значения оснований; смысл и причинные выводы свободного текста требуют проверки аналитиком."
)


def unique(items):
    return list(dict.fromkeys(items))


def node_limitations(record: dict) -> list[str]:
    quality = record["quality"]
    items = list(quality["reasons"])
    if quality["outbound_censored"] is True:
        items.append("Исходящие ограничены глубиной обхода; наблюдаемый ноль не доказывает реального конечного получателя.")
    elif quality["outbound_censored"] is None:
        items.append("Полнота наблюдения исходящих неизвестна; реальный конечный получатель не установлен.")
    if quality["inbound_incomplete"] is True:
        items.append("Входящие наблюдаются неполно; суммы и отношения нельзя считать полной картиной потока.")
    elif quality["inbound_incomplete"] is None:
        items.append("Полнота наблюдения входящих неизвестна.")
    if quality["is_seed"] is None:
        items.append("Принадлежность к исходным seed неизвестна.")
    if quality["hop_depth"] is None:
        items.append("Глубина исходного обхода неизвестна.")
    if record["metrics"]["out_in_ratio"] is None:
        items.append("Отношение исходящих к входящим неприменимо или неизвестно; это не нулевое отношение.")
    if record["assignment_status"] == "insufficient_evidence":
        items.append("peripheral — техническая остаточная категория при недостатке данных, а не доказанная периферийность.")
    if record["metrics"]["out_degree_unique"] == 0:
        items.append("Нулевые исходящие в наблюдаемой сети не доказывают реального конечного получателя средств.")
    return unique(items)


def result_limitations(tool: str, result: dict) -> list[str]:
    if tool == "get_node":
        return node_limitations(result)
    if tool == "get_cluster":
        return result["hypothesis"]["limitations"]
    if tool == "get_neighbors":
        limits = []
        if result["truncated"]:
            limits.append(
                f"Окружение ограничено: показано {result['shown_nodes']} из {result['total_nodes']} узлов; "
                "метрики карточек относятся ко всему наблюдаемому графу."
            )
        for node in result["nodes"]:
            limits.extend(node_limitations(node))
        return unique(limits)
    if tool == "check_concentration" and result["top_receiver_share"] is None:
        return ["Доля крупнейшего получателя неприменима или неизвестна; это не нулевая концентрация."]
    return []


@dataclass
class Context:
    target: dict
    record: dict
    evidence: dict
    claims: list
    limitations: list


def build_context(target: dict, record: dict) -> Context:
    record = deepcopy(record)
    prefix = f"{target['kind']}:{quote(target['id'], safe='')}"
    evidence = {}
    if target["kind"] == "node":
        # Deliberately exclude unknown attributes (including any future personal data).
        fields = ("gid", "component_id", "cluster_id", "role", "role_score", "priority_score",
                  "assignment_status", "metrics", "quality", "role_explanation", "priority_explanation",
                  "role_evidence", "priority_evidence")
        record = {field: record[field] for field in fields}
        evidence[f"{prefix}:card"] = record
        evidence[f"{prefix}:metrics"] = record["metrics"]
        evidence[f"{prefix}:quality"] = record["quality"]
        claims = []
        for name in ("role", "priority"):
            evidence_id = f"{prefix}:{name}_explanation"
            evidence[evidence_id] = {"text": record[f"{name}_explanation"]}
            evidence_ids = [evidence_id]
            for i, item in enumerate(record[f"{name}_evidence"]):
                entry_id = f"{prefix}:{name}_evidence:{i}"
                evidence[entry_id] = item
                evidence_ids.append(entry_id)
            claims.append({"text": record[f"{name}_explanation"], "evidence_ids": evidence_ids})
        limits = node_limitations(record)
    else:
        record = {field: record[field] for field in ("cluster_id", "component_id", "gids", "hypothesis")}
        evidence[f"{prefix}:card"] = record
        evidence[f"{prefix}:hypothesis"] = record["hypothesis"]
        claims = [{"text": record["hypothesis"]["text"], "evidence_ids": [f"{prefix}:hypothesis"]}]
        limits = record["hypothesis"]["limitations"]
    return Context(deepcopy(target), record, evidence, claims, unique(BASE_LIMITATIONS + limits))


def fallback(run_id: str, context: Context | None, checks: list, reason: str, limitations: list) -> dict:
    claims = deepcopy(context.claims) if context else []
    if context:
        summary = "Локальная справка. " + " ".join(item["text"] for item in context.claims)
    else:
        summary = "Локальная карточка не получена в пределах времени запроса. Обновите данные и повторите запрос."
    for check in checks:
        claims.append({"text": f"Выполнена локальная проверка {check['tool']}; результат доступен в основаниях.",
                       "evidence_ids": [check["evidence_id"]]})
    return {"contract_version": "1.0", "run_id": run_id, "status": "fallback",
            "summary": summary, "claims": claims,
            "limitations": unique((context.limitations if context else BASE_LIMITATIONS) + limitations +
                                   ["Ответ модели не принят; показаны локальные объяснения и только завершённые проверки."]),
            "checks": deepcopy(checks), "fallback_reason": reason}
