"""Schema-first inspection and input audit; never publishes a complete run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from time import perf_counter

from .features import build_features
from .io import InputValidationError, inspect_schemas, load_inputs


def _read_json(path: Path) -> dict:
    def reject_constant(value: str) -> None:
        raise ValueError(f"Недопустимое JSON-значение: {value}")

    def unique_keys(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{path}: повторяющийся JSON-ключ {key!r}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant,
                       object_pairs_hook=unique_keys)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: ожидается JSON-объект")
    return value


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_report(path: Path, value: dict) -> None:
    """Publish JSON atomically without replacing an existing user file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
            stream.write("\n")
        # Both files are on the same filesystem. link fails if destination exists.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def audit_inputs(input_dir: Path, mapping_path: Path, self_transfers: str) -> dict:
    """Return an explicitly preliminary audit with full observed metrics."""
    started = perf_counter()
    mapping_hash_before = _hash_file(mapping_path)
    mapping = _read_json(mapping_path)
    # Snapshot the exact files before reading rows, then verify they did not
    # change while auditing. Do not dereference paths supplied by an unreviewed
    # mapping here; the directory snapshot only visits top-level parquet files.
    hashes_before = {path.name: _hash_file(path) for path in sorted(input_dir.glob("*.parquet"))
                     if path.is_file()}
    nodes, edges, audit = load_inputs(input_dir, mapping, self_transfers)
    metrics = build_features(nodes, edges)
    hashes = {}
    for table in mapping["tables"].values():
        filename = table["file"]
        hashes[filename] = _hash_file(input_dir / filename)
        if hashes[filename] != hashes_before.get(filename):
            raise InputValidationError(f"{filename}: файл изменился во время аудита; повторите запуск")
    if mapping_hash_before != _hash_file(mapping_path):
        raise InputValidationError("Сопоставление полей изменилось во время аудита; повторите запуск")
    return {
        "status": "audit_only",
        "dataset_kind": mapping["dataset_kind"],
        "input_hashes": hashes,
        "mapping_hash": mapping_hash_before,
        "self_transfers": self_transfers,
        "audit": audit,
        "observed_nodes": [{**node, "metrics": metrics[node["gid"]]} for node in nodes],
        "observed_edges": edges,
        "elapsed_seconds_before_report": perf_counter() - started,
        "limitations": [
            "Предварительный аудит: роли, кластеры и приоритеты не назначены.",
            "Этот файл не является завершённым набором результатов контракта v1.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Аналитика A: проверка входа и наблюдаемых метрик")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Прочитать только схемы parquet, без строк")
    inspect.add_argument("--input-dir", required=True, type=Path)
    inspect.add_argument("--output", type=Path, help="Новый файл отчёта; по умолчанию stdout")
    audit = commands.add_parser("audit", help="Сверить вход и рассчитать метрики без классификации")
    audit.add_argument("--input-dir", required=True, type=Path)
    audit.add_argument("--mapping", required=True, type=Path)
    audit.add_argument("--self-transfers", choices=("include", "exclude"), required=True)
    audit.add_argument("--output", type=Path, help="Новый файл отчёта; по умолчанию stdout")
    args = parser.parse_args(argv)
    started = perf_counter()
    try:
        if args.command == "inspect":
            result = {"status": "schema_only", **inspect_schemas(args.input_dir)}
        else:
            result = audit_inputs(args.input_dir, args.mapping, args.self_transfers)
        if args.output:
            _write_report(args.output, result)
            print(json.dumps({
                "status": result["status"], "report": str(args.output.resolve()),
                "elapsed_seconds": perf_counter() - started,
            }, ensure_ascii=False, allow_nan=False))
        else:
            print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (InputValidationError, ValueError, OSError) as error:
        failure = {"status": "failed", "error": str(error)}
        if isinstance(error, InputValidationError) and error.audit:
            failure["audit"] = error.audit
        if args.output and not args.output.exists():
            try:
                _write_report(args.output, failure)
            except OSError as output_error:
                failure["report_error"] = str(output_error)
        print(json.dumps(failure, ensure_ascii=False, allow_nan=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
