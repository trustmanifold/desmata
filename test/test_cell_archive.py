"""Content-addressing a cell: pack it on one peer, reconstruct it byte-for-byte
on another, and confirm the hashes are deterministic. This is the storage
foundation for fetching a cell by its hash (`from_hash`).
"""

from pathlib import Path

import desmata.samples.greeter.cell as greeter
from desmata.builtins.cell import DesmataBuiltins
from desmata.cell_archive import (
    cell_hashes,
    membrane_files,
    nucleus_hash,
    nucleus_names,
    pack_cell,
    unpack_cell,
)

from conftest import make_ipfs


def test_pack_then_unpack_cell_across_peers(
    builtins: DesmataBuiltins, tmp_path: Path
):
    cell_dir = Path(greeter.__file__).parent

    # peer A packs the whole greeter cell
    ipfs_a = builtins.ipfs
    ipfs_a.init()
    hashes, car = pack_cell(ipfs_a, cell_dir, workdir=tmp_path)
    assert hashes.cell_hash.digest.startswith("bafy")     # CIDv1 dag-cbor manifest
    assert str(hashes.cell_hash).startswith("dsm:ipfs:")  # self-describing string form
    # both addresses are deterministic
    assert cell_hashes(ipfs_a, cell_dir) == hashes
    assert nucleus_hash(ipfs_a, cell_dir) == hashes.nucleus_hash

    # peer B reconstructs it from the CAR alone
    ipfs_b = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "peerB")
    into = tmp_path / "unpacked"
    names = unpack_cell(ipfs_b, car, hashes.cell_hash, into)

    expected = {n for n in nucleus_names(cell_dir) if (cell_dir / n).exists()} | {
        p.relative_to(cell_dir).as_posix() for p in membrane_files(cell_dir)
    }
    assert set(names) == expected
    # the cell's files round-trip byte-for-byte
    for name in names:
        assert (into / name).read_bytes() == (cell_dir / name).read_bytes()
