"""Content-addressing a cell's nucleus: pack it on one peer, reconstruct it
byte-for-byte on another, and confirm the nucleus hash is deterministic. This is
the storage foundation for fetching a cell by its hash (`from_hash`).
"""

from pathlib import Path

import desmata.samples.greeter.cell as greeter
from desmata.builtins.cell import DesmataBuiltins
from desmata.cell_archive import NUCLEUS, nucleus_hash, pack_cell, unpack_cell

from conftest import make_ipfs


def test_pack_then_unpack_cell_nucleus_across_peers(
    builtins: DesmataBuiltins, tmp_path: Path
):
    cell_dir = Path(greeter.__file__).parent

    # peer A packs the greeter cell's nucleus
    ipfs_a = builtins.ipfs
    ipfs_a.init()
    cid, car = pack_cell(ipfs_a, cell_dir, workdir=tmp_path)
    assert cid.digest.startswith("bafy")     # CIDv1 dag-cbor manifest
    assert str(cid).startswith("dsm:ipfs:")  # self-describing string form
    assert nucleus_hash(ipfs_a, cell_dir) == cid  # deterministic

    # peer B reconstructs it from the CAR alone
    ipfs_b = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "peerB")
    into = tmp_path / "unpacked"
    names = unpack_cell(ipfs_b, car, cid, into)

    assert set(names) == {n for n in NUCLEUS if (cell_dir / n).exists()}
    # the nucleus files round-trip byte-for-byte
    for name in names:
        assert (into / name).read_bytes() == (cell_dir / name).read_bytes()
