import contextlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import typer

from desmata.bootstrap import (
    BootstrapSource,
    ToolCheck,
    bootstrap_builtins,
    cell_factory,
    check_trusted_tools,
    clean_all_cell_homes,
    clean_cell_home,
    list_cells,
    userspace,
)
from desmata.cli.common import cli_logger
from desmata.log import CliLoggers

app = typer.Typer()

# A one-line reminder of why desmata depends on each trusted tool.
TOOL_PURPOSE = {
    "nix": "builds and pins desmata's managed dependencies",
    "git": "local repository operations",
}


@contextlib.contextmanager
def _quieted(verbose: bool):
    """Suppress the underlying tools' chatter (nix/ipfs progress, captured
    stdout) by redirecting fd 2 while the work runs. If the work fails, the
    captured output is replayed so errors aren't hidden. With ``verbose`` the
    chatter is left alone."""
    if verbose:
        yield
        return

    buffer = tempfile.TemporaryFile(mode="w+")
    sys.stderr.flush()
    saved_fd = os.dup(2)
    os.dup2(buffer.fileno(), 2)
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        sys.stderr.flush()
        os.dup2(saved_fd, 2)
        os.close(saved_fd)
        if failed:
            buffer.seek(0)
            captured = buffer.read()
            if captured.strip():
                sys.stderr.write("\n--- tool output (shown because the step failed) ---\n")
                sys.stderr.write(captured)
        buffer.close()


def _render_checks(results: list[ToolCheck]) -> bool:
    for r in results:
        mark = "ok  " if r.ok else "FAIL"
        purpose = TOOL_PURPOSE.get(r.name, "")
        line = f"  [{mark}] {r.name:<4} {r.detail}"
        if purpose:
            line += f"  — {purpose}"
        typer.echo(line)
    return all(r.ok for r in results)


@app.command()
def ls(verbose: bool = typer.Option(False, "--verbose", "-v")):
    log = cli_logger(verbose=verbose)
    log.info("dsm command: ls")


