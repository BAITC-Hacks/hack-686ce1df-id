"""Check the task CSV schema and values against one complete local run.

Uses only the standard library. No network or AI calls.
"""

import argparse
import csv
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path


REQUIRED = {
    "nodes_roles.csv": "gid role role_score cluster_id priority_score evidence in_deg out_deg in_kzt out_kzt pagerank pass_through depth is_seed truncated_by_depth".split(),
    "clusters.csv": "cluster_id n_nodes n_seed sum_kzt_internal top_gids hypothesis".split(),
    "top_nodes.csv": "rank gid role priority_score why".split(),
}


def verify(directory: Path) -> dict:
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    manifest = json.loads((directory / "manifest.json").read_text())
    require(manifest["status"] == "complete", "run is incomplete")
    nodes = json.loads((directory / "nodes.json").read_text())
    edges = json.loads((directory / "edges.json").read_text())
    by_gid = {node["gid"]: node for node in nodes}
    require(len(by_gid) == len(nodes) == manifest["counts"]["nodes"], "node count/IDs disagree")
    tables = {}
    for name, required in REQUIRED.items():
        with (directory / name).open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            require(set(required) <= set(reader.fieldnames or []), f"{name}: missing starter columns")
            rows = list(reader)
        require(all(all(row.get(key) not in (None, "") for key in required) for row in rows), f"{name}: empty required values")
        tables[name] = rows
    roles, clusters, top = (tables[name] for name in REQUIRED)
    require(len(roles) == len(nodes) and {r["gid"] for r in roles} == set(by_gid), "roles: missing or duplicate gids")
    expected_order = sorted(nodes, key=lambda n: (-n["priority_score"], n["gid"]))
    require(len(top) >= min(20, len(nodes)) and len(top) <= len(nodes), "top: invalid row count")
    require([r["gid"] for r in top] == [n["gid"] for n in expected_order[:len(top)]], "top: wrong priority order")
    require([int(r["rank"]) for r in top] == list(range(1, len(top) + 1)), "top: invalid ranks")
    assignments = {}
    ranks = {}
    for row in roles:
        node = by_gid[row["gid"]]
        metrics, quality = node["metrics"], node["quality"]
        require(row["role"] == node["role"], "roles: role mismatch")
        for name in ("role_score", "priority_score"):
            value = float(row[name])
            require(math.isfinite(value) and 0 <= value <= 1 and value == node[name] / 100, f"roles: invalid {name}")
        for csv_name, field in (("in_deg", "in_degree_unique"), ("out_deg", "out_degree_unique")):
            require(int(row[csv_name]) == metrics[field], f"roles: wrong {csv_name}")
        for csv_name, field in (("in_kzt", "in_amount_kzt"), ("out_kzt", "out_amount_kzt")):
            require(Decimal(row[csv_name]) == Decimal(metrics[field]), f"roles: wrong {csv_name}")
        ratio = metrics["out_in_ratio"]
        require(row["pass_through"] == "NA" if ratio is None else float(row["pass_through"]) == ratio, "roles: ratio or explicit NA mismatch")
        for csv_name, field in (("depth", "hop_depth"), ("is_seed", "is_seed")):
            expected = quality[field]
            require(row[csv_name] == ("NA" if expected is None else str(expected)), f"roles: wrong {csv_name}")
        censored = quality["outbound_censored"]
        truncated = None if censored is None else censored and metrics["out_degree_unique"] == 0
        require(row["truncated_by_depth"] == ("NA" if truncated is None else str(truncated)), "roles: wrong truncation flag")
        require(len(row["evidence"]) <= 200 and any(c.isdigit() for c in row["evidence"]), "roles: invalid evidence")
        rank = float(row["pagerank"])
        require(math.isfinite(rank) and 0 <= rank <= 1, "roles: invalid PageRank")
        ranks[row["gid"]] = rank
        assignments[row["gid"]] = row["cluster_id"]
    require(len(clusters) == manifest["counts"]["clusters"], "clusters: wrong count")
    require({r["cluster_id"] for r in clusters} == set(assignments.values()), "clusters: IDs disagree")
    for cluster in clusters:
        members = {gid for gid, cid in assignments.items() if cid == cluster["cluster_id"]}
        require(len(members) == int(cluster["n_nodes"]), "clusters: wrong membership count")
        require(sum(by_gid[g]["quality"]["is_seed"] is True for g in members) == int(cluster["n_seed"]), "clusters: wrong seed count")
        amount = sum((Decimal(e["amount_kzt"]) for e in edges if e["source"] in members and e["target"] in members), Decimal(0))
        require(amount == Decimal(cluster["sum_kzt_internal"]), "clusters: wrong internal amount")
        require(set(cluster["top_gids"].split(";")) <= members, "clusters: top IDs outside cluster")
    for row in top:
        node = by_gid[row["gid"]]
        require(row["role"] == node["role"] and float(row["priority_score"]) == node["priority_score"] / 100, "top: node mismatch")
    if nodes:
        require(math.isclose(math.fsum(ranks.values()), 1, abs_tol=1e-10), "PageRank: not normalized")
        outgoing = {gid: Decimal(by_gid[gid]["metrics"]["out_amount_kzt"]) for gid in by_gid}
        dangling = math.fsum(ranks[g] for g in by_gid if outgoing[g] == 0)
        expected = {g: (0.15 + 0.85 * dangling) / len(nodes) for g in by_gid}
        for edge in edges:
            if outgoing[edge["source"]]:
                expected[edge["target"]] += 0.85 * ranks[edge["source"]] * float(Decimal(edge["amount_kzt"]) / outgoing[edge["source"]])
        require(math.fsum(abs(expected[g] - ranks[g]) for g in by_gid) < 1e-8, "PageRank: directed amount-weighted stationary equation failed")
    return {"status": "passed", "run_id": manifest["run_id"], "counts": manifest["counts"],
            "rows": {name: len(rows) for name, rows in tables.items()},
            "required_columns": REQUIRED, "undefined_ratios": sum(r["pass_through"] == "NA" for r in roles),
            "sha256": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in REQUIRED}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.run_dir), ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError, TypeError) as error:
        parser.exit(1, f"Export verification failed: {error}\n")


if __name__ == "__main__":
    main()
