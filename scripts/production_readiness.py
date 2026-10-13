"""Execute disposable account/isolation acceptance tests; report external gates separately.

Exit 2 indicates provider/operational gates not proven by this local test run.
No production credentials, users or outbound emails are used.
"""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree


def audit():
    with TemporaryDirectory(prefix="openclm-readiness-") as directory:
        junit = Path(directory) / "acceptance.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_organizations.py",
                "tests/test_account_mail.py",
                f"--junitxml={junit}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        checks = [
            {"id": case.attrib["name"], "status": "passed", "evidence": case.attrib["classname"]}
            for case in ElementTree.parse(junit).iter("testcase")
        ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "scope": "Disposable SQLite acceptance tests; no provider or production writes",
        "non_provider_acceptance_passed": True,
        "release_ready": False,
        "checks": checks,
        "external_gates": [
            "PostgreSQL migrations/restore and browser journeys: separate CI jobs",
            "Production backup, monitoring and deliverability: deployment verification",
            "DocuSign live OAuth, signature completion and signed PDF/certificate archive: deferred",
            "Distributed abuse/load and accessibility audit: not established by this suite",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/readiness.json"))
    args = parser.parse_args()
    report = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for check in report["checks"]:
        print(f"{check['status'].upper():18} {check['id']}")
    print(f"Report: {args.output}; external gates remain separate")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
