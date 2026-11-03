"""The reference provider under the anolis-protocol conformance harness.

The harness is the cross-language oracle: every SDK's reference provider runs
it, so parity comes from the same tests rather than from comparing SDKs. This
test requires zero failures and lists any skip, so a skip is a visible choice.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Skips the reference provider is allowed, with the reason. Anything else that
# skips fails this test, so a skip cannot appear unnoticed.
EXPECTED_SKIPS: dict[str, str] = {}


@pytest.mark.conformance
def test_reference_provider_passes_the_harness(tmp_path: Path) -> None:
    bindir = str(Path(sys.executable).parent)
    harness = shutil.which("anolis-adpp-conformance", path=bindir)
    provider = shutil.which("anolis-provider-reference", path=bindir)
    assert harness and provider, "install the dev extra (uv sync --extra dev)"
    report = tmp_path / "report.xml"
    proc = subprocess.run(
        [
            harness,
            "--provider-bin",
            provider,
            "--provider-config",
            str(REPO / "config" / "conformance.yaml"),
            "--provider-profile",
            str(REPO / "config" / "conformance.toml"),
            f"--junitxml={report}",
            "-p",
            "no:cacheprovider",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=tmp_path,
    )
    cases = list(ET.parse(report).iter("testcase"))
    failed = [c.get("name") for c in cases if c.find("failure") is not None or c.find("error") is not None]
    skipped = {c.get("name"): (s.get("message") or "") for c in cases if (s := c.find("skipped")) is not None}
    assert proc.returncode == 0 and not failed, f"failed: {failed}\n{proc.stdout[-4000:]}"
    unexpected = {name: why for name, why in skipped.items() if name not in EXPECTED_SKIPS}
    assert not unexpected, f"unexpected skips: {unexpected}"
    assert len(cases) - len(skipped) > 40, f"only {len(cases)} cases collected"
