"""Inspecting the structure of one tool inside a cell.

A *cell* bundles one or more *tools* (managed dependencies in its closure -- the
builtin cell has just ``ipfs``, but a user cell might define ``cowsay``, or
``ipfs`` pinned to a different version). Each tool can be viewed two ways:

* **nix** -- the tool's dependency closure as a *graph of nix store paths*
  (a path and the paths it references, transitively).
* **ipfs** -- that same closure as a *merkle DAG of content-addressed blocks*,
  rolled up per store path.

The dedup question these answer: if two tools both depend on the same store path
(say a shared ``go``), is it stored once? IPFS keys blocks by CID, so a given
store path always decomposes into the same blocks -- inspect that path under each
tool and the CID is identical, i.e. stored once. (Caveat: the per-store-path
*storage* dedups; the Phase-1 NAR-blob *transport* does not -- see
``agent_primers/ipfs-dedup.md``.)
"""

from dataclasses import dataclass, field
from pathlib import Path

from desmata.builtins.cell import DesmataBuiltins, Tools
from desmata.interface import Cell, Dependency
from desmata.nix import Nix


def known_cells() -> dict[str, type[Cell]]:
    """Cells addressable by name on the CLI. Today only the builtin cell exists;
    user-defined cells will register here once they're loadable."""
    return {"builtins": DesmataBuiltins}


def cell_tools(cell: Cell) -> list[tuple[str, Dependency]]:
    """The managed dependencies ("tools") in a cell's closure, by name."""
    closure = cell.closure
    tools: list[tuple[str, Dependency]] = []
    for name in type(closure).model_fields:
        value = getattr(closure, name)
        if isinstance(value, Dependency):
            tools.append((name, value))
    return tools


def find_tool(cell: Cell, name: str) -> Dependency | None:
    """The cell's tool dependency with this name, or None."""
    for tool_name, dep in cell_tools(cell):
        if tool_name == name:
            return dep
    return None


def _strip(store_path: str) -> str:
    return store_path.replace("/nix/store/", "")


def closure_sizes(nix: Nix, dep: Dependency) -> dict[str, int]:
    """Map each store-path id in the tool's closure to its NAR size in bytes."""
    return {
        _strip(str(i.path)): i.narSize for i in nix.closure_info(str(dep.root))
    }


# --- nix view (store-path graph) ------------------------------------------

@dataclass
class ToolNix:
    tool: str
    root_id: str               # store-path id of the tool itself
    sizes: dict[str, int]      # store-path id -> NAR size
    edges: dict[str, list[str]]  # store-path id -> ids it references

    @property
    def total_size(self) -> int:
        return sum(self.sizes.values())


def inspect_tool_nix(nix: Nix, name: str, dep: Dependency) -> ToolNix:
    """The tool's closure as a store-path graph, built from nix's own
    ``references`` (the authoritative edges -- complete, unlike the cell's
    internally-built dependency DAG)."""
    infos = nix.closure_info(str(dep.root))
    members = {_strip(str(i.path)) for i in infos}
    sizes = {_strip(str(i.path)): i.narSize for i in infos}
    edges: dict[str, list[str]] = {}
    for i in infos:
        pid = _strip(str(i.path))
        edges[pid] = sorted(
            rid
            for ref in i.references
            if (rid := _strip(str(ref))) in members and rid != pid
        )
    return ToolNix(
        tool=name, root_id=_strip(str(dep.root)), sizes=sizes, edges=edges
    )


# --- ipfs view (merkle DAG of blocks) -------------------------------------

@dataclass
class DagNode:
    cid: str
    name: str                      # store-path id (roots) or entry name (children)
    size: int                      # bytes
    blocks: int                    # total blocks in this subtree
    kind: str                      # "dir" | "file" | "leaf"
    truncated: bool = False        # a dir not expanded because of the depth limit
    children: list["DagNode"] = field(default_factory=list)


@dataclass
class ToolIpfs:
    tool: str
    roots: list[DagNode] = field(default_factory=list)  # one per store path
    unique_blocks: int = 0          # distinct block CIDs across the whole closure

    @property
    def naive_blocks(self) -> int:
        return sum(r.blocks for r in self.roots)

    @property
    def duplicates_eliminated(self) -> int:
        return self.naive_blocks - self.unique_blocks


def _build_dag(ipfs: Tools.IPFS, cid: str, name: str, size: int, depth: int) -> DagNode:
    """Walk the UnixFS DAG under ``cid`` to ``depth`` levels of *directories*.
    Files are collapsed (their internal chunk tree is shown as a block count);
    directories past ``depth`` are truncated with their subtree block count."""
    total = len(ipfs.refs(cid)) + 1
    children = ipfs.ls(cid)
    if not children:
        return DagNode(cid, name, size, blocks=1, kind="leaf")
    named = [(c, s, n) for (c, s, n) in children if n]
    if not named:
        # a file's children are unnamed chunk-tree nodes -> collapse the file
        return DagNode(cid, name, size, blocks=total, kind="file")
    if depth <= 0:
        return DagNode(cid, name, size, blocks=total, kind="dir", truncated=True)
    kids = [_build_dag(ipfs, c, n.rstrip("/"), s, depth - 1) for c, s, n in named]
    return DagNode(cid, name, size, blocks=total, kind="dir", children=kids)


def inspect_tool_ipfs(
    nix: Nix, ipfs: Tools.IPFS, name: str, dep: Dependency, *, depth: int = 2
) -> ToolIpfs:
    """Decompose the tool's closure into its content-addressed merkle DAG in
    ``ipfs`` (a scratch repo), one tree per store path, largest first.

    ``unique_blocks`` counts each block once no matter how many store paths (or,
    across invocations, tools) reference it -- so identical content is stored
    once. The deeper the tree, the more sharing surface a later tool can land on.
    """
    sizes = closure_sizes(nix, dep)
    all_blocks: set[str] = set()
    tool = ToolIpfs(tool=name)
    for pid, size in sorted(sizes.items(), key=lambda kv: kv[1], reverse=True):
        cid = ipfs.add(Path("/nix/store") / pid, recursive=True)
        all_blocks |= set(ipfs.refs(cid))
        all_blocks.add(cid)
        tool.roots.append(_build_dag(ipfs, cid, pid, size, depth))
    tool.unique_blocks = len(all_blocks)
    return tool
