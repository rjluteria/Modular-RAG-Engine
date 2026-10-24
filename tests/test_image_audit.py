import json

import pytest

from scripts.check_image_audit import check


@pytest.mark.parametrize(
    ("vulnerability", "secret", "allowed"),
    [
        ({"Severity": "HIGH"}, False, False),
        ({"Severity": "UNKNOWN", "FixedVersion": "2"}, False, False),
        ({"Severity": "MEDIUM"}, False, True),
        ({"Severity": "LOW", "FixedVersion": "2"}, False, True),
        (None, True, False),
        (None, False, True),
    ],
)
def test_image_gate_keeps_residual_findings_visible_and_blocks_secrets(
    tmp_path, vulnerability, secret, allowed
):
    path = tmp_path / "scan.json"
    path.write_text(json.dumps({
        "Metadata": {"ImageID": "sha256:synthetic"},
        "Results": [{
            "Vulnerabilities": [vulnerability] if vulnerability else [],
            "Secrets": [{"RuleID": "synthetic"}] if secret else [],
        }],
    }))
    assert check(path) is allowed


def test_image_gate_rejects_missing_scan_evidence(tmp_path):
    path = tmp_path / "scan.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="missing scan evidence"):
        check(path)
