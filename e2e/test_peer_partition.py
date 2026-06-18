"""End-to-end peer/partition test (podman). Excluded from the fast suite via
pytest.ini's ``testpaths = test``; run it explicitly with ``pytest e2e`` (or
``e2e/run.sh`` directly).

It builds the desmata-e2e image and stands up two peers on a partitioned
network: peer A on the internet, peer B reachable only to A. See e2e/README.md.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def _podman_ready() -> bool:
    if not shutil.which("podman"):
        return False
    return subprocess.run(["podman", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _podman_ready(), reason="podman not available/running")
def test_peer_partition_e2e():
    result = subprocess.run(
        ["bash", str(HERE / "run.sh")],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, "e2e harness failed (see captured output above)"
