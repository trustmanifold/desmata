"""Semantic Paint packaged as a session cell — the SP-as-a-cell proof.

`spd` (the SP node daemon) is a server: it binds ports, is configured entirely
by env (SP_DATA_DIR is its state home), and stops on SIGTERM. That is exactly
the serverful shape Cell.session() drives -- setup starts it against the
session home, teardown stops it -- so packaging SP needs nothing from the
gossip/verification layer (it is a Category-2 *distributable artifact*, not a
verifiable-function cell; agent_primers/session-cells.md §4).

This drives the *real* hermetic spd (SP flake `packages.spd`) through the
session seam. It is opt-in: skips when the SemanticPaint checkout or a working
`nix build .#spd` isn't available, the same way desmata's other sibling-cell
tests skip. A productionized cell lives in a standalone `spd-cell` repo
(mirroring gnize-cell); here the wrapper is defined inline to prove the shape.
"""

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from desmata.interface import Cell, Closure
from desmata.session import Persistent
from test_session import make_context  # the sandbox BasicContext helper

pytestmark = pytest.mark.spd

SP_DIR = Path(os.environ.get("SEMANTICPAINT_DIR", str(Path.home() / "src" / "SemanticPaint")))


def _build_spd() -> Path:
    """`nix build .#spd` in the SP checkout -> the `spd` binary, or skip."""
    if not (SP_DIR / "flake.nix").exists():
        pytest.skip(f"no SemanticPaint checkout at {SP_DIR}")
    try:
        out = subprocess.run(
            ["nix", "build", ".#spd", "--print-out-paths", "--no-link"],
            cwd=SP_DIR, capture_output=True, text=True, timeout=1200,
        )
    except FileNotFoundError:
        pytest.skip("nix not available")
    if out.returncode != 0:
        pytest.skip(f"nix build .#spd failed:\n{out.stderr[-800:]}")
    spd = Path(out.stdout.strip().splitlines()[-1]) / "bin" / "spd"
    if not spd.exists():
        pytest.skip(f"spd binary missing at {spd}")
    return spd


@pytest.fixture(scope="module")
def spd_bin() -> Path:
    return _build_spd()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _NoDeps(Closure):
    pass


class SpdNode:
    """The session handle: the daemon process and where to reach its API."""

    def __init__(self, process: subprocess.Popen, admin_port: int):
        self.process = process
        self.admin_port = admin_port


class SpdCell(Cell[_NoDeps]):
    """SP's node daemon as a session cell. setup starts spd against the session
    home (SP_DATA_DIR); teardown SIGTERMs it. Category-2: nothing gossip-side."""

    def __init__(self, closure: _NoDeps, context, spd_bin: Path):
        super().__init__(closure, context)
        self.spd_bin = spd_bin

    def setup(self, home: Path) -> SpdNode:
        admin, gossip = _free_port(), _free_port()
        proc = subprocess.Popen(
            [str(self.spd_bin), "run"],
            env={
                "SP_AGENT_ID": "probe",
                "SP_ADMIN_PORT": str(admin),
                "SP_GOSSIP_PORT": str(gossip),
                "SP_DATA_DIR": str(home),  # the node's state lives in the session home
                "SP_DISCOVERY": "harness",
                "SP_ENGINE": "none",
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"spd exited early ({proc.returncode})")
            if self._healthz(admin):
                return SpdNode(proc, admin)
            time.sleep(0.3)
        proc.terminate()
        raise TimeoutError("spd never became healthy")

    def teardown(self, handle: SpdNode | None) -> None:
        if handle is not None and handle.process.poll() is None:
            handle.process.terminate()
            try:
                handle.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                handle.process.kill()
                handle.process.wait()

    @staticmethod
    def _healthz(port: int) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def healthz(self, session) -> bool:
        return self._healthz(session.handle.admin_port)


def _cell(tmp_path: Path, spd_bin: Path, name: str = "spd") -> SpdCell:
    return SpdCell(_NoDeps(local_name=name), make_context(tmp_path, name=name), spd_bin)


def test_spd_serves_through_a_session(tmp_path: Path, spd_bin: Path):
    cell = _cell(tmp_path, spd_bin)
    with cell.session() as s:  # Ephemeral by default
        assert s.handle.process.poll() is None
        pid = s.handle.process.pid
        # one bring-up, many requests through the one live node
        for _ in range(5):
            assert cell.healthz(s)
            assert s.handle.process.pid == pid
        home = s.home
        assert home.exists()
    assert s.handle.process.poll() is not None  # stopped on teardown
    assert not home.exists()  # ephemeral state dir discarded


def test_spd_persistent_home_is_reused(tmp_path: Path, spd_bin: Path):
    cell = _cell(tmp_path, spd_bin)
    with cell.session(home=Persistent(name="node")) as s:
        assert cell.healthz(s)
        first = s.home
    # a second session over the same named home serves the same state dir
    with cell.session(home=Persistent(name="node")) as s:
        assert cell.healthz(s)
        assert s.home == first
        assert s.home.exists()
