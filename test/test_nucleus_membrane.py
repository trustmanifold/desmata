"""The nucleus/membrane boundary, enforced and doing its job
(agent_primers/nucleus-membrane.md).

A cell's nucleus (the core flake.nix/flake.lock/cell.py plus whatever the
`nucleus` declaration file adds) is hashed and shared; the membrane (everything
else -- the small, auditable, forkable part) travels with the cell. We enforce:
- the nucleus hash is *invariant* to membrane changes (fork the membrane freely),
- the cell hash *does* change with the membrane (so sibling cells differ),
- the cell hash structurally commits to the nucleus hash (verify_has_nucleus),
- the author -- not a filename list -- decides where the boundary sits,
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
    cell_hashes,
    membrane_files,
    nucleus_hash,
    nucleus_names,
    pack_cell,
    require_nucleus,
    unpack_cell,
    verify_has_nucleus,
)

from conftest import make_ipfs


def make_cell(cell_dir: Path, *, cell_py: str = "# cell", files: dict[str, str] = {}):
    """A minimal cell directory: the mandatory core plus ``files`` (relative
    path -> content). Hashing never builds, so stub contents are enough."""
    cell_dir.mkdir(parents=True, exist_ok=True)
    (cell_dir / "flake.nix").write_text("{ }\n")
    (cell_dir / "flake.lock").write_text("{}\n")
    (cell_dir / "cell.py").write_text(cell_py)
    for name, content in files.items():
        path = cell_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


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

    # add/modify a membrane file (config) -- the forkable part; build residue
    # (cargo's target/) stays out of the membrane entirely
    (cell_dir / "config.txt").write_text("greeting = howdy\n")
    (cell_dir / "target" / "release").mkdir(parents=True)
    (cell_dir / "target" / "release" / "junk.bin").write_bytes(b"\x00")
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


def test_siblings_share_nucleus_hash_but_not_cell_hash(
    builtins: DesmataBuiltins, tmp_path: Path
):
    ipfs = builtins.ipfs
    ipfs.init()
    make_cell(tmp_path / "a", files={"pipelines.toml": "sum = 'math sum'\n"})
    make_cell(tmp_path / "b", files={"pipelines.toml": "avg = 'math avg'\n"})
    a = cell_hashes(ipfs, tmp_path / "a")
    b = cell_hashes(ipfs, tmp_path / "b")

    # forking the membrane doesn't spoil the shared nucleus
    assert a.nucleus_hash == b.nucleus_hash
    assert a.cell_hash != b.cell_hash

    # inclusion is structural: one dag get, no re-hashing of files. This is the
    # checkable fact that lets trust in both cells stack onto their nucleus.
    assert verify_has_nucleus(ipfs, a.cell_hash, a.nucleus_hash)
    assert verify_has_nucleus(ipfs, b.cell_hash, a.nucleus_hash)

    # and it can't be asserted into being: a different nucleus doesn't verify
    make_cell(tmp_path / "c", cell_py="# a different nucleus")
    c = cell_hashes(ipfs, tmp_path / "c")
    assert not verify_has_nucleus(ipfs, a.cell_hash, c.nucleus_hash)


def test_membrane_travels_with_the_cell(builtins: DesmataBuiltins, tmp_path: Path):
    ipfs_a = builtins.ipfs
    ipfs_a.init()
    cell_dir = tmp_path / "cell"
    make_cell(
        cell_dir,
        files={
            "pipelines.toml": "first = 'get 0'\n",
            "lib/notes.txt": "membranes have subdirectories now\n",
        },
    )
    hashes, car = pack_cell(ipfs_a, cell_dir, workdir=tmp_path)

    ipfs_b = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "peerB")
    into = tmp_path / "unpacked"
    names = unpack_cell(ipfs_b, car, hashes.cell_hash, into)

    assert "pipelines.toml" in names and "lib/notes.txt" in names
    for name in names:
        assert (into / name).read_bytes() == (cell_dir / name).read_bytes()


def test_nucleus_only_bundle_is_the_degenerate_cell(
    builtins: DesmataBuiltins, tmp_path: Path
):
    ipfs_a = builtins.ipfs
    ipfs_a.init()
    cell_dir = tmp_path / "cell"
    make_cell(cell_dir, files={"pipelines.toml": "local only\n"})
    hashes = cell_hashes(ipfs_a, cell_dir)

    # share just the stable core: export the nucleus manifest, not the cell
    car = tmp_path / "nucleus.car"
    ipfs_a.dag_export(hashes.nucleus_hash.digest, dest=car)

    ipfs_b = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "peerB")
    names = unpack_cell(ipfs_b, car, hashes.nucleus_hash, tmp_path / "unpacked")
    assert set(names) == {"flake.nix", "flake.lock", "cell.py"}  # empty membrane


def test_declared_nucleus_widens_the_boundary(
    builtins: DesmataBuiltins, tmp_path: Path
):
    ipfs = builtins.ipfs
    ipfs.init()
    # two cells differing only in lib/engine.nu, which the author declares nucleus
    declaration = {"nucleus": "lib/engine.nu\n"}
    make_cell(
        tmp_path / "a",
        files={**declaration, "lib/engine.nu": "def widely-trusted [] {1}\n"},
    )
    make_cell(
        tmp_path / "b",
        files={**declaration, "lib/engine.nu": "def widely-trusted [] {2}\n"},
    )

    assert "lib/engine.nu" in nucleus_names(tmp_path / "a")
    assert not any(
        p.name == "engine.nu" for p in membrane_files(tmp_path / "a")
    )  # declared files leave the membrane

    a = cell_hashes(ipfs, tmp_path / "a")
    b = cell_hashes(ipfs, tmp_path / "b")
    # the declared file is inside the trusted boundary: changing it changes
    # the nucleus hash (whereas as membrane it would not have)
    assert a.nucleus_hash != b.nucleus_hash

    # a declared-but-missing file breaks the cell
    (tmp_path / "a" / "lib" / "engine.nu").unlink()
    with pytest.raises(InvalidCell):
        require_nucleus(tmp_path / "a")
