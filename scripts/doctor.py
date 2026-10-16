"""Check a running agent API from a developer workstation.

Usage: python scripts/doctor.py --base-url http://127.0.0.1:9005
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class EndpointResult:
    path: str
    http_status: int | None
    body: dict | None
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.http_status == 200 and self.body is not None

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "http_status": self.http_status,
            "body": self.body,
            "error": self.error,
        }


def check_endpoint(base_url: str, path: str, timeout: float) -> EndpointResult:
    request = Request(
        base_url.rstrip("/") + path,
        headers={"Accept": "application/json", "User-Agent": "agent-backend-doctor"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read(64 * 1024)
    except HTTPError as exc:
        status = exc.code
        raw = exc.read(64 * 1024)
    except (URLError, TimeoutError, OSError) as exc:
        return EndpointResult(path, None, None, type(exc).__name__)

    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return EndpointResult(path, status, None, "Invalid JSON response")
    if not isinstance(body, dict):
        return EndpointResult(path, status, None, "Expected a JSON object")
    return EndpointResult(path, status, body)


def summarize(results: list[EndpointResult], machine_readable: bool) -> None:
    if machine_readable:
        print(json.dumps({"ok": all(item.passed for item in results),
                          "endpoints": [item.as_dict() for item in results]}, indent=2))
        return

    for item in results:
        state = "OK" if item.passed else "FAIL"
        code = item.http_status if item.http_status is not None else "no response"
        print(f"{state:4} {item.path:8} HTTP {code}")
        if item.error:
            print(f"     {item.error}")
        if item.path == "/ready" and item.body:
            for name, detail in item.body.get("checks", {}).items():
                print(f"     {name}: {detail.get('status', 'unknown')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:9005")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable output")
    args = parser.parse_args(argv)

    parsed = urlparse(args.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        parser.error("--base-url must be an HTTP or HTTPS URL")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    results = [
        check_endpoint(args.base_url, "/health", args.timeout),
        check_endpoint(args.base_url, "/ready", args.timeout),
    ]
    summarize(results, args.json)
    return 0 if all(item.passed for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
