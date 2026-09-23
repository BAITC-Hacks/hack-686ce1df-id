"""Create a local, verified submission ZIP from a clean committed source tree."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile

if __package__:
    from .verify_task_exports import verify
else:
    from verify_task_exports import verify


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {"backend", "config", "contracts", "docker", "frontend", "scripts", "tests", "docs"}
ROOT_FILES = {"README.md", "Dockerfile", "requirements.lock", "pyproject.toml",
              ".gitignore", ".dockerignore", ".env.example"}
EXCLUDED_DIRS = {"node_modules", "dist", "build", "__pycache__", ".pytest_cache", ".ruff_cache",
                 "coverage", "htmlcov", "data", "artifacts", "submission", ".git"}
SECRET_PATTERNS = (re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{25,}"),
                   re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"))
RESULT_FILES = ("nodes_roles.csv", "clusters.csv", "top_nodes.csv")
ARTIFACT_FILES = {"manifest.json", "audit.json", "nodes.json", "edges.json", "clusters.json",
                  "diagnostics.json", "input_mapping.json", "priorities.csv", "roles.csv",
                  "rules.json", "temporal.json", *RESULT_FILES}


def relative_path(value: str) -> PurePosixPath:
    """Reject traversal, platform-dependent separators and ambiguous ZIP names."""
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(ord(char) < 32 for char in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("Unsafe package path")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("Absolute package path is forbidden")
    return path


def safe_file(base: Path, relative: str) -> Path:
    path = base
    if base.is_symlink():
        raise ValueError("Symlinks are forbidden")
    for part in relative_path(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError("Symlinks are forbidden")
    if not path.is_file():
        raise ValueError("Required package file is missing")
    return path


def reject_secrets(content: bytes) -> None:
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        # Never include matching content, file excerpts, or credentials in logs.
        raise ValueError("Possible credential detected; package was not created")


def selected_source(name: str) -> bool:
    path = relative_path(name)
    if any(part in EXCLUDED_DIRS or part.startswith(".venv") for part in path.parts):
        return False
    if any(part.startswith(".env") for part in path.parts) and name != ".env.example":
        return False
    if path.suffix in {".parquet", ".pyc", ".pyo", ".pem", ".key", ".tsbuildinfo"}:
        return False
    return (name in ROOT_FILES or (len(path.parts) == 1 and name.startswith("compose") and path.suffix == ".yaml")
            or path.parts[0] in SOURCE_DIRS or path.parts[:2] == (".github", "workflows"))


def git(*args: str, root: Path = ROOT) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise ValueError("Git check failed; commit tracked changes before packaging")
    return result.stdout


def source_snapshot(root: Path) -> tuple[str, dict[str, bytes], dict[str, int]]:
    git("diff", "--quiet", "HEAD", "--", root=root)
    commit = git("rev-parse", "HEAD", root=root).decode().strip()
    payload, modes = {}, {}
    for entry in git("ls-files", "--stage", "-z", root=root).split(b"\0"):
        if not entry:
            continue
        info, raw_name = entry.split(b"\t", 1)
        mode, _, stage = info.split()
        name = raw_name.decode("utf-8")
        if not selected_source(name):
            continue
        if mode not in {b"100644", b"100755"} or stage != b"0":
            raise ValueError("Source symlinks, submodules and conflicts are forbidden")
        safe_file(root, name)
        content = git("show", f"{commit}:{name}", root=root)
        reject_secrets(content)
        payload[f"source/{name}"] = content
        modes[f"source/{name}"] = int(mode[-3:], 8)
    if "source/docs/submission.md" not in payload or "source/scripts/package_submission.py" not in payload:
        raise ValueError("Commit the submission guide and packaging script first")
    return commit, payload, modes


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def artifact_snapshot(run_dir: Path) -> tuple[str, dict[str, bytes], dict]:
    if run_dir.is_symlink():
        raise ValueError("Symlinks are forbidden")
    manifest_bytes = safe_file(run_dir, "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    run_id = manifest["run_id"]
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", run_id):
        raise ValueError("Unsafe run ID")
    if manifest.get("status") != "complete" or not isinstance(manifest.get("files"), dict):
        raise ValueError("A complete artifact manifest is required")
    files = {"manifest.json": manifest_bytes}
    for name in manifest["files"].values():
        relative_path(name)
        if name not in ARTIFACT_FILES:
            raise ValueError("Unexpected artifact filename")
        if name not in files:
            files[name] = safe_file(run_dir, name).read_bytes()
    if set(files) != ARTIFACT_FILES:
        raise ValueError("The full current artifact set is required")
    for content in files.values():
        reject_secrets(content)
    hashes = json.loads(files["audit.json"])["artifact_hashes"]
    if set(hashes) != set(files) - {"audit.json", "manifest.json"}:
        raise ValueError("Audit does not cover every included artifact")
    if any(digest(files[name]) != expected for name, expected in hashes.items()):
        raise ValueError("Artifact bytes do not match the immutable audit")
    if digest(files["rules.json"]) != manifest["rules_hash"]:
        raise ValueError("Rules do not match the manifest")
    # Verify the exact captured bytes, so a concurrent change in the source run
    # cannot mix verified CSVs with a different payload.
    with tempfile.TemporaryDirectory(prefix="money-graph-verify-") as directory:
        snapshot = Path(directory)
        for name, content in files.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        report = verify(snapshot)
    return run_id, files, report


def publish_zip(output: Path, payload: dict[str, bytes], modes: dict[str, int]) -> None:
    """Publish atomically without replacing an existing different package."""
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".packaging-", suffix=".zip", dir=output.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, content in sorted(payload.items()):
                relative_path(name)
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100000 | modes.get(name, 0o644)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content, compresslevel=9)
        try:
            os.link(temporary_path, output)
        except FileExistsError:
            if output.is_symlink() or output.read_bytes() != temporary_path.read_bytes():
                raise ValueError("A different package already exists; nothing was overwritten") from None
    finally:
        temporary_path.unlink(missing_ok=True)


def package(run_dir: Path, verification: Path, output_dir: Path, root: Path = ROOT) -> dict:
    commit, payload, modes = source_snapshot(root)
    run_id, artifacts, report = artifact_snapshot(run_dir)
    source_hashes = {PurePosixPath(name).name: digest(content) for name, content in payload.items()
                     if (PurePosixPath(name).parent == PurePosixPath("source/backend/app/analytics")
                         and name.endswith(".py")) or name == "source/backend/app/contracts.py"}
    if digest(json_bytes(source_hashes)) != json.loads(artifacts["manifest.json"])["versions"]["source_sha256"]:
        raise ValueError("The run was calculated with different analytics source code")
    verification_bytes = safe_file(verification.parent, verification.name).read_bytes()
    reject_secrets(verification_bytes)
    supplied = json.loads(verification_bytes)
    if (supplied.get("status") != "passed" or supplied.get("run_id") != run_id
            or supplied.get("sha256") != report["sha256"]):
        raise ValueError("Verification does not match the completed run and current CSV bytes")
    payload.update({f"artifacts/{run_id}/{name}": content for name, content in artifacts.items()})
    payload.update({f"results/{name}": artifacts[name] for name in RESULT_FILES})
    payload["verification.json"] = verification_bytes
    payload["README.md"] = payload["source/docs/submission.md"]
    payload["package-metadata.json"] = json_bytes({
        "format_version": 1, "source_commit": commit, "run_id": run_id,
        "counts": report["counts"], "source": "source/", "results": "results/",
        "artifacts": f"artifacts/{run_id}/", "raw_data_included": False,
    })
    payload["SHA256SUMS"] = "".join(f"{digest(content)}  {name}\n" for name, content in sorted(payload.items())).encode()
    # Recheck all payloads, including source copies and generated metadata.
    for content in payload.values():
        reject_secrets(content)
    git("diff", "--quiet", "HEAD", "--", root=root)
    if git("rev-parse", "HEAD", root=root).decode().strip() != commit:
        raise ValueError("Source commit changed while packaging; retry")
    output = output_dir / f"money-graph-{run_id}-{commit[:12]}.zip"
    publish_zip(output, payload, modes)
    return {"status": "created", "archive": str(output.resolve()), "run_id": run_id,
            "source_commit": commit, "files": len(payload), "sha256": digest(output.read_bytes())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--verification", required=True, type=Path)
    parser.add_argument("--output-dir", default=ROOT / "submission", type=Path)
    args = parser.parse_args()
    try:
        result = package(args.run_dir, args.verification, args.output_dir)
    except (ValueError, KeyError, OSError, TypeError, UnicodeError, zipfile.BadZipFile):
        # Details of malformed inputs can contain credentials. Report only a
        # stable, content-free diagnostic; no traceback or subprocess output.
        parser.exit(1, "Package not created: check clean committed sources, safe paths, credentials and matching verification.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
