"""Validated, in-memory snapshot of one complete analytics run.

The store never reads files again after loading. Public records are copies so
callers, including optional analytics integrations, cannot mutate the snapshot.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import io
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from .contracts import ClusterRecord, EdgeRecord, GraphResponse, NodeRecord, RunManifest


class StoreLoadError(ValueError):
    """The directory does not contain a complete, consistent result set."""


class RecordNotFoundError(LookupError):
    """A requested identifier is not present in the current run."""

    status_code = 404


class AnalyticsUnavailableError(RuntimeError):
    """The optional analytics implementation is not installed."""


@dataclass(frozen=True)
class ExportArtifact:
    filename: str
    content: bytes
    media_type: str


_Record = TypeVar("_Record", bound=BaseModel)


class _RecordMapping(Mapping[str, _Record], Generic[_Record]):
    """Read-only indexed view, returning detached records on lookup."""

    def __init__(self, records: dict[str, _Record]) -> None:
        self.__records = records

    def __getitem__(self, key: str) -> _Record:
        return self.__records[key].model_copy(deep=True)

    def __iter__(self) -> Iterator[str]:
        return iter(self.__records)

    def __len__(self) -> int:
        return len(self.__records)


_REQUIRED_FILES = {
    "nodes": "nodes.json",
    "edges": "edges.json",
    "clusters": "clusters.json",
    "priorities": "priorities.csv",
    "roles": "roles.csv",
    "rules": "rules.json",
    "audit": "audit.json",
}


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise StoreLoadError(f"Duplicate JSON key: {key!r}")
        value[key] = item
    return value


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise StoreLoadError("JSON numbers must be finite")
    return parsed


def _reject_constant(value: str) -> Any:
    raise StoreLoadError(f"Non-standard JSON number: {value}")


def _read_json(content: bytes, filename: str) -> Any:
    try:
        return json.loads(
            content.decode("utf-8-sig"),
            object_pairs_hook=_object_without_duplicates,
            parse_float=_finite_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError) as exc:
        raise StoreLoadError(f"Invalid {filename}: {exc}") from exc


def _records(content: bytes, filename: str, model: type[_Record]) -> list[_Record]:
    data = _read_json(content, filename)
    if not isinstance(data, list):
        raise StoreLoadError(f"{filename} must contain a JSON array")
    try:
        return [model.model_validate(item) for item in data]
    except ValidationError as exc:
        raise StoreLoadError(f"Invalid records in {filename}: {exc}") from exc


def _safe_artifact_path(directory: Path, relative: str) -> Path:
    # Check both path dialects, even when the server runs on Linux.
    posix = PurePosixPath(relative)
    windows = PureWindowsPath(relative)
    if (
        not relative
        or "\\" in relative
        or "\x00" in relative
        or ":" in relative
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise StoreLoadError(f"Unsafe artifact path: {relative!r}")
    candidate = (directory / relative).resolve(strict=True)
    if not candidate.is_relative_to(directory) or not candidate.is_file():
        raise StoreLoadError(f"Artifact is not a file inside the run: {relative!r}")
    return candidate


def _csv_rows(content: bytes, filename: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        headers = reader.fieldnames
        if headers is None or len(headers) != len(columns) or set(headers) != set(columns):
            raise StoreLoadError(f"{filename} must have columns {','.join(columns)}")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise StoreLoadError(f"{filename} contains a row with the wrong number of fields")
        return rows
    except (UnicodeError, csv.Error) as exc:
        raise StoreLoadError(f"Invalid {filename}: {exc}") from exc


def _same_score(actual: str, expected: float) -> bool:
    try:
        value = Decimal(actual)
        return value.is_finite() and value == Decimal(str(expected))
    except InvalidOperation:
        return False


class ResultStore:
    """One validated run, with identity and graph indexes built at startup."""

    def __init__(
        self,
        manifest: RunManifest,
        nodes: list[NodeRecord],
        edges: list[EdgeRecord],
        clusters: list[ClusterRecord],
        exports: dict[str, ExportArtifact],
    ) -> None:
        self._manifest = manifest.model_copy(deep=True)
        self._nodes = {node.gid: node.model_copy(deep=True) for node in nodes}
        self._clusters = {cluster.cluster_id: cluster.model_copy(deep=True) for cluster in clusters}
        self._edges = tuple(edge.model_copy(deep=True) for edge in edges)
        self._exports = dict(exports)
        self._node_view = _RecordMapping(self._nodes)
        self._priority_gids = sorted(self._nodes, key=self._priority_key)
        self._cluster_ids = sorted(self._clusters)
        self._components: dict[str, set[str]] = defaultdict(set)
        self._adjacency: dict[str, set[str]] = {gid: set() for gid in self._nodes}
        self._incident_edges: dict[str, set[int]] = {gid: set() for gid in self._nodes}
        for node in self._nodes.values():
            self._components[node.component_id].add(node.gid)
        for index, edge in enumerate(self._edges):
            self._adjacency[edge.source].add(edge.target)
            self._adjacency[edge.target].add(edge.source)
            self._incident_edges[edge.source].add(index)
            self._incident_edges[edge.target].add(index)

    @property
    def run_id(self) -> str:
        return self._manifest.run_id

    @property
    def manifest(self) -> RunManifest:
        return self._manifest.model_copy(deep=True)

    @property
    def nodes_by_gid(self) -> Mapping[str, NodeRecord]:
        return self._node_view

    @classmethod
    def from_directory(cls, directory: Path) -> ResultStore:
        """Load atomically, rejecting missing files or inconsistent results."""
        try:
            return cls._load(directory)
        except StoreLoadError:
            raise
        except (OSError, UnicodeError, ValidationError, ValueError, TypeError, RuntimeError) as exc:
            raise StoreLoadError(f"Cannot load result directory {directory}: {exc}") from exc

    @classmethod
    def _load(cls, directory: Path) -> ResultStore:
        directory = Path(directory).resolve(strict=True)
        if not directory.is_dir():
            raise StoreLoadError("Result path must be a directory")
        manifest_path = _safe_artifact_path(directory, "manifest.json")
        manifest_bytes = manifest_path.read_bytes()
        manifest_data = _read_json(manifest_bytes, "manifest.json")
        if not isinstance(manifest_data, dict) or "contract_version" not in manifest_data:
            raise StoreLoadError("manifest.json must explicitly declare contract_version")
        manifest = RunManifest.model_validate(manifest_data)
        if manifest.status != "complete" or manifest.contract_version != "1.0":
            raise StoreLoadError("A complete contract_version 1.0 run is required")
        if any(re.fullmatch(r"[0-9a-fA-F]{64}", value) is None for value in manifest.input_hashes.values()):
            raise StoreLoadError("manifest.input_hashes values must be SHA-256 hex digests")

        exports: dict[str, ExportArtifact] = {}
        contents: dict[str, bytes] = {}
        fingerprints: dict[Path, tuple[int, int, int]] = {}
        for name, relative in manifest.files.items():
            if not name or name in {".", ".."} or any(char in name for char in "/\\:\x00"):
                raise StoreLoadError(f"Unsafe export name: {name!r}")
            path = _safe_artifact_path(directory, relative)
            if name == "manifest" and path != manifest_path:
                raise StoreLoadError("The reserved export name 'manifest' must refer to manifest.json")
            before = path.stat()
            content = path.read_bytes()
            fingerprints[path] = (before.st_size, before.st_mtime_ns, before.st_ino)
            contents[name] = content
            suffix = path.suffix.lower()
            media_type = {".json": "application/json", ".csv": "text/csv"}.get(
                suffix, "application/octet-stream"
            )
            exports[name] = ExportArtifact(Path(relative).name, content, media_type)

        required: dict[str, bytes] = {}
        for logical, filename in _REQUIRED_FILES.items():
            candidates = [name for name in (logical, filename) if name in contents]
            if not candidates:
                raise StoreLoadError(f"manifest.files is missing required artifact {logical!r}")
            if len(candidates) > 1 and manifest.files[candidates[0]] != manifest.files[candidates[1]]:
                raise StoreLoadError(f"Ambiguous manifest.files aliases for {logical!r}")
            required[logical] = contents[candidates[0]]
            # Stable UI export names also work with filename-keyed manifests.
            exports[logical] = exports[candidates[0]]

        # Export the exact validated startup file, including its original formatting.
        exports["manifest"] = ExportArtifact("manifest.json", manifest_bytes, "application/json")

        nodes = _records(required["nodes"], "nodes.json", NodeRecord)
        edges = _records(required["edges"], "edges.json", EdgeRecord)
        clusters = _records(required["clusters"], "clusters.json", ClusterRecord)
        for logical in ("rules", "audit"):
            if not isinstance(_read_json(required[logical], f"{logical}.json"), dict):
                raise StoreLoadError(f"{logical}.json must contain a JSON object")
        if hashlib.sha256(required["rules"]).hexdigest() != manifest.rules_hash.lower():
            raise StoreLoadError("rules.json does not match manifest.rules_hash")

        cls._validate_relations(nodes, edges, clusters)
        for name, records in (("nodes", nodes), ("edges", edges), ("clusters", clusters)):
            if name in manifest.counts and manifest.counts[name] != len(records):
                raise StoreLoadError(f"manifest.counts.{name} does not match {name}.json")
        cls._validate_csv(nodes, required["priorities"], required["roles"])

        # Do not adopt a run that was still being rewritten during its load.
        if manifest_path.read_bytes() != manifest_bytes:
            raise StoreLoadError("manifest.json changed while the run was loading")
        for path, fingerprint in fingerprints.items():
            after = path.stat()
            if (after.st_size, after.st_mtime_ns, after.st_ino) != fingerprint:
                raise StoreLoadError(f"Artifact changed while the run was loading: {path.name}")
        return cls(manifest, nodes, edges, clusters, exports)

    @staticmethod
    def _validate_relations(
        nodes: list[NodeRecord], edges: list[EdgeRecord], clusters: list[ClusterRecord]
    ) -> None:
        node_map = {node.gid: node for node in nodes}
        cluster_map = {cluster.cluster_id: cluster for cluster in clusters}
        if len(node_map) != len(nodes):
            raise StoreLoadError("nodes.json contains duplicate gid values")
        if len(cluster_map) != len(clusters):
            raise StoreLoadError("clusters.json contains duplicate cluster_id values")
        directed_pairs: set[tuple[str, str]] = set()
        for edge in edges:
            if edge.source not in node_map or edge.target not in node_map:
                raise StoreLoadError(f"Edge references an unknown node: {edge.source} -> {edge.target}")
            pair = (edge.source, edge.target)
            if pair in directed_pairs:
                raise StoreLoadError(f"Duplicate directed edge: {edge.source} -> {edge.target}")
            directed_pairs.add(pair)
            if node_map[edge.source].component_id != node_map[edge.target].component_id:
                raise StoreLoadError("Edge endpoints belong to different components")
        memberships: set[str] = set()
        for cluster in clusters:
            for gid in cluster.gids:
                if gid not in node_map:
                    raise StoreLoadError(f"Cluster {cluster.cluster_id} references unknown gid {gid}")
                if gid in memberships:
                    raise StoreLoadError(f"Duplicate cluster membership for gid {gid}")
                memberships.add(gid)
                node = node_map[gid]
                if node.cluster_id != cluster.cluster_id or node.component_id != cluster.component_id:
                    raise StoreLoadError(f"Cluster membership disagrees with node {gid}")
        if memberships != set(node_map):
            raise StoreLoadError("Every node must occur exactly once in clusters.json")

    @staticmethod
    def _validate_csv(nodes: list[NodeRecord], priorities: bytes, roles: bytes) -> None:
        node_map = {node.gid: node for node in nodes}
        priority_rows = _csv_rows(
            priorities,
            "priorities.csv",
            ("rank", "gid", "role", "role_score", "priority_score", "priority_explanation"),
        )
        expected = sorted(nodes, key=lambda node: (-node.priority_score, node.gid))
        if len(priority_rows) != len(expected):
            raise StoreLoadError("priorities.csv must contain every node exactly once")
        for rank, (row, node) in enumerate(zip(priority_rows, expected), start=1):
            if (
                row["rank"] != str(rank)
                or row["gid"] != node.gid
                or row["role"] != node.role
                or not _same_score(row["role_score"], node.role_score)
                or not _same_score(row["priority_score"], node.priority_score)
                or row["priority_explanation"] != node.priority_explanation
            ):
                raise StoreLoadError(f"priorities.csv disagrees with nodes.json at rank {rank}")
        role_rows = _csv_rows(
            roles,
            "roles.csv",
            ("gid", "role", "role_score", "assignment_status", "role_explanation"),
        )
        if len(role_rows) != len(nodes):
            raise StoreLoadError("roles.csv must contain every node exactly once")
        seen: set[str] = set()
        for row in role_rows:
            gid = row["gid"]
            node = node_map.get(gid)
            if node is None or gid in seen:
                raise StoreLoadError(f"roles.csv contains an unknown or duplicate gid: {gid}")
            seen.add(gid)
            if (
                row["role"] != node.role
                or not _same_score(row["role_score"], node.role_score)
                or row["assignment_status"] != node.assignment_status
                or row["role_explanation"] != node.role_explanation
            ):
                raise StoreLoadError(f"roles.csv disagrees with nodes.json for gid {gid}")

    def _priority_key(self, gid: str) -> tuple[float, str]:
        return (-self._nodes[gid].priority_score, gid)

    def get_node(self, gid: str) -> NodeRecord:
        if gid not in self._nodes:
            raise RecordNotFoundError(f"Unknown gid: {gid}")
        return self._nodes[gid].model_copy(deep=True)

    def get_cluster(self, cluster_id: str) -> ClusterRecord:
        if cluster_id not in self._clusters:
            raise RecordNotFoundError(f"Unknown cluster_id: {cluster_id}")
        return self._clusters[cluster_id].model_copy(deep=True)

    def list_priorities(self, limit: int = 20, offset: int = 0) -> list[NodeRecord]:
        if type(limit) is not int or limit < 1 or type(offset) is not int or offset < 0:
            raise ValueError("limit must be positive and offset must be non-negative integers")
        return [self.get_node(gid) for gid in self._priority_gids[offset : offset + limit]]

    def list_clusters(self) -> list[ClusterRecord]:
        return [self.get_cluster(cluster_id) for cluster_id in self._cluster_ids]

    def get_neighbors(self, gid: str, radius: int = 1, limit: int = 50) -> GraphResponse:
        return self.get_graph(gid=gid, radius=radius, limit=limit)

    def get_graph(
        self,
        *,
        gid: str | None = None,
        cluster_id: str | None = None,
        component_id: str | None = None,
        radius: int = 1,
        limit: int = 300,
    ) -> GraphResponse:
        if sum(value is not None for value in (gid, cluster_id, component_id)) != 1:
            raise ValueError("Provide exactly one of gid, cluster_id, component_id")
        if type(radius) is not int or radius not in (1, 2):
            raise ValueError("radius must be 1 or 2")
        if type(limit) is not int or not 1 <= limit <= 300:
            raise ValueError("limit must be between 1 and 300")
        if gid is not None:
            self.get_node(gid)
            distances = {gid: 0}
            frontier = {gid}
            for depth in range(1, radius + 1):
                following = {
                    neighbor
                    for current in frontier
                    for neighbor in self._adjacency[current]
                    if neighbor not in distances
                }
                distances.update((neighbor, depth) for neighbor in following)
                frontier = following
                if not frontier:
                    break
            ordered = sorted(distances, key=lambda node_id: (distances[node_id], *self._priority_key(node_id)))
        elif cluster_id is not None:
            cluster = self.get_cluster(cluster_id)
            ordered = sorted(cluster.gids, key=self._priority_key)
        else:
            if component_id not in self._components:
                raise RecordNotFoundError(f"Unknown component_id: {component_id}")
            ordered = sorted(self._components[component_id], key=self._priority_key)
        shown = ordered[:limit]
        shown_set = set(shown)
        edge_indices = {index for node_id in shown for index in self._incident_edges[node_id]}
        edges = sorted(
            (
                self._edges[index]
                for index in edge_indices
                if self._edges[index].source in shown_set and self._edges[index].target in shown_set
            ),
            key=lambda edge: (edge.source, edge.target),
        )
        return GraphResponse(
            contract_version="1.0",
            run_id=self.run_id,
            nodes=[self.get_node(node_id) for node_id in shown],
            edges=[edge.model_copy(deep=True) for edge in edges],
            truncated=len(shown) < len(ordered),
            total_nodes=len(ordered),
            shown_nodes=len(shown),
        )

    def get_export(self, name: str) -> ExportArtifact:
        """Return an allowlisted artifact, canonical alias, or the startup manifest."""
        if name not in self._exports:
            raise RecordNotFoundError(f"Unknown export: {name}")
        return self._exports[name]

    def check_concentration(self, gid: str) -> dict[str, Any]:
        self.get_node(gid)
        try:
            module = importlib.import_module("backend.app.analytics.checks")
            check = getattr(module, "check_concentration")
        except (ImportError, AttributeError) as exc:
            raise AnalyticsUnavailableError("The analytics concentration check is not available") from exc
        if not callable(check):
            raise AnalyticsUnavailableError("The analytics concentration check is not callable")
        return check(gid, [edge.model_copy(deep=True) for edge in self._edges])
