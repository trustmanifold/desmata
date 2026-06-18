"""The sample (non-builtin) greeter cell.

Unlike the builtin cell (which wraps ipfs), greeter wraps an ordinary tool,
cowsay. Building it exercises the factory's non-builtin dependency path
(`build_or_get(context, hasher=...)`, hashing the tool with the builtin ipfs)
that the builtin cell skips -- so this is also the first test of a user-shaped
cell end to end: build the dependency, internalize it, run it.
"""

from desmata.higher_protocols import CellFactory
from desmata.samples.greeter.cell import GreeterCell
from injector import Injector


def test_greeter_cell_builds_and_runs_cowsay(components: Injector):
    cell = components.get(CellFactory).get(GreeterCell)

    assert cell.closure.local_name == "greeter"
    # the managed dependency was built and internalized
    assert "cowsay" in cell.closure.cowsay.id
    assert cell.closure.cowsay.hash.startswith("Qm")

    # and it actually runs: the cow says what we asked
    out = cell.greet("desmata")
    assert "desmata" in out
