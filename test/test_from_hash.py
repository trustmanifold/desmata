"""The headline loop: address a cell by its (nucleus) hash, reconstruct it from a
bundle, and *run* it. Packs the greeter cell, then `from_hash`-es it into a fresh
location and builds + runs the reconstructed cell -- proving a cell can travel by
hash and still work.
"""

from pathlib import Path

import desmata.samples.greeter.cell as greeter
from desmata.builtins.cell import DesmataBuiltins
from desmata.cell_archive import pack_cell
from desmata.get import from_hash  # the README's public entry point
from desmata.higher_protocols import CellFactory
from desmata.interface import Cell
from injector import Injector


def test_from_hash_reconstructs_and_runs_a_cell(
    builtins: DesmataBuiltins, components: Injector, tmp_path: Path
):
    ipfs = builtins.ipfs
    ipfs.init()

    # publish: the whole greeter cell, content-addressed
    hashes, car = pack_cell(ipfs, Path(greeter.__file__).parent, workdir=tmp_path)

    # resolve by hash into a fresh location, with no reference to the original
    factory = components.get(CellFactory)
    cell = from_hash(ipfs, factory, hashes.cell_hash, car, into=tmp_path / "fetched")

    assert isinstance(cell, Cell)
    assert cell.closure.local_name == "fetched"  # built from the unpacked dir
    # the reconstructed cell actually runs its tool
    assert "moo-from-hash" in cell.greet("moo-from-hash")