@app.command()
def check(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Verify the trusted tools (nix, git) desmata depends on.

    Desmata does not manage these itself -- it assumes you installed them and
    only checks that their versions meet its expectations.
    """
    loggers = CliLoggers(verbose=verbose)

    typer.echo("Checking the tools desmata trusts you to provide.")
    typer.echo("(desmata relies on your installation of these; it does not manage them.)")
    typer.echo("")

    with _quieted(verbose):
        results = check_trusted_tools(loggers)
    ok = _render_checks(results)

    typer.echo("")
    if ok:
        typer.echo("All trusted tools are present and conform. You're ready to bootstrap.")
    else:
        typer.echo("Some trusted tools are missing or too old (see above); install/upgrade them.")
        raise typer.Exit(code=1)


@app.command()
def bootstrap(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    source: BootstrapSource = typer.Option(
        BootstrapSource.auto,
        "--source",
        help="where to acquire the builtin cell from",
    ),
    home: Optional[Path] = typer.Option(
        None,
        "--home",
        help="sandbox desmata's state under this directory instead of the XDG dirs",
    ),
):
    """Verify desmata can build and use its managed dependencies.

    Builds the builtin cell (which wraps ipfs/kubo) and uses it to
    content-address a probe -- end-to-end proof that the managed-dependency
    path works on this host.
    """
    loggers = CliLoggers(verbose=verbose)

    typer.echo("Bootstrapping desmata.")
    typer.echo("Goal: prove desmata can acquire and use a managed dependency (ipfs).")
    typer.echo("")

    typer.echo("Step 1/2: verify the trusted tools (nix, git)")
    with _quieted(verbose):
        checks = check_trusted_tools(loggers)
    if not _render_checks(checks):
        typer.echo("")
        typer.echo("Trusted tools are not in order; aborting bootstrap.")
        raise typer.Exit(code=1)
    typer.echo("")

    typer.echo("Step 2/2: build the builtin (ipfs) cell and use it")
    typer.echo("  desmata builds ipfs with nix, then brings it and its whole")
    typer.echo("  dependency closure under content-addressed control. The first run")
    typer.echo("  may download from the internet; afterwards it's served from cache.")
    typer.echo("  (run with --verbose to watch nix and ipfs work)")

    factory = cell_factory(loggers, root=home)
    try:
        with tempfile.TemporaryDirectory() as workdir:
            with _quieted(verbose):
                result = bootstrap_builtins(
                    factory, workdir=Path(workdir), source=source
                )
    except NotImplementedError as e:
        typer.echo("")
        typer.echo(f"  [unavailable] {e}")
        raise typer.Exit(code=2)
    except Exception as e:
        typer.echo("")
        typer.echo(f"  bootstrap failed: {e}")
        typer.echo("  re-run with --verbose to see the tool output, or reset the")
        typer.echo("  builtin cell with `dsm clean builtins` and try again.")
        raise typer.Exit(code=1)
    typer.echo("  done.")
    typer.echo("")

    typer.echo(f"Bootstrapped '{result.cell_local_name}' via {result.source}.")
    typer.echo(f"  ipfs dependency   : {result.ipfs_dep_id}")
    typer.echo("      └ the nix store identity of the kubo build desmata now manages")
    typer.echo(f"  builtin cell hash : {result.ipfs_dep_hash}")
    typer.echo("      └ its content address: the same bytes hash to this everywhere")
    typer.echo(f"  probe \"desmata\"   → {result.probe_cid}")
    typer.echo("      └ produced by the managed ipfs, proving the cell actually runs")
    typer.echo("")
    typer.echo("Meaning: desmata can build and use its managed dependencies here.")
    typer.echo("Because everything is addressed by hash, this same cell can later be")
    typer.echo("reproduced -- or fetched from a peer when the internet is unavailable")
    typer.echo("(the peer path is not built yet: try `--source peer`).")


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PiB"


def _report_removed(what: str, removed: list[Path]) -> None:
    if removed:
        typer.echo(f"Cleared {what}:")
        for path in removed:
            typer.echo(f"  {path}")
    else:
        typer.echo(f"Nothing to clear for {what}.")


@app.command()
def cells(
    home: Optional[Path] = typer.Option(
        None, "--home", help="inspect a sandboxed state dir instead of the XDG dirs"
    ),
):
    """List the cells desmata has local state for.

    Each cell keeps its runtime state in a home directory; `dsm clean` can reset
    it.
    """
    infos = list_cells(userspace(CliLoggers(), root=home))
    if not infos:
        typer.echo("No cells yet. Run `dsm bootstrap` to create the builtin cell.")
        return
    typer.echo("Cells with local state:")
    for cell in infos:
        typer.echo(f"  {cell.name:<20} {_human_size(cell.size_bytes):>10}")


@app.command()
def clean(
    cell: Optional[str] = typer.Argument(
        None, help="name of the cell whose home directory to clear (see `dsm cells`)"
    ),
    everything: bool = typer.Option(
        False, "--all", help="clear every cell's home directory"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    home: Optional[Path] = typer.Option(
        None, "--home", help="operate on a sandboxed state dir instead of the XDG dirs"
    ),
):
    """Clear cells' home directories, resetting their runtime state.

    A cell's home holds whatever it stores at runtime; for the builtin cell that
    includes the ipfs repo and keys. This works for any cell type.
    """
    loggers = CliLoggers(verbose=verbose)
    if everything:
        removed = clean_all_cell_homes(loggers, root=home)
        _report_removed("all cell home directories", removed)
        return
    if cell is None:
        typer.echo("Name a cell to clear, or pass --all. See `dsm cells`.")
        raise typer.Exit(code=1)
    removed = clean_cell_home(loggers, cell, root=home)
    if removed is None:
        typer.echo(f"Cell '{cell}' has no home state to clear. See `dsm cells`.")
        raise typer.Exit(code=1)
    _report_removed(f"home directory for cell '{cell}'", [removed])


def cli():
    app()


# retained for backwards compatibility with older entry points
main = cli
