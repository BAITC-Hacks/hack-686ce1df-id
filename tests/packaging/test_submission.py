"""Submission packaging boundary checks; no credentials or external calls."""

import json
from pathlib import Path
import zipfile

import pytest

from scripts.package_submission import artifact_snapshot, publish_zip, reject_secrets, safe_file, selected_source


def test_artifact_paths_and_symlinks_cannot_escape_run(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    for unsafe in ("../outside", "/outside", "nested/../../outside", "nested\\outside", "C:/outside",
                   ".env", "nodes.parquet", "unexpected.json"):
        (run / "manifest.json").write_text(json.dumps({
            "status": "complete", "run_id": "real-test", "files": {"bad": unsafe},
        }))
        with pytest.raises(ValueError):
            artifact_snapshot(run)
    outside = tmp_path / "outside"
    outside.write_text("not a package input")
    (run / "link").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks"):
        safe_file(run, "link")
    (run / "directory").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinks"):
        safe_file(run, "directory/outside")
    assert selected_source("backend/app/main.py")
    assert selected_source(".env.example")
    assert not selected_source("frontend/.env.local")
    assert not selected_source("frontend/node_modules/vendor/index.js")
    assert not selected_source("frontend/dist/index.html")
    assert not selected_source("data/raw/nodes.parquet")


def test_secret_rejection_does_not_echo_value():
    values = [b"sk-" + b"a" * 30, b"-----BEGIN " + b"RSA PRIVATE KEY-----"]
    for value in values:
        with pytest.raises(ValueError) as caught:
            reject_secrets(value)
        assert value.decode() not in str(caught.value)
    reject_secrets(b"OPENAI_API_KEY=\nAI_MODEL=\n")


def test_zip_payload_is_repeatable_and_cannot_clobber_existing_archive(tmp_path):
    path = tmp_path / "package.zip"
    contents = {"README.md": b"Local handoff", "source/scripts/check.py": b"print('ok')\n"}
    publish_zip(path, contents, {"source/scripts/check.py": 0o755})
    original = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == set(contents)
        assert all(archive.read(name) == content for name, content in contents.items())
        assert archive.getinfo("source/scripts/check.py").external_attr >> 16 & 0o777 == 0o755
        assert archive.testzip() is None
    publish_zip(path, contents, {"source/scripts/check.py": 0o755})
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="already exists"):
        publish_zip(path, {"README.md": b"different"}, {})
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".packaging-*"))
