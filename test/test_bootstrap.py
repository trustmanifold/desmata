"""Tests for the bootstrap logic behind `dsm check` and `dsm bootstrap`.

These exercise the functions directly (rather than through the typer CLI) so
they run against real stderr file descriptors; the thin CLI wrapper is covered
in test_cli.py.
"""

import pytest

from desmata.bootstrap import (
    BootstrapSource,
    bootstrap_builtins,
    cell_factory,
    check_trusted_tools,
)
from desmata.log import TestLoggers


def test_check_trusted_tools_pass_on_conforming_host():
    results = {r.name: r for r in check_trusted_tools(TestLoggers())}
    assert results["nix"].ok, results["nix"].detail
    assert results["git"].ok, results["git"].detail
    # the detail carries the satisfying version
    assert "version" in results["nix"].detail


def test_bootstrap_builds_and_uses_builtin_cell(tmp_path):
    factory = cell_factory(TestLoggers(), root=tmp_path / "home")
    result = bootstrap_builtins(
        factory, workdir=tmp_path, source=BootstrapSource.auto
    )
    # auto resolves to the internet path until peer transport exists
    assert result.source is BootstrapSource.internet
    assert result.cell_local_name == "builtins"
    # ipfs built and produced content addresses (self-describing string form)
    assert result.ipfs_dep_hash.startswith("dsm:ipfs:Qm")
    assert result.probe_cid.startswith("Qm")


def test_bootstrap_peer_source_requires_peer_args(tmp_path):
    # peer bootstrap is implemented, but needs the peer's store URL + ipfs path
    # (automatic discovery awaits cell manifests)
    factory = cell_factory(TestLoggers(), root=tmp_path / "home")
    with pytest.raises(ValueError):
        bootstrap_builtins(
            factory, workdir=tmp_path, source=BootstrapSource.peer
        )
