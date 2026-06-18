"""The nucleus/membrane boundary, enforced.

A cell's nucleus (flake.nix, flake.lock, cell.py) is hashed and shared; the
membrane (other files -- config/glue) is the local, forkable part. We enforce:
- the nucleus hash is *invariant* to membrane changes (fork the config freely),
- the cell hash *does* change with the membrane (so sibling cells differ),
- a directory missing nucleus files is not a cell.
"""

import shutil
from pathlib import Path

import pytest

import desmata.samples.greeter.cell as greeter
from desmata.builtins.cell import DesmataBuiltins
from desmata.cell_archive import (
    InvalidCell,
    cell_hash,
    membrane_files,
    nucleus_hash,
    require_nucleus,
)


def _greeter_copy(dst: Path) -> Path:
    src = Path(greeter.__file__).parent
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("flake.nix", "flake.lock", "cell.py"):
        shutil.copy(src / name, dst / name)
    return dst


def test_nucleus_hash_is_invariant_to_membrane(
    builtins: DesmataBuiltins, tmp_path: Path
):
    ipfs = builtins.ipfs
    ipfs.init()
    cell_dir = _greeter_copy(tmp_path / "cell")

    nuc_before = nucleus_hash(ipfs, cell_dir)
    cell_before = cell_hash(ipfs, cell_dir)

    # add/modify a membrane file (config) -- the forkable part
    (cell_dir / "config.txt").write_text("greeting = howdy\n")
    assert [p.name for p in membrane_files(cell_dir)] == ["config.txt"]

    # nucleus hash unchanged; cell hash changed
    assert nucleus_hash(ipfs, cell_dir) == nuc_before
    assert cell_hash(ipfs, cell_dir) != cell_before

    # change the membrane again -> nucleus still stable, cell hash moves again
    (cell_dir / "config.txt").write_text("greeting = hi\n")
    assert nucleus_hash(ipfs, cell_dir) == nuc_before
    assert cell_hash(ipfs, cell_dir) != cell_before


def test_missing_nucleus_is_not_a_cell(builtins: DesmataBuiltins, tmp_path: Path):
    ipfs = builtins.ipfs
    ipfs.init()
    cell_dir = _greeter_copy(tmp_path / "cell")
    (cell_dir / "flake.nix").unlink()  # break the nucleus

    with pytest.raises(InvalidCell):
        require_nucleus(cell_dir)
    with pytest.raises(InvalidCell):
        nucleus_hash(ipfs, cell_dir)
