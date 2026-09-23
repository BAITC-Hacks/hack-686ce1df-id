"""Deterministic Parquet-to-artifacts calculation with atomic publication."""

from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
from time import perf_counter

from ..contracts import RunManifest
from ..store import ResultStore
from .derive import derive_records, validate_rules
from .exports import build_exports, json_bytes
from .io import InputValidationError, load_inputs
from .temporal import build_temporal_features

PIPELINE_VERSION = "real-data-v1"


def read_config(path: Path) -> tuple[dict, bytes]:
    content = path.read_bytes()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{path}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject(value):
        raise ValueError(f"{path}: non-finite JSON constant {value}")

    value = json.loads(content, object_pairs_hook=unique, parse_constant=reject)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value, content


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _input_hashes(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        raise InputValidationError(f"Input directory does not exist: {directory}")
    return {path.name: _sha(path.read_bytes()) for path in sorted(directory.glob("*.parquet")) if path.is_file()}


def _source_hash() -> str:
    files = sorted(Path(__file__).parent.glob("*.py")) + [Path(__file__).parents[1] / "contracts.py"]
    return _sha(json_bytes({str(path.name): _sha(path.read_bytes()) for path in files}))


def _existing_run(directory: Path, contents: dict[str, bytes], expected: RunManifest) -> RunManifest:
    store = ResultStore.from_directory(directory)
    if store.run_id != expected.run_id:
        raise ValueError(f"Existing output has a different run_id: {directory}")
    manifest = store.manifest
    for field in ("input_hashes", "rules_hash", "counts", "versions", "random_seed", "files", "warnings"):
        if getattr(manifest, field) != getattr(expected, field):
            raise ValueError(f"Existing output {field} differs; refusing to replace {directory}")
    for name, content in contents.items():
        if (directory / name).read_bytes() != content:
            raise ValueError(f"Existing output differs at {name}; refusing to replace {directory}")
    return manifest


def run_pipeline(input_dir: Path, output_dir: Path, rules_path: Path, mapping_path: Path) -> RunManifest:
    """Calculate every stage; return a complete run at output_dir/run_id.

    Repeated calls recalculate and compare immutable contents before reusing an
    existing identical directory. Nothing is published on input/validation error.
    """
    started = perf_counter()
    input_dir, output_dir, rules_path, mapping_path = map(Path, (input_dir, output_dir, rules_path, mapping_path))
    rules, rules_content = read_config(rules_path)
    mapping, mapping_content = read_config(mapping_path)
    validate_rules(rules)
    if mapping.get("reconciliation") != "error":
        raise InputValidationError("Complete runs require reconciliation='error'; use audit for diagnostic mismatches")
    before = _input_hashes(input_dir)
    code_hash = _source_hash()
    nodes, edges, audit = load_inputs(input_dir, mapping, rules["self_transfers"])
    temporal = build_temporal_features(input_dir, mapping, rules["self_transfers"])
    loaded_at = perf_counter()
    records, clusters, diagnostics = derive_records(nodes, edges, rules, temporal)
    derived_at = perf_counter()
    versions = {name: version(name) for name in ("networkx", "pyarrow", "numpy", "pydantic")}
    versions.update(python=platform.python_version(), pipeline=PIPELINE_VERSION, source_sha256=code_hash)
    hashes = {table["file"]: before[table["file"]] for table in mapping["tables"].values()}
    # The schema audit includes every top-level Parquet, even unmapped files.
    # Bind that entire snapshot to identity so adding a file never collides.
    identity = {"input": before, "rules": _sha(rules_content), "mapping": _sha(mapping_content), "versions": versions}
    prefix = "real-" if mapping["dataset_kind"] == "real" else "synthetic-analysis-"
    run_id = prefix + _sha(json_bytes(identity))[:24]
    warnings = [
        "Роли, score и назначения кластеров — гипотезы наблюдаемого графа, не вероятность нарушения.",
        "Исходящий обход, глубина, период и порог суммы ограничивают наблюдение; входящие извне выборки неизвестны.",
        "Граница обхода не является доказанным конечным получателем. У seed входящие неполны.",
        "Для Louvain использована неориентированная проекция с весом по количеству переводов; направления рёбер сохранены.",
        "Шкала приложения 0–100; в nodes_roles.csv и top_nodes.csv оценки переведены в 0–1.",
    ]
    if mapping["dataset_kind"] == "synthetic":
        warnings.insert(0, "Искусственные данные для проверки аналитического расчёта.")
    contents = build_exports(records, edges, clusters, temporal)
    contents["rules.json"] = rules_content
    contents["input_mapping.json"] = mapping_content
    contents["diagnostics.json"] = json_bytes(diagnostics)
    counts = {
        "nodes": len(records), "edges": len(edges), "clusters": len(clusters),
        "transactions": sum(edge["tx_count"] for edge in edges),
        "components": len({node.component_id for node in records}),
        "isolated_nodes": sum(node.metrics.in_degree_unique + node.metrics.out_degree_unique == 0 for node in records),
        "seeds": sum(node.quality.is_seed is True for node in records),
        "boundary_nodes": sum(node.quality.outbound_censored is True for node in records),
    }
    audit.update(pipeline_status="complete", dataset_kind=mapping["dataset_kind"], run_id=run_id,
                 pipeline_counts=counts, input_hashes=hashes, mapping_hash=_sha(mapping_content),
                 source_sha256=code_hash, limitations=warnings,
                 artifact_hashes={name: _sha(content) for name, content in contents.items()})
    contents["audit.json"] = json_bytes(audit)

    if (_input_hashes(input_dir) != before or rules_path.read_bytes() != rules_content
            or mapping_path.read_bytes() != mapping_content or _source_hash() != code_hash):
        raise InputValidationError("Input, configuration, or analytics code changed during calculation; rerun")
    output_dir.mkdir(parents=True, exist_ok=True)
    directory = output_dir / run_id
    staging = Path(tempfile.mkdtemp(prefix=".calculating-", dir=output_dir))
    try:
        for name, content in contents.items():
            with (staging / name).open("xb") as stream:
                stream.write(content)
        file_map = {Path(name).stem: name for name in contents if name != "clusters.csv"}
        file_map.update(clusters_csv="clusters.csv", manifest="manifest.json")
        manifest = RunManifest(
            run_id=run_id, status="complete", input_hashes=hashes, rules_hash=_sha(rules_content), counts=counts,
            elapsed_seconds=perf_counter() - started,
            stage_seconds={"read_and_audit": loaded_at - started, "derive": derived_at - loaded_at,
                           "serialize": perf_counter() - derived_at},
            random_seed=rules["random_seed"], versions=versions, files=file_map, warnings=warnings,
        )
        (staging / "manifest.json").write_bytes(json_bytes(manifest.model_dump(mode="json")))
        ResultStore.from_directory(staging)
        # The measured full wall time (including final manifest write and rename)
        # is returned by CLI; manifest records the time immediately before write.
        manifest = manifest.model_copy(update={"elapsed_seconds": perf_counter() - started,
                                               "stage_seconds": manifest.stage_seconds | {"validate": perf_counter() - started - manifest.elapsed_seconds}})
        (staging / "manifest.json").write_bytes(json_bytes(manifest.model_dump(mode="json")))
        if directory.exists():
            return _existing_run(directory, contents, manifest)
        try:
            os.rename(staging, directory)
        except OSError:
            if directory.exists():
                return _existing_run(directory, contents, manifest)
            raise
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)
