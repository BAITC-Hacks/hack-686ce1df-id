"""Single-writer revision storage with atomic publication and durable receipts."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from uuid import uuid4

from ..snapshots import PublishedSnapshot
from ..store import ResultStore
from .build import json_bytes, write_run
from .derive import derive
from .generator import generate_source
from .locking import ProcessLock, RepositoryLockedError
from .models import (AcceptedRequest, AddNodeRequest, AddTransferRequest, DemoSource,
                     MutationResponse, SourceGroup, SourceNode, SourceTransfer, source_quality)


class DemoError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str, run_id: str | None = None):
        super().__init__(message)
        self.status_code, self.code, self.message, self.run_id = status_code, code, message, run_id


@dataclass(frozen=True)
class MutationResult:
    response: MutationResponse
    current: PublishedSnapshot


def _sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class DemoRepository:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._thread_lock = threading.RLock()
        self._process_lock = ProcessLock(self.directory / ".writer.lock")
        self._snapshot: PublishedSnapshot | None = None

    @property
    def snapshot(self) -> PublishedSnapshot:
        snapshot = self._snapshot
        if snapshot is None:
            raise RuntimeError("Demo repository is not open")
        return snapshot

    def open(self) -> PublishedSnapshot:
        with self._thread_lock:
            if self._snapshot is not None:
                return self._snapshot
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self._process_lock.acquire()
                pointer = self.directory / "current.json"
                if pointer.exists():
                    current = json.loads(pointer.read_bytes())
                    if not isinstance(current, dict) or set(current) != {"run_id"}:
                        raise ValueError("Invalid current revision pointer")
                    run_id = current["run_id"]
                    if not isinstance(run_id, str) or not re.fullmatch(r"fixture-demo-[0-9a-f]{32}", run_id):
                        raise ValueError("Invalid current revision identifier")
                    revision = self.directory / "revisions" / run_id
                    if not revision.resolve().is_relative_to((self.directory / "revisions").resolve()):
                        raise ValueError("Revision escapes the repository")
                    self._snapshot = self._load_snapshot(revision, run_id)
                else:
                    if any(path.name != ".writer.lock" for path in self.directory.iterdir()):
                        raise ValueError("Каталог не пуст, но указатель current.json отсутствует; автоматическая замена данных запрещена")
                    self._publish(generate_source(), "fixture-demo-" + uuid4().hex)
                return self.snapshot
            except RepositoryLockedError as exc:
                raise DemoError(503, "demo_locked", str(exc)) from exc
            except (OSError, ValueError, TypeError, RuntimeError, LookupError) as exc:
                self._process_lock.release()
                self._snapshot = None
                raise DemoError(503, "demo_write_failed", f"Не удалось открыть сохранённое демо: {exc}") from exc

    def close(self) -> None:
        with self._thread_lock:
            self._snapshot = None
            self._process_lock.release()

    @staticmethod
    def _load_snapshot(directory: Path, run_id: str) -> PublishedSnapshot:
        store = ResultStore.from_directory(directory)
        if store.run_id != run_id:
            raise ValueError("Revision identifier disagrees with manifest")
        source_bytes = store.get_export("source").content
        if hashlib.sha256(source_bytes).hexdigest() != store.manifest.input_hashes.get("source"):
            raise ValueError("Canonical source hash disagrees with manifest")
        source = DemoSource.model_validate_json(source_bytes)
        records = derive(source)
        expected = {"nodes": [n.model_dump(mode="json") for n in records.nodes],
                    "edges": [e.model_dump(mode="json") for e in records.edges],
                    "clusters": [c.model_dump(mode="json") for c in records.clusters],
                    "node_names": [{"gid": n.gid, "display_name": n.display_name} for n in source.nodes],
                    "transfers": [t.model_dump(mode="json") for t in source.transfers]}
        for artifact, value in expected.items():
            if json.loads(store.get_export(artifact).content) != value:
                raise ValueError(f"{artifact} does not agree with the canonical source")
        if store.manifest.counts.get("transfers") != len(source.transfers):
            raise ValueError("Transfer count disagrees with canonical source")
        if source.accepted_requests and source.accepted_requests[-1].response.run_id != run_id:
            raise ValueError("Latest accepted request does not describe this revision")
        return PublishedSnapshot(store=store, source=source)

    def _publish(self, source: DemoSource, run_id: str) -> PublishedSnapshot:
        revisions = self.directory / "revisions"
        revisions.mkdir(exist_ok=True)
        staging = revisions / (".staging-" + uuid4().hex)
        staging.mkdir()
        write_run(source, staging, run_id)
        snapshot = self._load_snapshot(staging, run_id)
        for path in staging.iterdir():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        _sync_directory(staging)
        os.replace(staging, revisions / run_id)
        _sync_directory(revisions)
        pointer = self.directory / (".current-" + uuid4().hex + ".tmp")
        with pointer.open("xb") as handle:
            handle.write(json_bytes({"run_id": run_id}))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pointer, self.directory / "current.json")
        # Commit point: the complete immutable revision is now authoritative.
        # No subsequent failure may turn a successful write into a failed command.
        self._snapshot = snapshot
        try:
            _sync_directory(self.directory)
        except OSError:
            pass
        return snapshot

    def _check_command(self, kind: str, command):
        snapshot = self.snapshot
        canonical = json.dumps(command.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256((kind + "\n" + canonical).encode()).hexdigest()
        assert snapshot.source is not None
        for receipt in snapshot.source.accepted_requests:
            if receipt.request_id == command.request_id:
                if receipt.request_hash != request_hash:
                    raise DemoError(409, "request_id_conflict", "Этот request_id уже использован для другого запроса", snapshot.store.run_id)
                return request_hash, MutationResult(response=receipt.response, current=snapshot)
        if command.run_id != snapshot.store.run_id:
            raise DemoError(409, "stale_run", "Демо изменилось в другой вкладке. Обновите данные и подтвердите сохранение заново.", snapshot.store.run_id)
        return request_hash, None

    def _commit(self, command, request_hash, source, created_id, select_gid):
        previous = self.snapshot
        run_id = "fixture-demo-" + uuid4().hex
        response = MutationResponse(run_id=run_id, created_id=created_id, select_gid=select_gid,
                                    node_count=len(source.nodes), transfer_count=len(source.transfers))
        receipt = AcceptedRequest(request_id=command.request_id, request_hash=request_hash, response=response)
        source = source.model_copy(update={"accepted_requests": source.accepted_requests + (receipt,)})
        try:
            current = self._publish(source, run_id)
        except (OSError, ValueError, TypeError, RuntimeError, LookupError) as exc:
            raise DemoError(503, "demo_write_failed", "Не удалось сохранить демо. Прежняя версия остаётся доступной.", previous.store.run_id) from exc
        return MutationResult(response=response, current=current)

    def add_node(self, request: AddNodeRequest) -> MutationResult:
        with self._thread_lock:
            request_hash, replay = self._check_command("node", request)
            if replay is not None:
                return replay
            snapshot = self.snapshot
            source = snapshot.source
            assert source is not None
            gids = {node.gid for node in source.nodes}
            gid = request.gid
            if gid is None:
                index = 1
                while f"custom-{index:06d}" in gids:
                    index += 1
                gid = f"custom-{index:06d}"
            if gid in gids:
                raise DemoError(409, "duplicate_node", f"Узел с ID {gid} уже существует", snapshot.store.run_id)
            groups = source.groups
            if request.group is not None and request.group.kind == "existing":
                group_id = request.group.id
                if group_id not in {group.id for group in groups}:
                    raise DemoError(404, "not_found", "Выбранная демогруппа не существует", snapshot.store.run_id)
            else:
                group_id = "group-custom-" + uuid4().hex
                name = request.group.name if request.group is not None else ("Группа: " + request.display_name)[:80]
                groups += (SourceGroup(id=group_id, name=name),)
            node = SourceNode(gid=gid, display_name=request.display_name, group_id=group_id, quality=source_quality(request.quality))
            updated = source.model_copy(update={"nodes": source.nodes + (node,), "groups": groups})
            return self._commit(request, request_hash, updated, gid, gid)

    def add_transfer(self, request: AddTransferRequest) -> MutationResult:
        with self._thread_lock:
            request_hash, replay = self._check_command("transfer", request)
            if replay is not None:
                return replay
            snapshot = self.snapshot
            source = snapshot.source
            assert source is not None
            gids = {node.gid for node in source.nodes}
            if request.source not in gids or request.target not in gids:
                raise DemoError(404, "not_found", "Отправитель или получатель не существует; сначала добавьте узел", snapshot.store.run_id)
            transfer = SourceTransfer(id="tx-" + uuid4().hex, **request.model_dump(exclude={"run_id", "request_id"}))
            updated = source.model_copy(update={"transfers": source.transfers + (transfer,)})
            return self._commit(request, request_hash, updated, transfer.id, request.source)
