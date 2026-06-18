"""A sample (non-builtin) desmata cell.

The builtin cell wraps ipfs because desmata needs it. This one wraps an ordinary
tool (cowsay) to show what a *user* cell looks like, and to exercise the
non-builtin cell path end to end: build a managed dependency with nix,
internalize it under content-addressed control (hashed by the builtin ipfs), and
expose it as a runnable tool.

Inspect it: `dsm inspect greeter cowsay {nix,ipfs,drv,provenance}`.
"""

from pathlib import Path

from desmata.cell_utils import get_nix
from desmata.interface import Cell, Closure, Dependency
from desmata.lower_protocols import CellContext, PathHasher
from desmata.tool import Tool


class Tools:
    class Cowsay(Tool):
        def __init__(self, root: Path, context: CellContext):
            loggers = context.loggers.specialize("cowsay")
            bin_dir = root / "bin"
            super().__init__(
                name="cowsay",
                path=bin_dir / "cowsay",
                log=loggers.proc,
                env_filter=context.get_env_filter(exec_path=bin_dir),
            )


class Deps:
    class Cowsay(Dependency):
        @staticmethod
        def build_or_get(context: CellContext, hasher: PathHasher) -> "Deps.Cowsay":
            nix = get_nix(context)
            root, _ = nix.build("cowsay")
            return Deps.Cowsay(
                id=Dependency.get_id(root),
                hash=hasher.get_hash(root),
                root=root,
                immediate_dependencies={},
            )

        def get_tool(self, context: CellContext) -> "Tools.Cowsay":
            return Tools.Cowsay(root=Path(self.root), context=context)


class GreeterClosure(Closure):
    cowsay: Deps.Cowsay


class GreeterCell(Cell[GreeterClosure]):
    cowsay: Tools.Cowsay

    def __init__(self, closure: GreeterClosure, context: CellContext):
        super().__init__(closure, context)
        self.cowsay = closure.cowsay.get_tool(context)

    def greet(self, message: str) -> str:
        """Run the cell's cowsay on ``message`` and return what the cow says."""
        return self.cowsay(message)
