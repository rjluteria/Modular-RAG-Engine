"""Bind reviewed local runtime inputs and vulnerability evidence to an image."""

import hashlib
import json
from pathlib import Path

from scripts.check_image_audit import check


def runtime_inputs(root: Path) -> dict[str, str]:
    files = [p for folder in ("api", "core", "worker") for p in (root / folder).rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc" and p.name != "AGENTS.md"]
    files += [root / name for name in ("pyproject.toml", "uv.lock", "infra/Dockerfile",
                                      "infra/runtime_libraries.py", "infra/uv-requirements.txt")]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def verify_provenance(config, root: Path) -> None:
    manifest = json.loads(Path(config["image_provenance_file"]).read_text())
    audit_path = Path(config["image_audit_file"])
    audit = json.loads(audit_path.read_text())
    expected = config["verified_image_digest"]
    if (manifest.get("image_digest") != expected or audit.get("Metadata", {}).get("ImageID") != expected
            or manifest.get("audit_sha256") != hashlib.sha256(audit_path.read_bytes()).hexdigest()
            or manifest.get("runtime_inputs") != runtime_inputs(root)):
        raise ValueError("Image provenance does not match current runtime inputs/audit")
    if not check(audit_path):
        raise ValueError("Image audit contains blocking findings")
    if any(r.get("Secrets") for r in audit.get("Results", [])):
        raise ValueError("Image audit contains secret findings")
