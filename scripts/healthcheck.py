"""Exit nonzero on application/database failure; emits no confidential data."""

import os
import sys

import httpx


def main():
    url = os.environ.get("OPENCLM_HEALTH_URL", "https://legalops.dev/openclm/health")
    try:
        response = httpx.get(url, timeout=15, follow_redirects=False)
        response.raise_for_status()
        if response.json().get("status") != "ok":
            raise ValueError("Unhealthy response")
    except Exception:
        print("OpenCLM health check failed", file=sys.stderr)
        return 1
    print("OpenCLM health check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
