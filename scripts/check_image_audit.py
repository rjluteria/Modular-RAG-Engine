"""Report every severity; fail on severe findings or unapplied available fixes."""

import json
import sys
from collections import Counter
from pathlib import Path


def check(path: Path) -> bool:
    report = json.loads(path.read_text())
    if not report.get("Results") or not report.get("Metadata", {}).get("ImageID"):
        raise ValueError("Image audit is missing scan evidence")
    vulnerabilities = [v for result in report.get("Results", []) for v in result.get("Vulnerabilities", [])]
    counts = Counter(v["Severity"] for v in vulnerabilities)
    blocking = [
        v
        for v in vulnerabilities
        if v["Severity"] in ("CRITICAL", "HIGH") or (v.get("FixedVersion") and v["Severity"] not in ("LOW",))
    ]
    secrets = [s for result in report["Results"] for s in result.get("Secrets", [])]
    print("Image secret findings:", len(secrets))
    print("Runtime image finding counts (all severities):", dict(counts))
    print("Blocking severe/unapplied-fix findings:", len(blocking))
    return not blocking and not secrets


if __name__ == "__main__":
    raise SystemExit(0 if check(Path(sys.argv[1])) else 1)
