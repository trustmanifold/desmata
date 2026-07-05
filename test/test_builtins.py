"""Thorough exercise of the builtins cell.

The builtins cell wraps desmata's own non-python dependencies (ipfs/kubo and
git) built through nix. These tests assert the things the rest of the system
will rely on: the dependencies resolve to real, runnable nix store paths; the
ipfs hasher is deterministic and content-addressed; every dependency in the
closure's DAG gets internalized under desmata's control; and cell tools run in
an environment isolated from the host.

The ``builtins`` and ``session_components`` fixtures live in conftest.py and are
session-scoped because constructing the cell is expensive.
"""

import os
from pathlib import Path

from injector import Injector

from desmata.builtins.cell import Deps, DesmataBuiltins
from desmata.interface import Dependency
from desmata.lower_protocols import UserspaceFiles

# ipfs CIDs are content-addressed, so these are fixed forever.
BAR_CID = "QmW3J3czdUzxRaaN31Gtu5T1U5br3t631b8AHdvxHdsHWg"


def test_cell_identity(builtins: DesmataBuiltins):
    assert isinstance(builtins, DesmataBuiltins)
    assert builtins.closure.local_name == "builtins"
    # ipfs is the only managed builtin dependency (nix/git are trusted bootstrap
    # tools, verified by their interfaces in test_bootstrap_tools.py)
    assert isinstance(builtins.closure.ipfs, Deps.IPFS)


def test_ipfs_dependency_resolves_to_real_store_path(builtins: DesmataBuiltins):
    ipfs = builtins.closure.ipfs
    root = Path(ipfs.root)
    assert root.parts[:3] == ("/", "nix", "store")
    assert root.is_dir()
    assert (root / "bin" / "ipfs").exists()
    # id is derived from the store path; hash is an ipfs CID
    assert ipfs.id == Dependency.get_id(root)
    assert ipfs.hash.digest.startswith("Qm")
    assert str(ipfs.hash).startswith("dsm:ipfs:")


def test_ipfs_hash_is_deterministic(builtins: DesmataBuiltins, tmp_path: Path):
    f = tmp_path / "foo"
    f.write_text("bar")
    first = builtins.ipfs.get_hash(f)
    second = builtins.ipfs.get_hash(f)
    assert first == second == BAR_CID


def test_ipfs_hash_is_content_sensitive(builtins: DesmataBuiltins, tmp_path: Path):
    a = tmp_path / "a"
    a.write_text("bar")
    b = tmp_path / "b"
    b.write_text("baz")
    assert builtins.ipfs.get_hash(a) != builtins.ipfs.get_hash(b)


def test_ipfs_hashes_directories_recursively(builtins: DesmataBuiltins, tmp_path: Path):
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "one").write_text("uno")
    (tree / "sub" / "two").write_text("dos")
    h = builtins.ipfs.get_hash(tree)
    assert h.startswith("Qm")
    # the same tree always hashes the same way
    assert h == builtins.ipfs.get_hash(tree)


def test_dependencies_are_internalized(
    builtins: DesmataBuiltins, session_components: Injector
):
    userspace = session_components.get(UserspaceFiles)
    assert userspace.deps_by_id.is_dir()
    assert userspace.deps_by_hash.is_dir()
    assert any(userspace.deps_by_id.iterdir())
    # the ipfs dependency itself is internalized under its id
    assert (userspace.deps_by_id / builtins.closure.ipfs.id).exists()


def test_dependency_dag_is_consistent(
    builtins: DesmataBuiltins, session_components: Injector
):
    userspace = session_components.get(UserspaceFiles)
    ipfs = builtins.closure.ipfs

    # kubo is not a leaf; it pulls in transitive dependencies
    assert ipfs.immediate_dependencies

    seen: set[str] = set()
    leaves = 0

    def walk(dep: Dependency) -> None:
        nonlocal leaves
        # every node in the dag has been internalized by id
        assert (userspace.deps_by_id / dep.id).exists()
        if not dep.immediate_dependencies:
            leaves += 1
        for child_id, child in dep.immediate_dependencies.items():
            assert isinstance(child, Dependency)
            assert child.id == child_id, "dag key should match the dependency's id"
            if child.id not in seen:
                seen.add(child.id)
                walk(child)

    seen.add(ipfs.id)
    walk(ipfs)
    # a real closure bottoms out in at least one dependency with no children
    assert leaves >= 1


def test_tool_env_is_isolated_from_host(
    builtins: DesmataBuiltins, session_components: Injector
):
    userspace = session_components.get(UserspaceFiles)
    context = builtins.context
    env = builtins.ipfs.env_filter(dict(os.environ))

    assert env["USER"] == "desmata-user"
    # the cell runs against its own sandboxed home, not the developer's
    assert env["HOME"] == str(context.home)
    assert env["HOME"] != os.environ.get("HOME")
    assert Path(env["HOME"]).is_relative_to(userspace.data)
