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

from desmata.builtins.cell import DesmataBuiltins
from desmata.cell_factory import DefaultCellFactory
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


def bootstrap_builtins(
    factory: CellFactory,
    *,
    workdir: Path,
    source: BootstrapSource = BootstrapSource.auto,
) -> BootstrapResult:
    """Build the builtin cell and prove it works by hashing a probe with ipfs.

    The probe round-trips desmata's reason for managing ipfs at all: turning
    bytes into a content address.
    """
    if source is BootstrapSource.peer:
        raise NotImplementedError(
            "peer-based bootstrap is not implemented yet; the builtin cell can "
            "currently only be acquired over the internet via nix"
        )
    # TODO: when source is `auto`, detect connectivity and fall back to peers
    # when the internet is unavailable. For now `auto` always uses the internet.
    resolved = BootstrapSource.internet

    builtins = factory.get(DesmataBuiltins)

    probe = workdir / "desmata-bootstrap-probe"
    probe.write_text("desmata")
    probe_cid = builtins.ipfs.get_hash(probe)

    ipfs = builtins.closure.ipfs
    return BootstrapResult(
        source=resolved,
        cell_local_name=builtins.closure.local_name,
        ipfs_dep_id=ipfs.id,
        ipfs_dep_hash=ipfs.hash,
        probe_cid=probe_cid,
    )
