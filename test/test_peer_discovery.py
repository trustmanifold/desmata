"""The end-to-end flow this was all for: userA publishes a cell and serves it;
userB -- knowing only the hash, never A's address -- discovers it via the DHT,
fetches it, builds it, and calls a function on it.

The swarm is private (shared key, no public bootstrap, localhost-only): the
same discovery mechanics as the public network, hermetically. Three nodes
because discovery needs a rendezvous that is *not* the provider: A and B each
know only the bootstrap node, so B finding A's content proves the DHT did the
work.

The cell is the pinned nushell-cell fixture (NUSHELL_CELL_SRC, a flake input):
a cell authored outside this repo, exactly what a stranger's cell looks like.
"""

import json
import os
import secrets
import time
from pathlib import Path

import pytest
from desmata import serve
from desmata.builtins.cell import DesmataBuiltins, Tools
from desmata.get import from_hash, publish_cell
from desmata.higher_protocols import CellFactory
from injector import Injector

from conftest import isolate_node, make_ipfs

NUSHELL_CELL_SRC = os.environ.get("NUSHELL_CELL_SRC")

pytestmark = [
    pytest.mark.peernet,
    pytest.mark.skipif(
        NUSHELL_CELL_SRC is None,
        reason="NUSHELL_CELL_SRC is not set (the dev shell exports the pinned nushell-cell fixture)",
    ),
]


def _swarm_key() -> str:
    """A fresh private-network key: nodes sharing it form their own swarm."""
    return f"/key/swarm/psk/1.0.0/\n/base16/\n{secrets.token_hex(32)}\n"


def _local_addrs(node: Tools.IPFS) -> list[str]:
    """The node's dialable localhost multiaddrs, peer id included."""
    info = json.loads(node("id"))
    addrs = []
    for addr in info["Addresses"] or []:
        if not addr.startswith("/ip4/127.0.0.1/"):
            continue
        if "/p2p/" not in addr:
            addr = f"{addr}/p2p/{info['ID']}"
        addrs.append(addr)
    assert addrs, f"node has no dialable local addresses: {info}"
    return addrs


def _await_connected(node: Tools.IPFS, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if node("swarm", "peers", tolerate_err=True).strip():
            return
        time.sleep(0.5)
    raise AssertionError("node never connected to the swarm")


def _provide(node: Tools.IPFS, cid: str) -> None:
    """Announce ``cid`` to the DHT now, instead of on the reprovider's
    schedule. The subcommand moved between kubo versions."""
    for subcommand in (["routing", "provide"], ["dht", "provide"]):
        if node.run(*subcommand, cid, tolerate_err=True).exit_code == 0:
            return
    raise AssertionError("no working provide subcommand in this kubo")


def test_fetch_by_hash_alone_via_dht_discovery(
    builtins: DesmataBuiltins, components: Injector, tmp_path: Path
):
    kubo = Path(builtins.closure.ipfs.root)
    key = _swarm_key()

    boot = make_ipfs(kubo, tmp_path / "boot", name="boot")
    peer_a = make_ipfs(kubo, tmp_path / "peerA", name="peerA")
    peer_b = make_ipfs(kubo, tmp_path / "peerB", name="peerB")
    for node in (boot, peer_a, peer_b):
        isolate_node(node, swarm_key=key)

    # userA publishes the cell -- publishing is offline, no daemon involved
    hashes = publish_cell(peer_a, Path(NUSHELL_CELL_SRC))

    with serve.running(boot):
        # A and B are told about the rendezvous only; B never learns A's address
        for node in (peer_a, peer_b):
            for addr in _local_addrs(boot):
                node("bootstrap", "add", addr)

        with serve.running(peer_a), serve.running(peer_b):
            _await_connected(peer_a)
            _await_connected(peer_b)
            _provide(peer_a, hashes.cell_hash.digest)

            # userB: hash in, running cell out
            factory = components.get(CellFactory)
            cell = from_hash(
                peer_b,
                factory,
                str(hashes.cell_hash),
                into=tmp_path / "fetched",
                fetch_timeout=180,
            )
            assert (
                cell.str_to_str(
                    stages=['split column ","', "get column0", "get 0"],
                    input="foo,bar,baz",
                )
                == "foo"
            )
