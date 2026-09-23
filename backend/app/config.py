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
    frontend_dist: Path | None = None
    frontend_origin: str = "http://localhost:5173"
    ai_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.fixture_mode and self.run_dir is not None:
            raise ValueError("Choose AML_FIXTURE_MODE or AML_RUN_DIR, not both.")
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
    def from_environment(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        fixture_mode = os.getenv("AML_FIXTURE_MODE", "false").lower()
        if fixture_mode not in {"true", "false", "1", "0"}:
            raise ValueError("AML_FIXTURE_MODE must be true, false, 1, or 0.")
        run_dir = os.getenv("AML_RUN_DIR", "")
        frontend_dist = os.getenv("AML_FRONTEND_DIST", "")
        return cls(
            run_dir=Path(run_dir) if run_dir else None,
            fixture_mode=fixture_mode in {"true", "1"},
            frontend_dist=Path(frontend_dist) if frontend_dist else PROJECT_ROOT / "frontend" / "dist",
            frontend_origin=os.getenv("AML_FRONTEND_ORIGIN", "http://localhost:5173"),
            ai_timeout_seconds=float(os.getenv("AI_TIMEOUT_SECONDS", "30")),
        )
