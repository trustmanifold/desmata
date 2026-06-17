import contextlib
import os
import sys
import tempfile
from enum import Enum
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
from desmata.builtins.cell import Tools
from desmata.cell_factory import BasicContext
from desmata.cli.common import cli_logger
from desmata.fs import DesmataFiles
from desmata.inspect import (
    cell_tools,
    find_tool,
    inspect_tool_ipfs,
    inspect_tool_nix,
    known_cells,
)
from desmata.log import CliLoggers
from desmata.nix import Nix
from desmata.provenance import closure_provenance

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


class InspectView(str, Enum):
    nix = "nix"
    ipfs = "ipfs"
    provenance = "provenance"


def _short(store_path: str) -> str:
    return store_path.replace("/nix/store/", "")


def _short_cid(cid: str) -> str:
    return cid if len(cid) <= 18 else f"{cid[:14]}…{cid[-3:]}"


def _render_nix_graph(tool) -> None:
    """Print the tool's nix closure as an indented store-path graph (each path
    over the paths it references). Shared subtrees are shown once."""
    typer.echo(f"  {tool.root_id}  {_human_size(tool.sizes.get(tool.root_id, 0))}")
    seen: set[str] = set()

    def walk(node_id: str, prefix: str) -> None:
        children = tool.edges.get(node_id, [])
        for i, child_id in enumerate(children):
            last = i == len(children) - 1
            branch = "└─ " if last else "├─ "
            size = _human_size(tool.sizes.get(child_id, 0))
            if child_id in seen:
                typer.echo(f"{prefix}{branch}{child_id}  {size}  (shown above)")
                continue
            seen.add(child_id)
            typer.echo(f"{prefix}{branch}{child_id}  {size}")
            walk(child_id, prefix + ("   " if last else "│  "))

    walk(tool.root_id, "  ")


def _render_ipfs_dag(tool) -> None:
    """Print the tool's closure as a merkle DAG: one tree per store path, dirs
    expanded, files/cut-off dirs shown with their subtree block count."""

    def annotate(node) -> str:
        if node.kind == "file":
            return f"  [{node.blocks} blocks]"
        if node.truncated:
            return f"  [{node.blocks} blocks below]"
        return ""

    def walk(children, prefix: str) -> None:
        for i, ch in enumerate(children):
            last = i == len(children) - 1
            branch = "└─ " if last else "├─ "
            size = f"  {_human_size(ch.size)}" if ch.size else ""
            slash = "/" if ch.kind == "dir" else ""
            typer.echo(f"{prefix}{branch}{ch.name}{slash}{size}  "
                       f"{_short_cid(ch.cid)}{annotate(ch)}")
            if ch.children:
                walk(ch.children, prefix + ("   " if last else "│  "))

    for root in tool.roots:
        typer.echo("")
        typer.echo(f"  {root.name}  {_human_size(root.size)}  "
                   f"{_short_cid(root.cid)}  [{root.blocks} blocks]")
        walk(root.children, "  ")

    typer.echo("")
    typer.echo(f"  {tool.unique_blocks} unique blocks across {len(tool.roots)} "
               f"store paths ({tool.duplicates_eliminated} deduplicated within "
               "this tool).")
    typer.echo("  Sharing is by CID: a store path — or any file/subtree — shared")
    typer.echo("  with another tool has the same CID here, so it is stored once.")
    typer.echo("  Compare with `dsm inspect <cell> <other-tool> ipfs`.")


def _render_provenance(records) -> None:
    """Print the tool's per-store-path provenance (Trustix nix protocol): each
    record is a (Key=store path, Value={path,narHash,narSize,references}) entry,
    plus desmata's recipe link (deriver)."""
    for r in sorted(records, key=lambda r: r.nar_size, reverse=True):
        typer.echo("")
        typer.echo(f"  {_short(r.path)}")
        typer.echo(f"      narHash : {r.nar_hash}")
        typer.echo(f"      narSize : {_human_size(r.nar_size)}")
        typer.echo(f"      deriver : {_short(r.deriver) if r.deriver else '(none)'}")
        typer.echo(f"      refs    : {len(r.references)}")
    typer.echo("")
    typer.echo(f"  {len(records)} store paths captured, each a Trustix nix entry")
    typer.echo("  (Key = store path, Value = {path,narHash,narSize,references}).")
    if records:
        biggest = max(records, key=lambda r: r.nar_size)
        typer.echo("  example Value:")
        typer.echo(f"    {biggest.trustix_value().decode()}")


@app.command()
def inspect(
    cell: str = typer.Argument(..., help="cell name, e.g. 'builtins'"),
    tool: str = typer.Argument(
        ..., help="a managed tool in the cell, e.g. 'ipfs' (see the cell's tools)"
    ),
    view: InspectView = typer.Argument(
        ...,
        help="nix = store-path graph; ipfs = merkle DAG of blocks; "
        "provenance = Trustix-shaped narinfo per store path",
    ),
    depth: int = typer.Option(
        2, "--depth", help="ipfs view: directory levels to expand per store path"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    home: Optional[Path] = typer.Option(None, "--home"),
):
    """Show the structure of one tool inside a cell, one of two ways.

    A nix closure is a graph of nix store paths; an ipfs structure is a merkle
    DAG of content-addressed blocks. Examples:

      dsm inspect builtins ipfs nix     # ipfs tool's store-path graph
      dsm inspect builtins ipfs ipfs    # ipfs tool's block DAG
    """
    cells = known_cells()
    CellType = cells.get(cell)
    if CellType is None:
        typer.echo(f"Unknown cell '{cell}'. Known cells: {', '.join(cells)}.")
        typer.echo("(user-defined cells aren't loadable by name yet.)")
        raise typer.Exit(code=1)

    loggers = CliLoggers(verbose=verbose)
    with _quieted(verbose):
        built = cell_factory(loggers, root=home).get(CellType)
        nix = Nix(cwd=Path.cwd(), log=loggers.proc)
        dep = find_tool(built, tool)
        if dep is None:
            available = ", ".join(name for name, _ in cell_tools(built)) or "(none)"
            typer.echo(f"Cell '{cell}' has no tool '{tool}'. Tools: {available}.")
            raise typer.Exit(code=1)

        if view is InspectView.nix:
            result = inspect_tool_nix(nix, tool, dep)
        elif view is InspectView.provenance:
            result = closure_provenance(nix, dep)
        else:
            with tempfile.TemporaryDirectory() as scratch:
                files = DesmataFiles.sandbox(Path(scratch), log=loggers)
                ctx = BasicContext(
                    name="inspect-scratch",
                    cell_dir=Path(dep.root),
                    userspace=files,
                    loggers=loggers,
                )
                ipfs = Tools.IPFS(root=Path(dep.root), context=ctx)
                ipfs.init()
                result = inspect_tool_ipfs(nix, ipfs, tool, dep, depth=depth)

    if view is InspectView.nix:
        typer.echo(f"Cell '{cell}', tool '{tool}' — nix store-path graph "
                   f"({len(result.sizes)} paths, {_human_size(result.total_size)}):")
        _render_nix_graph(result)
    elif view is InspectView.provenance:
        typer.echo(f"Cell '{cell}', tool '{tool}' — provenance "
                   "(Trustix nix protocol, by store path):")
        _render_provenance(result)
    else:
        typer.echo(f"Cell '{cell}', tool '{tool}' — ipfs merkle DAG (by store path):")
        _render_ipfs_dag(result)


def cli():
    app()


# retained for backwards compatibility with older entry points
main = cli


if __name__ == "__main__":
    cli()
