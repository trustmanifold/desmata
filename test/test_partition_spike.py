"""Phase 1 partition spike: a nix closure moves between two ipfs peers by hash,
offline, and is reconstructed in a store that didn't have it.

This is the single most important assumption behind desmata's partition
tolerance. We prove the *data path* on one machine: peer A packages a closure
into a CAR file, peer B (a separate ipfs repo and a separate, initially-empty nix
store) reconstructs it from that CAR with no network. Cutting the real internet
and forcing B to fetch from A over a socket is a later, container-based phase;
here we prove the bytes flow by content address and `nix-store --import`
reconstructs them.

Peer A is the builtin cell's ipfs (session fixture); peer B is a second
Tools.IPFS on an independent home, sharing only the kubo *binary*.
"""

from pathlib import Path

from desmata.builtins.cell import DesmataBuiltins
from desmata.log import TestLoggers
from desmata.nix import Nix
from desmata.transport import export_closure_to_car, import_car_to_store

from conftest import make_ipfs


def test_closure_round_trips_between_peers_offline(
    builtins: DesmataBuiltins, tmp_path: Path
):
    kubo_root = Path(builtins.closure.ipfs.root)

    # peer A: the builtin cell's own ipfs repo
    ipfs_a = builtins.ipfs
    ipfs_a.init()  # idempotent

    # peer B: a second, independent ipfs repo
    ipfs_b = make_ipfs(kubo_root, tmp_path / "peerB", name="peerB")

    nix = Nix(cwd=tmp_path, log=TestLoggers().proc)

    # the dependency to move: a minimal closure (a leaf store path)
    material = tmp_path / "material.txt"
    material.write_text("hello from peer A\n")
    path = nix.add_to_store(material)

    a_work = tmp_path / "A"
    a_work.mkdir()
    b_work = tmp_path / "B"
    b_work.mkdir()

    # peer A packages the closure into a content-addressed CAR file
    cid, car = export_closure_to_car(nix, ipfs_a, path, workdir=a_work)
    assert cid.startswith("Qm")
    assert car.exists()

    # peer B's store genuinely does not have the path yet
    bstore = (tmp_path / "bstore").resolve()
    rel = path.lstrip("/")
    assert not (bstore / rel).exists()

    # peer B reconstructs it from the CAR alone -- no rebuild, no network
    imported = import_car_to_store(
        nix, ipfs_b, car, cid, workdir=b_work, store=bstore
    )

    # the reconstructed identity matches, and the path now exists in B's store
    assert path in imported
    assert (bstore / rel).exists()

    # the bytes survived the round trip exactly (CID integrity across two repos)
    assert (a_work / "closure.nar").read_bytes() == (
        b_work / "recovered.nar"
    ).read_bytes()
