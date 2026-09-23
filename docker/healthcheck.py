"""Readiness means that both a complete dataset and the built UI are available."""

import json
import sys
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def main() -> int:
    # Docker can inherit host proxy settings; readiness stays inside the container.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:8000/api/health", timeout=2) as response:
            health = json.load(response)
        if health.get("data_ready") is not True:
            raise ValueError("No complete dataset loaded; inspect container logs.")
        with opener.open("http://127.0.0.1:8000/", timeout=2) as response:
            if response.headers.get_content_type() != "text/html":
                raise ValueError("Built frontend is unavailable.")
    except (URLError, OSError, ValueError) as error:
        print(f"Not ready: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
