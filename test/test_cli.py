"""The `dsm` CLI wires the bootstrap logic (tested in test_bootstrap.py) into
typer commands. Here we just assert the commands and entry point exist, since
exercising them end-to-end would spawn subprocesses against typer's captured
stderr (which has no file descriptor)."""

from desmata.cli.dsm import app, cli


def test_dsm_exposes_check_and_bootstrap_commands():
    names = {info.callback.__name__ for info in app.registered_commands}
    assert {"check", "bootstrap"} <= names


def test_dsm_entry_point_is_callable():
    # pyproject points the `dsm` script at this symbol
    assert callable(cli)


def test_dsm_registers_clean_and_cells_commands():
    names = {info.callback.__name__ for info in app.registered_commands}
    assert {"clean", "cells"} <= names
