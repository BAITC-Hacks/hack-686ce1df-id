"""Serve read-only artifacts, fixture data, or a persistent editable demo."""

import argparse
from pathlib import Path

import uvicorn

from backend.app.config import PROJECT_ROOT, Settings
from backend.app.main import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve one completed Money Graph run.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--fixtures", action="store_true", help="Explicitly serve artificial development data")
    source.add_argument("--demo", action="store_true", help="Serve a persistent, editable synthetic demo")
    source.add_argument("--run-dir", type=Path, help="Completed analytics run directory")
    source.add_argument("--data-dir", type=Path, help="Calculate and serve a run from local Parquet input")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "artifacts", help="Analytics output directory")
    parser.add_argument("--rules", type=Path, default=PROJECT_ROOT / "config" / "rules.json", help="Analytics rules JSON")
    parser.add_argument("--mapping", type=Path, default=PROJECT_ROOT / "config" / "input_mapping.json", help="Input mapping JSON")
    parser.add_argument("--demo-dir", type=Path, help="Persistent demo directory (requires --demo)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.demo_dir is not None and not args.demo:
        parser.error("--demo-dir requires --demo")
    overrides = {}
    if args.fixtures:
        overrides = dict(fixture_mode=True, run_dir=None, demo_mode=False, demo_dir=None)
    elif args.demo:
        overrides = dict(fixture_mode=False, run_dir=None, demo_mode=True)
        if args.demo_dir is not None:
            overrides["demo_dir"] = args.demo_dir
    elif args.data_dir is not None:
        try:
            # Serving existing artifacts or demo data does not require Parquet dependencies.
            from backend.app.analytics.pipeline import run_pipeline

            manifest = run_pipeline(args.data_dir, args.output_dir, args.rules, args.mapping)
        except Exception as exc:
            parser.exit(2, f"Analytics run failed; server was not started: {exc}\n")
        overrides = dict(
            fixture_mode=False, run_dir=args.output_dir / manifest.run_id,
            demo_mode=False, demo_dir=None,
        )
    elif args.run_dir:
        overrides = dict(fixture_mode=False, run_dir=args.run_dir, demo_mode=False, demo_dir=None)
    settings = Settings.from_environment(**overrides)
    uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
