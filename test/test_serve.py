"""The daemon lifecycle behind `dsm serve`: it comes up on a repo, its
presence is visible to the rest of desmata (which is how from_hash knows the
online fallback is available), and it goes away cleanly."""

from pathlib import Path

from desmata import serve
from desmata.builtins.cell import DesmataBuiltins

from conftest import isolate_node, make_ipfs


def test_daemon_lifecycle(builtins: DesmataBuiltins, tmp_path: Path):
    ipfs = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "node")
    isolate_node(ipfs)  # random localhost ports, no public network

    assert not ipfs.daemon_running()
    with serve.running(ipfs) as process:
        assert ipfs.daemon_running()
        assert process.poll() is None
        # commands now route through the daemon and still work
        assert ipfs("id").strip()
    assert not ipfs.daemon_running()
