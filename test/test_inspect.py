"""`dsm inspect <cell> <tool> <view>` shows one tool's structure two ways: its
nix store-path graph, or its IPFS merkle DAG of blocks.

The builtin cell manages one tool (ipfs/kubo), statically linked, so there's no
shared dependency to dedup *yet*. These tests assert the analysis is correct and
the command/resolution wiring works, so the structure is legible the moment a
user cell adds a second tool. See agent_primers/ipfs-dedup.md.
"""

from pathlib import Path

from desmata.builtins.cell import DesmataBuiltins
from desmata.cli.dsm import app
from desmata.inspect import (
    cell_tools,
    find_tool,
    inspect_tool_ipfs,
    inspect_tool_nix,
    known_cells,
)
from desmata.log import TestLoggers
from desmata.nix import Nix

from conftest import make_ipfs


def test_inspect_command_is_registered():
    names = {info.callback.__name__ for info in app.registered_commands}
    assert "inspect" in names


def test_known_cells_and_tool_resolution(builtins: DesmataBuiltins):
    assert known_cells().get("builtins") is DesmataBuiltins
    # the builtin cell exposes exactly the ipfs tool
    assert [name for name, _ in cell_tools(builtins)] == ["ipfs"]
    assert find_tool(builtins, "ipfs") is builtins.closure.ipfs
    assert find_tool(builtins, "cowsay") is None


def test_inspect_tool_nix_graph(builtins: DesmataBuiltins):
    nix = Nix(cwd=Path.cwd(), log=TestLoggers().proc)
    result = inspect_tool_nix(nix, "ipfs", builtins.closure.ipfs)

    assert result.tool == "ipfs"
    assert result.total_size > 0
    # the closure has the root plus its data deps, all sized
    assert len(result.sizes) >= 4
    assert result.root_id == builtins.closure.ipfs.id
    assert "kubo" in result.root_id
    # the graph is complete: every closure path is a node with an edge entry,
    # and the root reaches the rest via references
    assert set(result.edges) == set(result.sizes)
    assert result.edges[result.root_id]  # kubo references its data deps


def test_inspect_tool_ipfs_dag(builtins: DesmataBuiltins, tmp_path: Path):
    nix = Nix(cwd=Path.cwd(), log=TestLoggers().proc)
    scratch = make_ipfs(
        Path(builtins.closure.ipfs.root), tmp_path / "scratch", name="scratch"
    )

    result = inspect_tool_ipfs(nix, scratch, "ipfs", builtins.closure.ipfs, depth=2)

    # one DAG tree per store path, each a real subtree of blocks
    assert len(result.roots) == 4
    assert result.unique_blocks > 0
    assert all(r.blocks > 0 for r in result.roots)
    # naive >= unique always; equal here since kubo's paths share no blocks
    assert result.naive_blocks >= result.unique_blocks

    # the tree is genuinely deeper than store paths: the kubo root expands into
    # child nodes (bin/ → the ipfs binary), each carrying its own CID
    kubo = max(result.roots, key=lambda r: r.size)
    assert "kubo" in kubo.name
    assert kubo.children  # decomposed below the store-path level
    assert all(child.cid for child in kubo.children)

    # re-adding a store path is deterministic (same CID) — the basis of dedup
    again = scratch.add(Path("/nix/store") / kubo.name, recursive=True)
    assert again == kubo.cid
