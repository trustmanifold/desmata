"""Bootstrapping: verifying desmata's footing before it does anything useful.

Desmata rests on a small set of *trusted* tools the user is expected to have
installed (nix, git, and desmata itself + its python deps). Everything else is
*managed*: built and content-addressed by desmata. The builtin cell -- which
wraps ipfs/kubo -- is the first managed thing, and the bootstrap for everything
that follows, because ipfs is how desmata content-addresses and (eventually)
moves things between peers.

This module holds the logic behind two ``dsm`` commands:

* ``dsm check``     -- verify the trusted tools conform to desmata's interface.
* ``dsm bootstrap`` -- verify the builtin cell can be built and used.

Reframed goal for ``bootstrap``: it should acquire the builtin cell from the
*internet* when no peers are available, and from *peers* when no internet is
available. Today only the internet path exists (nix builds kubo from its
substituters); the peer path is the next milestone and is marked explicitly
below so the seam is real rather than implied.
"""

import os
import shutil
from dataclasses import dataclass
from enum import StrEnum, auto as enum_auto
from pathlib import Path

from xdg_base_dirs import xdg_data_home

import desmata.builtins.cell as _builtins_cell
from desmata.builtins.cell import BuiltinsClosure, Deps, DesmataBuiltins
from desmata.cell_factory import BasicContext, DefaultCellFactory
from desmata.consts import desmata
from desmata.db import LocalSqlite
from desmata.fs import DesmataFiles
from desmata.git import Git
from desmata.higher_protocols import CellFactory
from desmata.lower_protocols import Loggers, UserspaceFiles
from desmata.nix import Nix
from desmata.ssh import Ssh


# --- trusted-tool checks ---------------------------------------------------

@dataclass
class ToolCheck:
    name: str
    ok: bool
    detail: str


def check_trusted_tools(loggers: Loggers) -> list[ToolCheck]:
    """Verify each trusted tool is installed and conforms to its interface.

    Returns one ``ToolCheck`` per tool; ``all(c.ok for c in result)`` is the
    overall verdict.
    """
    log = loggers.proc
    results: list[ToolCheck] = []
    tools = [
        ("nix", Nix(cwd=Path.cwd(), log=log)),
        ("git", Git(log=log)),
        ("ssh", Ssh(log=log)),
    ]
    for name, tool in tools:
        try:
            tool.check()
            version = ".".join(str(part) for part in tool.version)
            results.append(ToolCheck(name=name, ok=True, detail=f"version {version}"))
        except Exception as e:  # missing, too old, or otherwise non-conforming
            results.append(ToolCheck(name=name, ok=False, detail=str(e)))
    return results


# --- production dependency injection ---------------------------------------

def userspace(loggers: Loggers, *, root: Path | None = None) -> DesmataFiles:
    """Where desmata keeps its state. Defaults to the XDG dirs; ``root``
    sandboxes everything under one directory (used by tests)."""
    if root is not None:
        return DesmataFiles.sandbox(root, log=loggers)
    data = xdg_data_home() / desmata
    return DesmataFiles(
        log=loggers,
        deps_by_id=data / "deps" / "by_id",
        deps_by_hash=data / "deps" / "by_hash",
    )


def cell_factory(loggers: Loggers, *, root: Path | None = None) -> DefaultCellFactory:
    files = userspace(loggers, root=root)
    db_factory = LocalSqlite(log=loggers, userspace=files)
    return DefaultCellFactory(log=loggers, userspace=files, db_factory=db_factory)


# --- cleaning managed state ------------------------------------------------

def _force_rmtree(path: Path) -> None:
    """Remove a tree that may contain read-only entries copied/linked from the
    nix store.

    Directories internalized from the nix store can lack write permission, which
    blocks ``shutil.rmtree``. We make directories writable first -- but never
    files: an internalized file may be a hard link into the nix store, and
    chmod-ing it would alter the store inode. Unlinking such a hard link is safe
    (it only drops desmata's reference; the store copy is untouched), and that
    only needs the parent directory to be writable.
    """
    for root, _dirs, _files in os.walk(path):
        os.chmod(root, 0o700)
    shutil.rmtree(path)


@dataclass
class CellInfo:
    name: str
    home: Path
    size_bytes: int  # total size of the home directory's contents


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                pass
    return total


def list_cells(files: UserspaceFiles) -> list[CellInfo]:
    """The cells desmata has created local state for.

    Each cell gets a home directory under ``<data>/cells/<name>/home`` the first
    time it is built; that home holds whatever the cell stores at runtime (for
    the builtin cell, the ipfs repo and its keys). The reported size is the
    total size of that home.
    """
    cells_dir = files.data / "cells"
    if not cells_dir.exists():
        return []
    cells: list[CellInfo] = []
    for entry in sorted(cells_dir.iterdir()):
        if entry.is_dir():
            home = entry / "home"
            size = _dir_size(home) if home.exists() else 0
            cells.append(CellInfo(name=entry.name, home=home, size_bytes=size))
    return cells


