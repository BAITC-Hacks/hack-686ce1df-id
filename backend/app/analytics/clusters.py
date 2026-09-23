"""Deterministic Louvain communities within observed weak components."""

from collections import Counter
import hashlib
import json

import networkx as nx

from ..contracts import ClusterHypothesis, ClusterRecord


def _stable_id(prefix: str, gids: list[str]) -> str:
    serialized = json.dumps(sorted(gids), ensure_ascii=False, separators=(",", ":")).encode()
    return f"{prefix}-{hashlib.sha256(serialized).hexdigest()[:16]}"


def build_clusters(nodes: list[dict], edges: list[dict], roles: dict[str, dict], rules: dict) -> tuple[dict, list[ClusterRecord], dict]:
    directed = nx.DiGraph()
    directed.add_nodes_from(sorted(node["gid"] for node in nodes))
    pairs: dict[tuple[str, str], int] = {}
    for edge in sorted(edges, key=lambda item: (item["source"], item["target"])):
        source, target = edge["source"], edge["target"]
        directed.add_edge(source, target)
        pair = tuple(sorted((source, target)))
        pairs[pair] = pairs.get(pair, 0) + edge["tx_count"]
    components = sorted((sorted(part) for part in nx.weakly_connected_components(directed)), key=tuple)
    membership, clusters, component_details = {}, [], []
    settings, hypothesis_rules = rules["clustering"], rules["cluster_hypotheses"]
    for component in components:
        component_id = _stable_id("component", component)
        projection = nx.Graph()
        projection.add_nodes_from(component)
        component_set = set(component)
        projection.add_weighted_edges_from((source, target, count) for (source, target), count in sorted(pairs.items())
                                           if source in component_set and count > 0)
        communities = []
        for positive_component in sorted((sorted(part) for part in nx.connected_components(projection)), key=tuple):
            if len(positive_component) == 1:
                communities.append(positive_component)
            else:
                subgraph = projection.subgraph(positive_component).copy()
                communities.extend(sorted(part) for part in nx.community.louvain_communities(
                    subgraph, weight="weight", resolution=settings["resolution"],
                    threshold=settings["threshold"], seed=rules["random_seed"]))
        for gids in sorted(communities, key=tuple):
            cluster_id = _stable_id("cluster", gids)
            counts = Counter(roles[gid]["role"] for gid in gids)
            internal = directed.subgraph(gids)
            hub_gid = min(gids, key=lambda gid: (-internal.degree(gid), gid))
            hub_degree = internal.degree(hub_gid)
            n = len(gids)
            if n == 1:
                rule_id, text = hypothesis_rules["rule_ids"]["singleton"], "Один узел; назначение не установлено."
            elif counts["coordinator"] >= hypothesis_rules["coordinator_min_count"]:
                rule_id = hypothesis_rules["rule_ids"]["coordinator"]
                text = f"Структура с двусторонним сбором и распределением: coordinator={counts['coordinator']}/{n}."
            elif counts["transit"] / n >= hypothesis_rules["transit_min_fraction"]:
                rule_id = hypothesis_rules["rule_ids"]["transit"]
                text = f"Наблюдаемая транзитная структура: transit={counts['transit']}/{n} ({counts['transit']/n:.3f})."
            elif counts["terminal"] / n >= hypothesis_rules["terminal_min_fraction"]:
                rule_id = hypothesis_rules["rule_ids"]["terminal"]
                text = f"Структура получателей без наблюдаемых исходящих: terminal={counts['terminal']}/{n} ({counts['terminal']/n:.3f})."
            else:
                rule_id, text = hypothesis_rules["rule_ids"]["unresolved"], f"Назначение не установлено; узлов={n}."
            text += f" Внутренних направленных связей={internal.number_of_edges()}; узел {hub_gid}: внутренняя степень={hub_degree}."
            clusters.append(ClusterRecord(cluster_id=cluster_id, component_id=component_id, gids=gids,
                hypothesis=ClusterHypothesis(text=text, basis_rule_ids=[rule_id], limitations=[
                    "Louvain применён к симметризованному графу с суммой количеств переводов; направление в нём не учитывается.",
                    "Полнота входящих извне выборки не установлена; исходящие на границе обхода обрезаны.",
                    "Сообщество не доказывает единую организацию, назначение счёта или нарушение.",
                    "Кластер из одного узла не обязательно является изолятом исходного графа.",
                ])))
            for gid in gids:
                membership[gid] = {"component_id": component_id, "cluster_id": cluster_id}
        component_details.append({"component_id": component_id, "node_count": len(component),
                                  "cluster_count": len(communities)})
    clusters.sort(key=lambda item: item.cluster_id)
    if len({item.cluster_id for item in clusters}) != len(clusters):
        raise ValueError("cluster identity collision")
    return membership, clusters, {
        "algorithm": "networkx.louvain_communities", "networkx_version": nx.__version__,
        "projection": "undirected sum of tx_count; zero weights omitted only for Louvain",
        "random_seed": rules["random_seed"], "resolution": settings["resolution"], "threshold": settings["threshold"],
        "component_count": len(components), "cluster_count": len(clusters),
        "isolated_nodes": sorted(nx.isolates(directed)), "components": component_details,
    }
