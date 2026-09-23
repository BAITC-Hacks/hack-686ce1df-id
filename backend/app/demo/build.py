"""Write one complete, independently loadable synthetic run."""

import csv
import hashlib
import io
import json
from pathlib import Path
from time import perf_counter

from ..contracts import RunManifest
from .derive import RULES_PATH, derive
from .generator import GENERATOR_VERSION
from .models import DemoSource


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _csv_bytes(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def write_run(source: DemoSource, directory: Path, run_id: str) -> None:
    started = perf_counter()
    directory.mkdir(parents=True, exist_ok=True)
    records = derive(source)
    derived_at = perf_counter()
    source_content = json_bytes(source.model_dump(mode="json"))
    rules_content = RULES_PATH.read_bytes()
    contents = {
        "source.json": source_content,
        "nodes.json": json_bytes([n.model_dump(mode="json") for n in records.nodes]),
        "edges.json": json_bytes([e.model_dump(mode="json") for e in records.edges]),
        "clusters.json": json_bytes([c.model_dump(mode="json") for c in records.clusters]),
        "node_names.json": json_bytes([{"gid": n.gid, "display_name": n.display_name} for n in source.nodes]),
        "transfers.json": json_bytes([t.model_dump(mode="json") for t in source.transfers]),
        "rules.json": rules_content,
        "audit.json": json_bytes({"status": "synthetic_consistent", "node_count": len(source.nodes), "transfer_count": len(source.transfers),
                                  "self_transfers": "rejected", "money": "KZT; exact integer tiyn aggregation", "synthetic": True}),
    }
    priority_columns = ("rank", "gid", "role", "role_score", "priority_score", "priority_explanation")
    contents["priorities.csv"] = _csv_bytes(priority_columns, (
        {"rank": rank} | {key: getattr(node, key) for key in priority_columns[1:]}
        for rank, node in enumerate(sorted(records.nodes, key=lambda n: (-n.priority_score, n.gid)), start=1)))
    role_columns = ("gid", "role", "role_score", "assignment_status", "role_explanation")
    contents["roles.csv"] = _csv_bytes(role_columns, ({key: getattr(node, key) for key in role_columns} for node in records.nodes))
    for name, content in contents.items():
        (directory / name).write_bytes(content)
    elapsed = perf_counter() - started
    manifest = RunManifest(run_id=run_id, status="complete", input_hashes={"source": hashlib.sha256(source_content).hexdigest()},
                           rules_hash=hashlib.sha256(rules_content).hexdigest(),
                           counts={"nodes": len(records.nodes), "edges": len(records.edges), "clusters": len(records.clusters), "transfers": len(source.transfers)},
                           elapsed_seconds=elapsed, stage_seconds={"derive": derived_at - started, "serialize": elapsed - (derived_at - started)},
                           random_seed=source.seed, versions={"demo_generator": GENERATOR_VERSION, "demo_rules": json.loads(rules_content)["version"]},
                           files={Path(name).stem: name for name in contents} | {"manifest": "manifest.json"},
                           warnings=["Все люди и переводы вымышлены. Данные предназначены только для демонстрации.",
                                     "Роли и приоритет рассчитаны демонстрационными правилами; production-правила не утверждены.",
                                     "Кластеры — заданные демогруппы внутри компонент, не результат Louvain и не доказательство общей организации."])
    (directory / "manifest.json").write_bytes(json_bytes(manifest.model_dump(mode="json")))