def clean_cell_home(
    loggers: Loggers, name: str, *, root: Path | None = None
) -> Path | None:
    """Clear one cell's home directory, resetting its runtime state.

    Returns the directory removed, or None if it had no state. Cleaning a cell's
    home works for any cell type -- there's nothing ipfs-specific about it.
    """
    files = userspace(loggers, root=root)
    home = files.data / "cells" / name / "home"
    if home.exists():
        _force_rmtree(home)
        return home
    return None


def clean_all_cell_homes(loggers: Loggers, *, root: Path | None = None) -> list[Path]:
    """Clear every cell's home directory. Returns the directories removed."""
    files = userspace(loggers, root=root)
    removed: list[Path] = []
    for cell in list_cells(files):
        if cell.home.exists():
            _force_rmtree(cell.home)
            removed.append(cell.home)
    return removed


# --- bootstrap -------------------------------------------------------------

class BootstrapSource(StrEnum):
    auto = enum_auto()       # peers if no internet, internet otherwise (peer path TODO)
    internet = enum_auto()   # build/fetch via nix substituters
    peer = enum_auto()       # assemble the cell from a peer (not yet implemented)


@dataclass
class BootstrapResult:
    source: BootstrapSource
    cell_local_name: str
    ipfs_dep_id: str
    ipfs_dep_hash: str
    probe_cid: str


def _probe(workdir: Path, ipfs) -> str:
    probe = workdir / "desmata-bootstrap-probe"
    probe.write_text("desmata")
    return ipfs.get_hash(probe)


def construct_builtins_from_path(
    factory: DefaultCellFactory, ipfs_path: str
) -> DesmataBuiltins:
    """Build the builtin cell from an ipfs store path that is already present
    locally -- without evaluating or building the flake. This is the eval-free
    path a peer-bootstrapped node takes: it has received ipfs's closure, so it
    just wraps it."""
    cell_dir = Path(_builtins_cell.__file__).parent
    context = BasicContext(
        name="builtins",
        cell_dir=cell_dir,
        userspace=factory.userspace,
        loggers=factory.log,
    )
    # hash=None: the closure arrived by store path; its content address is
    # computed once the wrapped ipfs is usable (it is desmata's hasher).
    ipfs_dep = Deps.IPFS(
        id=Nix.get_id(Path(ipfs_path)),
        hash=None,
        root=Path(ipfs_path),
        immediate_dependencies={},
    )
    closure = BuiltinsClosure(local_name="builtins", ipfs=ipfs_dep)
    cell = DesmataBuiltins(closure, context)
    # the internet path inits the ipfs repo while building; the peer path didn't
    # build, so init here -- `ipfs add --only-hash` needs a repo (idempotent)
    cell.ipfs.init()
    return cell


def _bootstrap_from_peer(
    factory: DefaultCellFactory, *, workdir: Path, from_url: str, ipfs_path: str
) -> BootstrapResult:
    """Acquire ipfs's closure from a peer (over the trusted tools -- no ipfs),
    then construct and use the builtin cell without rebuilding it."""
    nix = Nix(cwd=Path(_builtins_cell.__file__).parent, log=factory.log.proc)
    # the chicken-and-egg breaker: nix moves the closure, no ipfs required
    nix("copy", "--from", from_url, "--no-check-sigs", ipfs_path)

    builtins = construct_builtins_from_path(factory, ipfs_path)
    return BootstrapResult(
        source=BootstrapSource.peer,
        cell_local_name=builtins.closure.local_name,
        ipfs_dep_id=builtins.closure.ipfs.id,
        ipfs_dep_hash="(acquired from peer)",
        probe_cid=_probe(workdir, builtins.ipfs),
    )


def bootstrap_builtins(
    factory: CellFactory,
    *,
    workdir: Path,
    source: BootstrapSource = BootstrapSource.auto,
    from_url: str | None = None,
    ipfs_path: str | None = None,
) -> BootstrapResult:
    """Build the builtin cell and prove it works by hashing a probe with ipfs.

    The probe round-trips desmata's reason for managing ipfs at all: turning
    bytes into a content address.

    With ``source=peer`` the cell is acquired from a peer instead of built:
    ``from_url`` is the peer's nix store URL (e.g. ``ssh://peer@host``) and
    ``ipfs_path`` the store path to fetch. (Automatic discovery of that path will
    come with cell manifests; for now the caller supplies it.)
    """
    if source is BootstrapSource.peer:
        if not (from_url and ipfs_path):
            raise ValueError(
                "peer bootstrap needs from_url (the peer's nix store URL) and "
                "ipfs_path (the store path to fetch)"
            )
        return _bootstrap_from_peer(
            factory, workdir=workdir, from_url=from_url, ipfs_path=ipfs_path
        )
    # TODO: when source is `auto`, detect connectivity and fall back to peers
    # when the internet is unavailable. For now `auto` always uses the internet.
    resolved = BootstrapSource.internet

    builtins = factory.get(DesmataBuiltins)
    probe_cid = _probe(workdir, builtins.ipfs)

    ipfs = builtins.closure.ipfs
    return BootstrapResult(
        source=resolved,
        cell_local_name=builtins.closure.local_name,
        ipfs_dep_id=ipfs.id,
        ipfs_dep_hash=str(ipfs.hash),
        probe_cid=probe_cid,
    )
