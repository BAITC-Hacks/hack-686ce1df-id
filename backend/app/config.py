"""Explicit, local configuration. Importing the app never starts a pipeline."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "contract-v1"


@dataclass(frozen=True)
class Settings:
    run_dir: Path | None = None
    fixture_mode: bool = False
    demo_mode: bool = False
    demo_dir: Path | None = None
    frontend_dist: Path | None = None
    frontend_origin: str = "http://localhost:5173"
    ai_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if sum((self.fixture_mode, self.demo_mode, self.run_dir is not None)) > 1:
            raise ValueError("Choose only one of AML_FIXTURE_MODE, AML_DEMO_MODE, or AML_RUN_DIR.")
        if self.demo_dir is not None and not self.demo_mode:
            raise ValueError("AML_DEMO_DIR requires AML_DEMO_MODE=true or --demo.")
        if self.demo_mode and self.demo_dir is None:
            object.__setattr__(self, "demo_dir", PROJECT_ROOT / "data" / "demo")
        if not 0 < self.ai_timeout_seconds <= 30:
            raise ValueError("AI_TIMEOUT_SECONDS must be greater than 0 and at most 30.")
        origin = urlsplit(self.frontend_origin)
        if (
            origin.scheme != "http"
            or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
            or origin.path or origin.query or origin.fragment or origin.username
        ):
            raise ValueError("AML_FRONTEND_ORIGIN must be a local HTTP origin without a path.")
        # Accessing port also rejects malformed port strings.
        _ = origin.port

    @classmethod
    def from_environment(cls, **overrides) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        fixture_mode = os.getenv("AML_FIXTURE_MODE", "false").lower()
        if fixture_mode not in {"true", "false", "1", "0"}:
            raise ValueError("AML_FIXTURE_MODE must be true, false, 1, or 0.")
        demo_mode = os.getenv("AML_DEMO_MODE", "false").lower()
        if demo_mode not in {"true", "false", "1", "0"}:
            raise ValueError("AML_DEMO_MODE must be true, false, 1, or 0.")
        run_dir = os.getenv("AML_RUN_DIR", "")
        demo_dir = os.getenv("AML_DEMO_DIR", "")
        frontend_dist = os.getenv("AML_FRONTEND_DIST", "")
        values = {
            "run_dir": Path(run_dir) if run_dir else None,
            "fixture_mode": fixture_mode in {"true", "1"},
            "demo_mode": demo_mode in {"true", "1"},
            "demo_dir": Path(demo_dir) if demo_dir else None,
            "frontend_dist": Path(frontend_dist) if frontend_dist else PROJECT_ROOT / "frontend" / "dist",
            "frontend_origin": os.getenv("AML_FRONTEND_ORIGIN", "http://localhost:5173"),
            "ai_timeout_seconds": float(os.getenv("AI_TIMEOUT_SECONDS", "30")),
        }
        # Explicit CLI source selection replaces environment source fields
        # before validation, including mutually exclusive environment modes.
        return cls(**(values | overrides))
