"""Tests for idempotent bootstrap, listing cells, and clearing cell homes.

Bootstrapping creates a home directory for the builtin cell (holding the ipfs
repo). Doing it again must not fail (building any cell re-runs this), and
clearing a cell's home -- a generic operation that works for any cell type --
must reset that state so a fresh bootstrap re-initializes.
"""

from desmata.bootstrap import (
    BootstrapSource,
    bootstrap_builtins,
    cell_factory,
    clean_all_cell_homes,
    clean_cell_home,
    list_cells,
    userspace,
)
from desmata.log import TestLoggers


def test_bootstrap_is_idempotent(tmp_path):
    home = tmp_path / "home"
    factory = cell_factory(TestLoggers(), root=home)
    first = bootstrap_builtins(factory, workdir=tmp_path, source=BootstrapSource.auto)
    # a second bootstrap against the now-initialized home must not error
    second = bootstrap_builtins(factory, workdir=tmp_path, source=BootstrapSource.auto)
    assert first.probe_cid == second.probe_cid


def test_bootstrap_registers_the_builtin_cell(tmp_path):
    loggers = TestLoggers()
    home = tmp_path / "home"
    factory = cell_factory(loggers, root=home)
    bootstrap_builtins(factory, workdir=tmp_path, source=BootstrapSource.auto)

    cells = list_cells(userspace(loggers, root=home))
    by_name = {c.name: c for c in cells}
    assert "builtins" in by_name
    assert by_name["builtins"].size_bytes > 0


def test_clean_cell_home_then_rebootstrap(tmp_path):
    loggers = TestLoggers()
    home = tmp_path / "home"
    factory = cell_factory(loggers, root=home)
    bootstrap_builtins(factory, workdir=tmp_path, source=BootstrapSource.auto)

    removed = clean_cell_home(loggers, "builtins", root=home)
    assert removed is not None
    # the cell is still known, but its home is now empty
    builtins = {c.name: c for c in list_cells(userspace(loggers, root=home))}["builtins"]
    assert builtins.size_bytes == 0

    # the headline workflow: clean, then bootstrap, and it passes
    bootstrap_builtins(factory, workdir=tmp_path, source=BootstrapSource.auto)
    builtins = {c.name: c for c in list_cells(userspace(loggers, root=home))}["builtins"]
    assert builtins.size_bytes > 0


def test_clean_all_cell_homes(tmp_path):
    loggers = TestLoggers()
    home = tmp_path / "home"
    factory = cell_factory(loggers, root=home)
    bootstrap_builtins(factory, workdir=tmp_path, source=BootstrapSource.auto)

    removed = clean_all_cell_homes(loggers, root=home)
    assert removed
    assert all(c.size_bytes == 0 for c in list_cells(userspace(loggers, root=home)))


def test_listing_and_cleaning_are_safe_when_empty(tmp_path):
    loggers = TestLoggers()
    home = tmp_path / "untouched"
    assert list_cells(userspace(loggers, root=home)) == []
    assert clean_cell_home(loggers, "builtins", root=home) is None
    assert clean_all_cell_homes(loggers, root=home) == []
