"""Phase 2 partition transport: a nix closure moves between two ipfs peers by
hash, offline, per store path, and reconstructs in a store that didn't have it.

This is the single most important assumption behind desmata's partition
tolerance. We prove the *data path* on one machine: peer A packages a closure as
per-path NARs under an IPLD manifest (one CAR), peer B (a separate ipfs repo and a
separate, initially-empty nix store) reconstructs it from that CAR with no
network. Cutting the real internet and forcing B to fetch from A over a socket is
a later, container-based phase.

Crucially the transport is per store path, so a dependency shared by two closures
exports to the *same* NAR CID — dedup is preserved over the wire (the whole point
of thread 3).
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

    # peer A packages the closure into a content-addressed CAR (IPLD manifest)
    manifest_cid, car = export_closure_to_car(nix, ipfs_a, path, workdir=a_work)
    assert manifest_cid.digest.startswith("bafy")  # CIDv1 dag-cbor manifest
    assert car.exists()

    # peer B's store genuinely does not have the path yet
    bstore = (tmp_path / "bstore").resolve()
    rel = path.lstrip("/")
    assert not (bstore / rel).exists()

    # peer B reconstructs it from the CAR alone -- no rebuild, no network
    imported = import_car_to_store(
        nix, ipfs_b, car, manifest_cid, workdir=b_work, store=bstore
    )

    # the reconstructed identity matches, and the path now exists in B's store
    assert path in imported
    assert (bstore / rel).exists()


def test_transport_preserves_cross_closure_dedup(
    builtins: DesmataBuiltins, tmp_path: Path
):
    ipfs_a = builtins.ipfs
    ipfs_a.init()
    nix = Nix(cwd=tmp_path, log=TestLoggers().proc)

    kubo = str(builtins.closure.ipfs.root)
    tz = next(
        str(i.path) for i in nix.closure_info(kubo) if "tzdata" in str(i.path)
    )

    full = tmp_path / "full"
    full.mkdir()
    alone = tmp_path / "alone"
    alone.mkdir()

    full_cid, _ = export_closure_to_car(nix, ipfs_a, kubo, workdir=full)
    tz_cid, _ = export_closure_to_car(nix, ipfs_a, tz, workdir=alone)

    full_manifest = ipfs_a.dag_get(full_cid.digest)
    tz_manifest = ipfs_a.dag_get(tz_cid.digest)
    cid_in_full = next(
        e["nar"]["/"] for e in full_manifest["paths"] if e["path"] == tz
    )
    cid_alone = next(
        e["nar"]["/"] for e in tz_manifest["paths"] if e["path"] == tz
    )

    # the shared dependency (tzdata) exports to the same NAR CID whether it
    # travels inside kubo's closure or on its own — so a peer that already has it
    # stores/transfers it once
    assert cid_in_full == cid_alone
