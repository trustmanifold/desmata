"""Content-addressing a cell: hash, pack, and unpack its nucleus and membrane.

A cell's **nucleus** is its stable, defining files -- the widely-trusted part
that should change rarely. The mandatory core is ``flake.nix``, ``flake.lock``,
``cell.py``; an author widens the nucleus with an optional ``nucleus``
declaration file (one relative path per line, ``#`` comments), which is itself
part of the nucleus when present -- the boundary lives inside the hash, so
sibling cells can't disagree about where it sits. The **membrane** is everything
else in the cell directory: the small, forkable, quickly-auditable part.

The two identities and how they relate (see agent_primers/nucleus-membrane.md):

* ``nucleus_hash`` -- the CID of an IPLD manifest linking the nucleus files.
  Invariant to membrane changes: forks that only touch the membrane share it,
  which is how sibling cells find each other and how trust in many different
  cells can stack onto one shared nucleus.
* ``cell_hash`` -- the CID of a manifest that links the membrane files **and
  embeds the nucleus manifest as an IPLD link**. The cell hash therefore
  *structurally commits* to the nucleus hash: "this cell contains that nucleus,
  unchanged" is readable off the manifest (``verify_has_nucleus``), not a claim
  anyone has to take on trust.

``pack_cell`` bundles the whole cell (nucleus + membrane) into a CAR;
``unpack_cell`` reconstructs it on another peer, and also accepts a
nucleus-only bundle (the degenerate empty-membrane cell). ``from_hash`` /
``from_peer`` resolve a cell by either hash and run it.
"""

import importlib.util
import inspect as _inspect
import os
import subprocess
import sys
from pathlib import Path

from desmata.builtins.cell import Tools
from desmata.content import ContentBackend, Hash
from desmata.exceptions import (
    ArtifactPinMismatch,
    CellUnavailable,
    UnknownBackendException,
)
from desmata.higher_protocols import CellFactory
from desmata.interface import NUCLEUS, NUCLEUS_DECLARATION, Cell
from desmata.lower_protocols import CellHash, CellHashes, NucleusHash

# packaging noise, never part of either hash
_IGNORE_FILES = {"__init__.py"}
_IGNORE_DIRS = {"__pycache__"}


class InvalidCell(ValueError):
    """A directory that is not a valid cell (e.g. missing nucleus files)."""


def nucleus_names(cell_dir: Path) -> tuple[str, ...]:
    """The relative paths of ``cell_dir``'s nucleus files: the mandatory core
    plus whatever the ``nucleus`` declaration file adds. Reading the declaration
    is pure data access -- computing a nucleus hash never executes code."""
    cell_dir = Path(cell_dir)
    names = list(NUCLEUS)
    declaration = cell_dir / NUCLEUS_DECLARATION
    if declaration.exists():
        names.append(NUCLEUS_DECLARATION)
        for line in declaration.read_text().splitlines():
            name = line.strip()
            if name and not name.startswith("#"):
                names.append(name)
    return tuple(dict.fromkeys(names))  # declared dupes of core collapse


def nucleus_files(cell_dir: Path) -> list[Path]:
    """The nucleus files that exist under ``cell_dir``, in a deterministic order."""
    cell_dir = Path(cell_dir)
    return [cell_dir / name for name in nucleus_names(cell_dir) if (cell_dir / name).exists()]


def membrane_files(cell_dir: Path) -> list[Path]:
    """The membrane files: everything under ``cell_dir`` that isn't the nucleus,
    walked recursively with deterministic ordering. These are the local,
    forkable parts of a cell -- and they travel with it (``pack_cell``).

    Hidden files/dirs (``.git``, ``.envrc``, ...), ``__pycache__``, ``*.pyc``,
    ``__init__.py``, and symlinks (a stray ``nix build`` ``result`` link) are
    excluded: none of them belong in a content address."""
    cell_dir = Path(cell_dir)
    nucleus = set(nucleus_names(cell_dir))
    found: list[Path] = []
    for root, dirs, files in os.walk(cell_dir, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and d not in _IGNORE_DIRS
        )
        for name in files:
            path = Path(root) / name
            rel = path.relative_to(cell_dir).as_posix()
            if (
                name.startswith(".")
                or name in _IGNORE_FILES
                or name.endswith(".pyc")
                or rel in nucleus
                or path.is_symlink()
            ):
                continue
            found.append(path)
    return sorted(found, key=lambda p: p.relative_to(cell_dir).as_posix())


