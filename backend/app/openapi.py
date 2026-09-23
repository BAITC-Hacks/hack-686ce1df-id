"""Generate or check the checked-in OpenAPI contract without loading artifacts."""

import argparse
import json

from backend.app.config import PROJECT_ROOT, Settings
from backend.app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    schema = create_app(Settings()).openapi()
    target = PROJECT_ROOT / "contracts" / "openapi.json"
    if args.check:
        if not target.is_file() or json.loads(target.read_text(encoding="utf-8")) != schema:
            raise SystemExit("OpenAPI snapshot is stale. Run: python -m backend.app.openapi")
        print("OpenAPI snapshot matches the application.")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(schema, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Wrote {target}")


if __name__ == "__main__":
    main()
