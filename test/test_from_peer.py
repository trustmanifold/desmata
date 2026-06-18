"""Fetch a cell from a peer by its hash alone, then run it.

Peer A publishes the greeter cell (stores its nucleus in A's ipfs). Peer B -- a
separate ipfs repo -- knows only the cid and that A has it; it fetches the bundle
from A, reconstructs the cell, builds its cowsay, and runs it. No prior copy of
the cell on B.
"""

from pathlib import Path

import desmata.samples.greeter.cell as greeter
from desmata.builtins.cell import DesmataBuiltins
from desmata.get import from_peer, publish_cell
from desmata.higher_protocols import CellFactory
from injector import Injector

from conftest import make_ipfs


def test_from_peer_fetches_and_runs_a_cell_by_hash(
    builtins: DesmataBuiltins, components: Injector, tmp_path: Path
):
    # peer A publishes the cell: stores its nucleus, gets back the hash
    ipfs_a = builtins.ipfs
    ipfs_a.init()
    cid = publish_cell(ipfs_a, Path(greeter.__file__).parent)

    # peer B has only the cid + a reference to A; it fetches and runs the cell
    ipfs_b = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "peerB")
    factory = components.get(CellFactory)
    cell = from_peer(
        ipfs_a, ipfs_b, factory, cid, into=tmp_path / "fetched", workdir=tmp_path
    )

    assert "from-a-peer" in cell.greet("from-a-peer")