# --- artifact pins ----------------------------------------------------------
# A lightweight cell pins prebuilt artifacts (wasm components) in its nucleus:
# an `artifact` manifest maps each blob's cell-relative path to its content
# address. The blob itself is membrane (it travels in the CAR like any file);
# the pin is nucleus (the author declares `artifact` in the `nucleus` file), so
# trust in the nucleus structurally extends to the exact bytes of the blob.
# Pins are computed with the same offline hashing as publish/fetch
# (`ipfs add --only-hash`), so they agree with what peers compute.

ARTIFACT_MANIFEST = "artifact"


def artifact_pins(cell_dir: Path) -> dict[str, Hash]:
    """The artifact manifest of the cell at ``cell_dir``: cell-relative blob
    path -> pinned hash. Empty when the cell declares no artifacts. Pure data
    access, like reading the nucleus declaration."""
    manifest = Path(cell_dir) / ARTIFACT_MANIFEST
    if not manifest.exists():
        return {}
    pins: dict[str, Hash] = {}
    for line in manifest.read_text().splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        try:
            name, pin = entry.split()
        except ValueError:
            raise InvalidCell(
                f"{manifest} line {entry!r}: expected '<relative-path>  <hash>'"
            )
        pins[name] = Hash.parse(pin)
    return pins


def verify_artifacts(hasher: ContentBackend, cell_dir: Path) -> dict[str, Hash]:
    """Check every pinned artifact blob under ``cell_dir`` against its pin.

    Returns the verified pins. Raises :class:`ArtifactPinMismatch` if a blob is
    missing or hashes to something else -- publish uses this to refuse to
    spread a lying cell, and runners use it before executing fetched bytes."""
    cell_dir = Path(cell_dir)
    problems: list[str] = []
    pins = artifact_pins(cell_dir)
    for name, pin in pins.items():
        blob = cell_dir / name
        if not blob.exists():
            problems.append(f"{name}: pinned as {pin} but no such file")
            continue
        actual = hasher.hash_path(blob)
        if actual != pin:
            problems.append(f"{name}: pinned as {pin} but hashes to {actual}")
    if problems:
        raise ArtifactPinMismatch(
            f"artifact pin(s) in {cell_dir} do not match their blobs: "
            + "; ".join(problems)
        )
    return pins


def require_nucleus(cell_dir: Path) -> None:
    """Raise :class:`InvalidCell` unless every nucleus file -- core and declared
    -- exists under ``cell_dir``."""
    cell_dir = Path(cell_dir)
    missing = [n for n in nucleus_names(cell_dir) if not (cell_dir / n).exists()]
    if missing:
        raise InvalidCell(
            f"{cell_dir} is not a cell: missing nucleus file(s) {missing}"
        )


def _entries(ipfs: Tools.IPFS, cell_dir: Path, files: list[Path]) -> list[dict]:
    """Add each file to ``ipfs`` and link it under its cell-relative path."""
    return [
        {"name": f.relative_to(cell_dir).as_posix(), "blob": {"/": ipfs.add(f)}}
        for f in files
    ]


def _nucleus_manifest(ipfs: Tools.IPFS, cell_dir: Path) -> str:
    """Store the nucleus manifest; its CID is the cell's nucleus hash."""
    require_nucleus(cell_dir)
    cell_dir = Path(cell_dir)
    return ipfs.dag_put({"nucleus": _entries(ipfs, cell_dir, nucleus_files(cell_dir))})


def _cell_manifest(ipfs: Tools.IPFS, cell_dir: Path) -> tuple[str, str]:
    """Store both manifests; returns ``(cell_cid, nucleus_cid)``. The cell
    manifest embeds the nucleus manifest as an IPLD link, so the cell hash
    structurally commits to the nucleus hash."""
    cell_dir = Path(cell_dir)
    nucleus_cid = _nucleus_manifest(ipfs, cell_dir)
    cell_cid = ipfs.dag_put(
        {
            "cell": {
                "nucleus": {"/": nucleus_cid},
                "membrane": _entries(ipfs, cell_dir, membrane_files(cell_dir)),
            }
        }
    )
    return cell_cid, nucleus_cid


