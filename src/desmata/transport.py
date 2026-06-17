"""Moving a built dependency between peers by hash, offline.

This is the heart of desmata's partition-tolerance bet: a peer who already has a
nix store closure can hand it to a peer who lacks it, with no rebuild and no
internet, because everything is addressed by hash. The transport is a
"sneakernet" CAR round-trip:

    closure --(nix-store --export)--> NAR --(ipfs add)--> repo A
            --(ipfs dag export)--> CAR  ===handed to B===  CAR
            --(ipfs dag import)--> repo B --(ipfs get)--> NAR
            --(nix-store --import)--> reconstructed closure

The two halves are deliberately split so the producer (peer A) and consumer
(peer B) can run on different machines at different times. The only thing that
crosses between them is the CAR file -- itself content-addressed by ``cid``.

These functions are what a future ``dsm bootstrap --source peer`` will call; for
now they are exercised by the partition spike test.

KNOWN GAP — deduplication. This transport ships a whole closure as one opaque
NAR blob (`nix-store --export`), then `ipfs add`s it. IPFS's default fixed-size
chunker cuts at byte offsets, so a dependency shared by two closures lands on
*misaligned* block boundaries in each NAR and is transferred/stored once per
closure, not once total. The per-store-path / per-file storage model that
`desmata.inspect` (and `test_dedup.py`) exercise *does* dedup shared content for
free (identical bytes -> identical CID). To make the transport inherit that,
move closures **per store path** -- a CAR built from individual `ipfs add`s plus
a small IPLD manifest whose links point to each path's CID -- so a shared
dependency becomes a shared sub-DAG. That manifest also lines up with how cells
already internalize dependencies per path. Verify any fix with `dsm inspect
<cell> <tool> ipfs`: a path shared across two tools must show the same CID.
"""

from pathlib import Path

from desmata.builtins.cell import Tools
from desmata.nix import Nix


def export_closure_to_car(
    nix: Nix, ipfs: Tools.IPFS, path: str, *, workdir: Path
) -> tuple[str, Path]:
    """Peer A: package ``path``'s whole closure into a CAR file.

    Returns the ``cid`` addressing the closure and the path to the CAR file
    (written under ``workdir``). The cid is the same wherever this runs -- it is
    how peer B asks for exactly these bytes.
    """
    nar = workdir / "closure.nar"
    nix.export_closure(nix.closure_paths(path), dest=nar)

    cid = ipfs.add(nar)
    car = workdir / f"{cid}.car"
    ipfs.dag_export(cid, dest=car)
    return cid, car


def import_car_to_store(
    nix: Nix,
    ipfs: Tools.IPFS,
    car: Path,
    cid: str,
    *,
    workdir: Path,
    store: Path | None = None,
) -> list[str]:
    """Peer B: reconstruct a closure from a CAR file received from peer A.

    Loads the CAR into B's ipfs repo, recovers the NAR addressed by ``cid``, and
    imports it into the nix store (``store`` selects an alternate local store
    root). Returns the store paths reconstructed.
    """
    ipfs.dag_import(car)

    nar = workdir / "recovered.nar"
    ipfs.get(cid, nar)
    return nix.import_closure(nar, store=store)
