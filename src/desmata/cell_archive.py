"""Content-addressing a cell: hash, pack, and unpack its nucleus.

A cell's **nucleus** is its stable, defining files (``flake.nix``, ``flake.lock``,
``cell.py``) -- the recipe a peer trusts and the thing that should change rarely.
We content-address the nucleus so a cell can be referred to, and fetched, **by
hash** -- the foundation for the aspirational ``from_hash`` API.

``pack_cell`` bundles the nucleus into a CAR (reusing the same IPLD-manifest shape
as the dependency transport in ``transport.py``); ``unpack_cell`` reconstructs the
nucleus files on another peer. The nucleus hash is the manifest's CID, so it is
deterministic: the same nucleus always addresses the same way.

This is the storage half of "call a cell by its hash"; loading and running the
reconstructed cell is the next slice (``from_hash``).
"""

import importlib.util
import inspect as _inspect
import sys
from pathlib import Path

from desmata.builtins.cell import Tools
from desmata.higher_protocols import CellFactory
from desmata.interface import Cell

# The stable files that define a cell (see the README's nucleus/membrane split).
NUCLEUS = ("flake.nix", "flake.lock", "cell.py")


def nucleus_files(cell_dir: Path) -> list[Path]:
    """The nucleus files that exist under ``cell_dir``, in a deterministic order."""
    return [cell_dir / name for name in NUCLEUS if (cell_dir / name).exists()]


def _nucleus_manifest(ipfs: Tools.IPFS, cell_dir: Path) -> tuple[str, dict]:
    """Add each nucleus file to ``ipfs`` and build an IPLD manifest linking them;
    return ``(manifest_cid, manifest)``. The CID is the cell's nucleus hash."""
    entries = [
        {"name": f.name, "blob": {"/": ipfs.add(f)}}
        for f in nucleus_files(Path(cell_dir))
    ]
    manifest = {"nucleus": entries}
    return ipfs.dag_put(manifest), manifest


def nucleus_hash(ipfs: Tools.IPFS, cell_dir: Path) -> str:
    """The content address of a cell's nucleus (deterministic)."""
    cid, _ = _nucleus_manifest(ipfs, Path(cell_dir))
    return cid


def pack_cell(
    ipfs: Tools.IPFS, cell_dir: Path, *, workdir: Path
) -> tuple[str, Path]:
    """Package a cell's nucleus into a CAR. Returns ``(nucleus_cid, car_path)``;
    ``dag export`` of the manifest pulls in every nucleus file's blocks."""
    cid, _ = _nucleus_manifest(ipfs, Path(cell_dir))
    car = workdir / f"{cid}.car"
    ipfs.dag_export(cid, dest=car)
    return cid, car


def unpack_cell(
    ipfs: Tools.IPFS, car: Path, cid: str, into: Path
) -> list[str]:
    """Reconstruct a cell's nucleus from a CAR into ``into``. Returns the file
    names written (the inverse of :func:`pack_cell`)."""
    ipfs.dag_import(car)
    manifest = ipfs.dag_get(cid)
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for entry in manifest["nucleus"]:
        ipfs.get(entry["blob"]["/"], into / entry["name"])
        names.append(entry["name"])
    return names


# --- loading a reconstructed cell ------------------------------------------

def load_cell_class(cell_dir: Path) -> type[Cell]:
    """Import the ``cell.py`` under ``cell_dir`` and return the Cell subclass it
    defines. The module is registered in ``sys.modules`` so the cell factory can
    re-import it (it resolves the cell's flake dir from the module file)."""
    cell_py = Path(cell_dir) / "cell.py"
    name = f"desmata_cell_{abs(hash(str(cell_py.resolve())))}"
    spec = importlib.util.spec_from_file_location(name, cell_py)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    for _, obj in _inspect.getmembers(module, _inspect.isclass):
        if (
            issubclass(obj, Cell)
            and obj is not Cell
            and obj.__module__ == module.__name__
        ):
            return obj
    raise ValueError(f"no Cell subclass defined in {cell_py}")


def from_hash(
    ipfs: Tools.IPFS,
    factory: CellFactory,
    cid: str,
    car: Path,
    *,
    into: Path,
) -> Cell:
    """Reconstruct a cell from its nucleus hash and run it: unpack the nucleus,
    load its Cell class, and build it via the factory (which builds the cell's
    managed dependencies from the unpacked flake).

    This is the "call a cell by its hash" loop, locally: the ``car`` is the
    nucleus bundle (produced by :func:`pack_cell`); fetching it from a peer by
    ``cid`` reuses the ipfs/ssh transport. The README's eventual
    ``from_hash("Qm…", interface=...)`` is this with discovery + an interface
    check on top."""
    unpack_cell(ipfs, car, cid, into)
    cell_class = load_cell_class(Path(into))
    return factory.get(cell_class)