def nucleus_hash(ipfs: Tools.IPFS, cell_dir: Path) -> NucleusHash:
    """The content address of a cell's nucleus (deterministic, and invariant to
    membrane changes -- forking the membrane doesn't change it)."""
    return NucleusHash(
        Hash(backend=ipfs.backend, digest=_nucleus_manifest(ipfs, Path(cell_dir)))
    )


def cell_hash(ipfs: Tools.IPFS, cell_dir: Path) -> CellHash:
    """The content address of a *whole* cell: nucleus + membrane. Two cells with
    the same nucleus but different membranes share a :func:`nucleus_hash` but
    differ here -- which is how you find sibling cells and tell them apart."""
    cid, _ = _cell_manifest(ipfs, Path(cell_dir))
    return CellHash(Hash(backend=ipfs.backend, digest=cid))


def cell_hashes(ipfs: Tools.IPFS, cell_dir: Path) -> CellHashes:
    """Both content addresses of the cell at ``cell_dir``."""
    cell_cid, nucleus_cid = _cell_manifest(ipfs, Path(cell_dir))
    return CellHashes(
        cell_hash=CellHash(Hash(backend=ipfs.backend, digest=cell_cid)),
        nucleus_hash=NucleusHash(Hash(backend=ipfs.backend, digest=nucleus_cid)),
    )


def _require_handled(ipfs: Tools.IPFS, hash: Hash) -> None:
    if not ipfs.can_handle(hash):
        raise UnknownBackendException(f"this {ipfs.backend} repo cannot resolve {hash}")


# how long to look for a cell's blocks on the network before giving up
DEFAULT_FETCH_TIMEOUT = 120.0


def _ensure_available(
    ipfs: Tools.IPFS, hash: Hash, *, fetch_timeout: float = DEFAULT_FETCH_TIMEOUT
) -> None:
    """Make sure every block under ``hash`` is in the local repo.

    Resolution is offline-first: content that is already local resolves with no
    daemon involved. Only on a miss does the network enter the picture, and
    that requires a running daemon (``dsm serve``) -- the daemon finds whichever
    peer has the content and fetches it (pinned, so it stays local).
    """
    if ipfs.have(hash.digest):
        return
    if not ipfs.daemon_running():
        raise CellUnavailable(
            f"{hash} is not in the local repo and no ipfs daemon is running. "
            "Run `dsm serve` (and keep it running) so desmata can fetch it "
            "from peers."
        )
    try:
        ipfs.pin_add(hash.digest, offline=False, timeout=fetch_timeout)
    except subprocess.TimeoutExpired as e:
        raise CellUnavailable(
            f"no peer provided {hash} within {fetch_timeout:.0f}s -- is the "
            "publisher's `dsm serve` running and reachable?"
        ) from e


def verify_has_nucleus(
    ipfs: Tools.IPFS, cell: CellHash | Hash, nucleus: NucleusHash | Hash
) -> bool:
    """Whether the cell addressed by ``cell`` contains -- unchanged -- the
    nucleus addressed by ``nucleus``.

    Because the cell manifest embeds the nucleus manifest as a link, this is a
    single ``dag get``, not a re-hash of any file. It is the checkable fact that
    lets trust in many sibling cells stack onto their shared nucleus (the
    ``has_nucleus`` color in agent_primers/nucleus-membrane.md)."""
    _require_handled(ipfs, cell)
    manifest = ipfs.dag_get(cell.digest)
    try:
        return manifest["cell"]["nucleus"]["/"] == nucleus.digest
    except (KeyError, TypeError):
        return False  # not a cell manifest (e.g. a nucleus-only address)


def pack_cell(
    ipfs: Tools.IPFS, cell_dir: Path, *, workdir: Path
) -> tuple[CellHashes, Path]:
    """Package a whole cell (nucleus + membrane) into a CAR. Returns
    ``(hashes, car_path)``; ``dag export`` of the cell manifest pulls the
    nucleus manifest and every file blob along with it."""
    hashes = cell_hashes(ipfs, Path(cell_dir))
    car = workdir / f"{hashes.cell_hash.digest}.car"
    ipfs.dag_export(hashes.cell_hash.digest, dest=car)
    return hashes, car


