"""Server source selection stays explicit and computes only when requested."""

import builtins
from itertools import combinations
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from backend import __main__ as server_cli
from backend.app import config


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    for name in ("AML_FIXTURE_MODE", "AML_DEMO_MODE", "AML_RUN_DIR", "AML_DEMO_DIR",
                 "AML_FRONTEND_ORIGIN", "AI_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    app = Mock(name="application")
    create = Mock(return_value=app)
    run = Mock()
    monkeypatch.setattr(server_cli, "create_app", create)
    monkeypatch.setattr(server_cli.uvicorn, "run", run)
    return create, run, app


@pytest.fixture
def pipeline(monkeypatch):
    module = ModuleType("backend.app.analytics.pipeline")
    module.run_pipeline = Mock(return_value=SimpleNamespace(run_id="real-test"))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module.run_pipeline


def test_data_dir_computes_then_serves_published_run(server, pipeline, tmp_path, monkeypatch):
    source, output = tmp_path / "input", tmp_path / "output"
    rules, mapping = tmp_path / "rules.json", tmp_path / "mapping.json"
    # Explicit CLI source replaces even mutually conflicting environment modes.
    monkeypatch.setenv("AML_FIXTURE_MODE", "true")
    monkeypatch.setenv("AML_DEMO_MODE", "true")
    monkeypatch.setenv("AML_DEMO_DIR", str(tmp_path / "demo"))
    monkeypatch.setenv("AML_RUN_DIR", str(tmp_path / "old"))

    server_cli.main([
        "--data-dir", str(source), "--output-dir", str(output),
        "--rules", str(rules), "--mapping", str(mapping), "--port", "8091",
    ])

    pipeline.assert_called_once_with(source, output, rules, mapping)
    create, run, app = server
    settings = create.call_args.args[0]
    assert settings.run_dir == output / "real-test"
    assert not settings.demo_mode and not settings.fixture_mode
    assert settings.demo_dir is None
    run.assert_called_once_with(app, host="127.0.0.1", port=8091)


def test_data_dir_uses_project_relative_defaults(server, pipeline, tmp_path):
    source = tmp_path / "input"
    server_cli.main(["--data-dir", str(source)])
    pipeline.assert_called_once_with(
        source, config.PROJECT_ROOT / "artifacts", config.PROJECT_ROOT / "config" / "rules.json",
        config.PROJECT_ROOT / "config" / "input_mapping.json",
    )


@pytest.mark.parametrize("failure", [ValueError("invalid money"), OSError("cannot read input"), ImportError("missing pyarrow")])
def test_failed_calculation_never_starts_server(server, pipeline, failure, capsys):
    pipeline.side_effect = failure
    with pytest.raises(SystemExit) as error:
        server_cli.main(["--data-dir", "data/raw"])
    assert error.value.code == 2
    assert "server was not started" in capsys.readouterr().err
    server[0].assert_not_called()
    server[1].assert_not_called()


SOURCE_ARGS = [("--fixtures",), ("--demo",), ("--run-dir", "artifacts/completed"), ("--data-dir", "data/raw")]


@pytest.mark.parametrize("first,second", list(combinations(SOURCE_ARGS, 2)))
def test_all_source_options_are_mutually_exclusive(server, pipeline, first, second):
    with pytest.raises(SystemExit) as error:
        server_cli.main([*first, *second])
    assert error.value.code == 2
    pipeline.assert_not_called()
    server[0].assert_not_called()


@pytest.mark.parametrize("args", [[], ["--fixtures"], ["--demo"], ["--run-dir", "artifacts/completed"]])
def test_existing_modes_never_import_pipeline(server, monkeypatch, args):
    original_import = builtins.__import__

    def forbid_pipeline(name, *positional, **keywords):
        if name == "backend.app.analytics.pipeline":
            raise AssertionError("Existing sources must not import the pipeline")
        return original_import(name, *positional, **keywords)

    monkeypatch.setattr(builtins, "__import__", forbid_pipeline)
    server_cli.main(args)
    server[1].assert_called_once()
    settings = server[0].call_args.args[0]
    assert settings.fixture_mode is ("--fixtures" in args)
    assert settings.demo_mode is ("--demo" in args)
    assert settings.run_dir == (Path("artifacts/completed") if "--run-dir" in args else None)


def test_data_dir_does_not_accept_demo_directory(server, pipeline):
    with pytest.raises(SystemExit) as error:
        server_cli.main(["--data-dir", "data/raw", "--demo-dir", "data/demo"])
    assert error.value.code == 2
    pipeline.assert_not_called()
    server[1].assert_not_called()
