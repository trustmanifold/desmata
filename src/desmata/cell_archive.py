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
from desmata.content import Hash
from desmata.exceptions import UnknownBackendException
from desmata.higher_protocols import CellFactory
from desmata.interface import NUCLEUS, Cell
from desmata.lower_protocols import CellHash, NucleusHash

# NUCLEUS (the stable, defining files, imported from interface) is hashed and
# shared; the membrane is everything else in the cell directory (config/glue you
# fork). The split is enforced: the nucleus hash is invariant to membrane
# changes, and a directory missing any nucleus file is not a cell.
_MEMBRANE_IGNORE = {"__init__.py"}  # packaging noise, not config


class InvalidCell(ValueError):
    """A directory that is not a valid cell (e.g. missing nucleus files)."""


def nucleus_files(cell_dir: Path) -> list[Path]:
    """The nucleus files that exist under ``cell_dir``, in a deterministic order."""
    return [cell_dir / name for name in NUCLEUS if (cell_dir / name).exists()]


def membrane_files(cell_dir: Path) -> list[Path]:
    """The membrane files: everything in ``cell_dir`` that isn't the nucleus (or
    packaging noise), sorted. These are the local, forkable parts of a cell."""
    cell_dir = Path(cell_dir)
    return sorted(
        p
        for p in cell_dir.iterdir()
        if p.is_file()
        and p.name not in NUCLEUS
        and p.name not in _MEMBRANE_IGNORE
        and not p.name.endswith(".pyc")
    )


def require_nucleus(cell_dir: Path) -> None:
    """Raise :class:`InvalidCell` unless ``cell_dir`` has every nucleus file."""
    missing = [n for n in NUCLEUS if not (Path(cell_dir) / n).exists()]
    if missing:
        raise InvalidCell(
            f"{cell_dir} is not a cell: missing nucleus file(s) {missing}"
        )


def _manifest(ipfs: Tools.IPFS, key: str, files: list[Path]) -> tuple[str, dict]:
    entries = [{"name": f.name, "blob": {"/": ipfs.add(f)}} for f in files]
    manifest = {key: entries}
    return ipfs.dag_put(manifest), manifest


def _nucleus_manifest(ipfs: Tools.IPFS, cell_dir: Path) -> tuple[str, dict]:
    """Add each nucleus file to ``ipfs`` and build an IPLD manifest linking them;
    return ``(manifest_cid, manifest)``. The CID is the cell's nucleus hash."""
    require_nucleus(cell_dir)
    return _manifest(ipfs, "nucleus", nucleus_files(Path(cell_dir)))


def nucleus_hash(ipfs: Tools.IPFS, cell_dir: Path) -> NucleusHash:
    """The content address of a cell's nucleus (deterministic, and invariant to
    membrane changes -- forking the config doesn't change it)."""
    cid, _ = _nucleus_manifest(ipfs, Path(cell_dir))
    return NucleusHash(Hash(backend=ipfs.backend, digest=cid))


def cell_hash(ipfs: Tools.IPFS, cell_dir: Path) -> CellHash:
    """The content address of a *whole* cell: nucleus + membrane. Two cells with
    the same nucleus but different membranes share a :func:`nucleus_hash` but
    differ here -- which is how you find sibling cells and tell them apart."""
    require_nucleus(cell_dir)
    files = nucleus_files(Path(cell_dir)) + membrane_files(cell_dir)
    cid, _ = _manifest(ipfs, "cell", files)
    return CellHash(Hash(backend=ipfs.backend, digest=cid))


def _require_handled(ipfs: Tools.IPFS, hash: Hash) -> None:
    if not ipfs.can_handle(hash):
        raise UnknownBackendException(f"this {ipfs.backend} repo cannot resolve {hash}")


def pack_cell(
    ipfs: Tools.IPFS, cell_dir: Path, *, workdir: Path
) -> tuple[NucleusHash, Path]:
    """Package a cell's nucleus into a CAR. Returns ``(nucleus_hash, car_path)``;
    ``dag export`` of the manifest pulls in every nucleus file's blocks."""
    cid, _ = _nucleus_manifest(ipfs, Path(cell_dir))
    car = workdir / f"{cid}.car"
    ipfs.dag_export(cid, dest=car)
    return NucleusHash(Hash(backend=ipfs.backend, digest=cid)), car


def unpack_cell(
    ipfs: Tools.IPFS, car: Path, hash: Hash, into: Path
) -> list[str]:
    """Reconstruct a cell's nucleus from a CAR into ``into``. Returns the file
    names written (the inverse of :func:`pack_cell`)."""
    _require_handled(ipfs, hash)
    ipfs.dag_import(car)
    manifest = ipfs.dag_get(hash.digest)
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
    hash: Hash,
    car: Path,
    *,
    into: Path,
) -> Cell:
    """Reconstruct a cell from its nucleus hash and run it: unpack the nucleus,
    load its Cell class, and build it via the factory (which builds the cell's
    managed dependencies from the unpacked flake).

    This is the "call a cell by its hash" loop, locally: the ``car`` is the
    nucleus bundle (produced by :func:`pack_cell`); fetching it from a peer by
    hash reuses the ipfs/ssh transport. The README's eventual
    ``from_hash("dsm:ipfs:Qm…", interface=...)`` is this with discovery + an
    interface check on top."""
    unpack_cell(ipfs, car, hash, into)
    require_nucleus(into)  # a reconstructed bundle must be a valid cell
    cell_class = load_cell_class(Path(into))
    return factory.get(cell_class)


def publish_cell(ipfs: Tools.IPFS, cell_dir: Path) -> NucleusHash:
    """Store a cell's nucleus in ``ipfs`` (so peers can fetch it by hash) and
    return its content address -- the hash a peer passes to :func:`from_peer`."""
    return nucleus_hash(ipfs, Path(cell_dir))


def from_peer(
    peer_ipfs: Tools.IPFS,
    ipfs: Tools.IPFS,
    factory: CellFactory,
    hash: Hash,
    *,
    into: Path,
    workdir: Path,
) -> Cell:
    """Fetch a cell from a peer **by its hash alone** and run it: the peer serves
    the nucleus bundle for ``hash`` (``peer_ipfs``), this node imports it, then
    reconstructs and builds the cell. No prior copy of the cell is needed -- only
    the hash and a reference to a peer that has it.

    Here ``peer_ipfs`` is the peer's ipfs repo (two repos on one host in tests);
    over a network it is the same call against a remote/ssh-wrapped ipfs."""
    _require_handled(peer_ipfs, hash)
    car = Path(workdir) / f"{hash.digest}.car"
    peer_ipfs.dag_export(hash.digest, dest=car)
    return from_hash(ipfs, factory, hash, car, into=into)