def unpack_cell(
    ipfs: Tools.IPFS,
    car: Path | None,
    hash: Hash,
    into: Path,
    *,
    fetch_timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> list[str]:
    """Reconstruct a cell into ``into``; the inverse of :func:`pack_cell`.
    Accepts either a whole-cell bundle (nucleus + membrane) or a nucleus-only
    bundle (the degenerate empty-membrane cell). Returns the relative paths
    written.

    ``car`` is the sneakernet path: import the bundle's blocks, then unpack.
    With ``car=None`` the blocks are resolved by hash instead -- from the local
    repo when present, else from peers via the running daemon
    (:func:`_ensure_available`)."""
    _require_handled(ipfs, hash)
    if car is not None:
        ipfs.dag_import(car)
    else:
        _ensure_available(ipfs, hash, fetch_timeout=fetch_timeout)
    manifest = ipfs.dag_get(hash.digest)

    if "cell" in manifest:
        nucleus = ipfs.dag_get(manifest["cell"]["nucleus"]["/"])["nucleus"]
        entries = nucleus + manifest["cell"]["membrane"]
    else:
        entries = manifest["nucleus"]

    into = Path(into)
    names: list[str] = []
    for entry in entries:
        dest = into / entry["name"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        ipfs.get(entry["blob"]["/"], dest)
        names.append(entry["name"])
    return names


# --- loading a reconstructed cell ------------------------------------------

def load_cell_class(cell_dir: Path) -> type[Cell]:
    """Import the ``cell.py`` under ``cell_dir`` and return the Cell subclass it
    defines. The module is registered in ``sys.modules`` so the cell factory can
    re-import it (it resolves the cell's flake dir from the module file).

    Deliberately nucleus-only: membrane files are never imported here. The
    nucleus decides how (and whether) its membrane is interpreted, which is what
    keeps a fork auditable by reading only its membrane diff."""
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
    hash: Hash | str,
    car: Path | None = None,
    *,
    into: Path,
    fetch_timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> Cell:
    """Resolve a cell by its hash and run it: materialize the bundle, load its
    Cell class, and build it via the factory (which builds the cell's managed
    dependencies from the unpacked flake).

    ``hash`` may be a cell hash (nucleus + membrane arrive) or a nucleus hash
    (the membrane is empty), as a :class:`Hash` or its string form
    (``"dsm:ipfs:..."``). Resolution is offline-first: content already in the
    local repo (published here, fetched before, or imported from the ``car``
    bundle if one is given) needs no daemon; anything else is fetched from
    whichever peer has it, which requires ``dsm serve`` to be running -- both
    here (to fetch) and at the publisher (to provide)."""
    if isinstance(hash, str):
        hash = Hash.parse(hash)
    unpack_cell(ipfs, car, hash, into, fetch_timeout=fetch_timeout)
    require_nucleus(into)  # a reconstructed bundle must be a valid cell
    cell_class = load_cell_class(Path(into))
    return factory.get(cell_class)


def publish_cell(ipfs: Tools.IPFS, cell_dir: Path) -> CellHashes:
    """Store a whole cell in ``ipfs`` (so peers can fetch it by hash) and return
    both of its content addresses. Hand a peer the ``cell_hash`` to share the
    cell as-is (membrane included), or the ``nucleus_hash`` to share just the
    stable core.

    The cell manifest is pinned: published cells must survive GC, and a serving
    daemon (``dsm serve``) announces pinned content to peers.

    Refuses (:class:`ArtifactPinMismatch`) to publish a cell whose artifact
    pins don't match its blobs: a peer would fetch bytes the nucleus disowns."""
    verify_artifacts(ipfs, Path(cell_dir))
    hashes = cell_hashes(ipfs, Path(cell_dir))
    ipfs.pin_add(hashes.cell_hash.digest)
    return hashes


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
    the bundle for ``hash`` (``peer_ipfs``), this node imports it, then
    reconstructs and builds the cell. No prior copy of the cell is needed -- only
    the hash and a reference to a peer that has it.

    Here ``peer_ipfs`` is the peer's ipfs repo (two repos on one host in tests);
    over a network it is the same call against a remote/ssh-wrapped ipfs."""
    _require_handled(peer_ipfs, hash)
    car = Path(workdir) / f"{hash.digest}.car"
    peer_ipfs.dag_export(hash.digest, dest=car)
    return from_hash(ipfs, factory, hash, car, into=into)
