"""python -m backend --fixtures, or --run-dir artifacts/<run_id>."""

import argparse
from dataclasses import replace
from pathlib import Path

import uvicorn

from backend.app.config import Settings
from backend.app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve one completed Money Graph run.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--fixtures", action="store_true", help="Explicitly serve artificial development data")
    source.add_argument("--run-dir", type=Path, help="Completed analytics run directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    settings = Settings.from_environment()
    if args.fixtures:
        settings = replace(settings, fixture_mode=True, run_dir=None)
    elif args.run_dir:
        settings = replace(settings, fixture_mode=False, run_dir=args.run_dir)
    uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
