from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.demo import repository as module
from backend.app.demo.models import AddNodeRequest, AddTransferRequest
from backend.app.demo.repository import DemoError, DemoRepository


@pytest.fixture
def repo(tmp_path):
    repository = DemoRepository(tmp_path / "demo")
    repository.open()
    try:
        yield repository
    finally:
        repository.close()


def node_command(repo, **changes):
    return AddNodeRequest.model_validate(dict(run_id=repo.snapshot.store.run_id, request_id=str(uuid4()),
                                              display_name="Айдана Садыкова") | changes)


def transfer_command(repo, **changes):
    return AddTransferRequest.model_validate(dict(run_id=repo.snapshot.store.run_id, request_id=str(uuid4()),
                                                   source="0007", target="7", amount_kzt="5000.01", date="2026-07-12") | changes)


def test_replay_after_restart_and_later_write_does_not_duplicate_or_rewind(repo):
    command = transfer_command(repo)
    accepted = repo.add_transfer(command).response
    newest = repo.add_node(node_command(repo)).response
    directory = repo.directory
    repo.close()
    reopened = DemoRepository(directory)
    try:
        reopened.open()
        result = reopened.add_transfer(command)
        assert result.response == accepted
        assert result.current.store.run_id == newest.run_id
        assert reopened.snapshot.store.run_id == newest.run_id
        assert len(reopened.snapshot.source.transfers) == 6001
        assert len(reopened.snapshot.source.nodes) == 501
    finally:
        reopened.close()


def test_namesakes_leading_zeros_auto_id_and_frozen_metadata(repo):
    old = repo.snapshot
    result = repo.add_node(node_command(repo, gid="0000007", display_name="Александр Иванов"))
    node = next(n for n in result.current.source.nodes if n.gid == "0000007")
    assert node.display_name == "Александр Иванов"
    assert result.current.store.get_node(node.gid).role_score == 0
    assert len(old.source.nodes) == 500
    assert old.store.run_id != result.current.store.run_id
    assert isinstance(result.current.source.nodes, tuple)
    assert isinstance(node.quality.reasons, tuple)
    with pytest.raises(ValidationError):
        node.quality.outbound_censored = False
    generated = repo.add_node(node_command(repo)).response
    assert generated.created_id == generated.select_gid == "custom-000001"


@pytest.mark.parametrize("case,status,code", [("duplicate", 409, "duplicate_node"), ("unknown_group", 404, "not_found"),
                                             ("unknown_node", 404, "not_found"), ("stale", 409, "stale_run")])
def test_rejected_commands_do_not_change_source(repo, case, status, code):
    original = repo.snapshot
    with pytest.raises(DemoError) as caught:
        if case == "duplicate":
            repo.add_node(node_command(repo, gid="0007"))
        elif case == "unknown_group":
            repo.add_node(node_command(repo, group={"kind": "existing", "id": "missing"}))
        elif case == "unknown_node":
            repo.add_transfer(transfer_command(repo, target="missing"))
        else:
            repo.add_node(node_command(repo, run_id="fixture-demo-old"))
    assert (caught.value.status_code, caught.value.code) == (status, code)
    assert caught.value.run_id == original.store.run_id
    assert repo.snapshot is original


def test_request_id_conflict_is_checked_before_stale_run(repo):
    command = transfer_command(repo)
    repo.add_transfer(command)
    current = repo.snapshot
    with pytest.raises(DemoError) as caught:
        repo.add_transfer(command.model_copy(update={"amount_kzt": "6000.00"}))
    assert caught.value.code == "request_id_conflict"
    assert repo.snapshot is current


def test_parallel_commands_on_one_run_only_accept_one(repo):
    commands = [node_command(repo), node_command(repo)]

    def submit(command):
        try:
            return repo.add_node(command).response
        except DemoError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, commands))
    assert sum(isinstance(result, DemoError) and result.code == "stale_run" for result in results) == 1
    assert len(repo.snapshot.source.nodes) == 501


def test_pointer_failure_preserves_last_good_revision_and_retry(repo, monkeypatch):
    old = repo.snapshot
    command = node_command(repo)
    real_replace = os.replace

    def fail_pointer(source, target):
        if Path(target).name == "current.json":
            raise OSError("disk full")
        real_replace(source, target)

    monkeypatch.setattr(module.os, "replace", fail_pointer)
    with pytest.raises(DemoError) as caught:
        repo.add_node(command)
    assert caught.value.code == "demo_write_failed"
    assert repo.snapshot is old
    monkeypatch.setattr(module.os, "replace", real_replace)
    directory = repo.directory
    repo.close()
    reopened = DemoRepository(directory)
    try:
        assert reopened.open().store.run_id == old.store.run_id
        assert reopened.add_node(command).response.node_count == 501
    finally:
        reopened.close()


def test_after_commit_fsync_failure_does_not_report_failed_command(repo, monkeypatch):
    real_sync = module._sync_directory

    def fail_after_commit(path):
        if path == repo.directory:
            raise OSError("directory fsync unavailable")
        real_sync(path)

    monkeypatch.setattr(module, "_sync_directory", fail_after_commit)
    response = repo.add_node(node_command(repo)).response
    assert repo.snapshot.store.run_id == response.run_id
    assert json.loads((repo.directory / "current.json").read_bytes())["run_id"] == response.run_id


@pytest.mark.parametrize("filename", ["current.json", "source.json", "nodes.json", "node_names.json"])
def test_corruption_fails_closed_without_reseeding(repo, filename):
    directory = repo.directory
    run = repo.snapshot.store.run_id
    path = directory / "current.json" if filename == "current.json" else directory / "revisions" / run / filename
    repo.close()
    path.write_text("broken data", encoding="utf-8")
    reopened = DemoRepository(directory)
    with pytest.raises(DemoError):
        reopened.open()
    assert path.read_text(encoding="utf-8") == "broken data"
    assert len(list((directory / "revisions").iterdir())) == 1


def test_nonempty_directory_without_pointer_is_never_initialized(tmp_path):
    (tmp_path / "important.txt").write_text("keep")
    with pytest.raises(DemoError):
        DemoRepository(tmp_path).open()
    assert (tmp_path / "important.txt").read_text() == "keep"
    assert not (tmp_path / "current.json").exists()


def test_cross_component_transfer_merges_components_but_group_remains_separate(repo):
    result = repo.add_node(node_command(repo, group={"kind": "existing", "id": repo.snapshot.source.groups[0].id}))
    new_gid = result.response.created_id
    isolated = result.current.store.get_node(new_gid)
    other = result.current.store.get_node("100001")
    assert isolated.component_id != other.component_id
    assert isolated.cluster_id != other.cluster_id
    result = repo.add_transfer(transfer_command(repo, source="100001", target=new_gid))
    assert result.current.store.get_node(new_gid).component_id == result.current.store.get_node("100001").component_id
    assert result.current.store.get_node(new_gid).cluster_id == result.current.store.get_node("100001").cluster_id


def test_process_lock_and_release_after_process_exit(tmp_path):
    script = "from pathlib import Path; import sys; from backend.app.demo.repository import DemoRepository; r=DemoRepository(Path(sys.argv[1])); r.open(); print('ready', flush=True); sys.stdin.readline()"
    process = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        contender = DemoRepository(tmp_path)
        with pytest.raises(DemoError) as caught:
            contender.open()
        assert caught.value.code == "demo_locked"
        process.terminate()
        process.wait(timeout=10)
        assert contender.open().source is not None
        contender.close()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
