"""Synthetic Store double, never used by application code or a production run."""

from copy import deepcopy


NODE = {
    "gid": "0007", "component_id": "component-test", "cluster_id": "cluster-test",
    "role": "peripheral", "role_score": 0, "priority_score": 20,
    "assignment_status": "insufficient_evidence",
    "metrics": {
        "in_degree_unique": 0, "out_degree_unique": 2,
        "tx_in_count": 0, "tx_out_count": 2,
        "in_amount_kzt": "0.00", "out_amount_kzt": "100.00", "out_in_ratio": None,
    },
    "quality": {"is_seed": True, "hop_depth": 0, "outbound_censored": False,
                "inbound_incomplete": True, "reasons": ["Синтетический пример; входящие seed неполны."]},
    "role_evidence": [],
    "priority_evidence": [{"rule_id": "fixture-rule", "metric": "out_amount_kzt",
                           "operator": "gte", "actual": "100.00", "threshold": "50.00", "passed": True}],
    "role_explanation": "Недостаточно наблюдаемых данных для более специфической роли",
    "priority_explanation": "Приоритет задан искусственным примером, а не правилами production.",
}

CLUSTER = {"cluster_id": "cluster-test", "component_id": "component-test",
           "gids": ["0007", "receiver-a", "receiver-b"],
           "hypothesis": {"text": "Искусственный кластер для проверки AI.",
                          "basis_rule_ids": ["fixture-rule"], "limitations": ["Это тестовые данные."]}}

EDGES = [{"source": "0007", "target": "receiver-a", "amount_kzt": "30.00", "tx_count": 1},
         {"source": "0007", "target": "receiver-b", "amount_kzt": "70.00", "tx_count": 1}]


class FakeStore:
    run_id = "synthetic-ai-test"

    def __init__(self):
        self.nodes = {"0007": deepcopy(NODE)}
        for gid in ["receiver-a", "receiver-b"]:
            self.nodes[gid] = deepcopy(NODE) | {"gid": gid}
        self.cluster = deepcopy(CLUSTER)
        self.calls = []

    def get_node(self, gid):
        self.calls.append(("get_node", gid))
        return deepcopy(self.nodes[gid])

    def get_cluster(self, cluster_id):
        self.calls.append(("get_cluster", cluster_id))
        if cluster_id != self.cluster["cluster_id"]:
            raise KeyError(cluster_id)
        return deepcopy(self.cluster)

    def get_neighbors(self, gid, radius=1, limit=50):
        self.calls.append(("get_neighbors", gid))
        selected = [gid] + sorted(set(self.nodes) - {gid})
        selected = selected[:limit]
        return {"contract_version": "1.0", "run_id": self.run_id,
                "nodes": [deepcopy(self.nodes[key]) for key in selected],
                "edges": [deepcopy(e) for e in EDGES if e["source"] in selected and e["target"] in selected],
                "total_nodes": len(self.nodes), "shown_nodes": len(selected),
                "truncated": len(selected) < len(self.nodes)}

    def check_concentration(self, gid):
        self.calls.append(("check_concentration", gid))
        # A supplies this calculation. This is a manually verified test response, not a second formula.
        if gid != "0007":
            return {"gid": gid, "total_out_kzt": "0.00", "top_receiver_gid": None,
                    "top_receiver_share": None, "receiver_count": 0}
        return {"gid": gid, "total_out_kzt": "100.00", "top_receiver_gid": "receiver-b",
                "top_receiver_share": 0.7, "receiver_count": 2}


def request(kind="node", id="0007", **extra):
    return {"run_id": FakeStore.run_id, "target": {"kind": kind, "id": id}, **extra}
