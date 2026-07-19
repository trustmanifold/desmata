"""The serverful sample cell (tally) over the session seam, end to end.

Unlike greeter (a one-shot tool), tally keeps a real process alive and is
messaged through :meth:`~desmata.interface.Cell.session`. This drives the full
factory path -- build the python dependency with nix, internalize it with the
builtin ipfs -- and then the session lifecycle + home policy on a real cell,
not a test-local one.
"""

from pathlib import Path

from injector import Injector

from desmata.higher_protocols import CellFactory
from desmata.samples.tally.cell import TallyCell
from desmata.session import Ephemeral, Persistent


def test_tally_builds_and_serves_over_a_session(components: Injector, tmp_path: Path):
    cell = components.get(CellFactory).get(TallyCell)

    # the managed dependency was built and internalized (the greeter path)
    assert cell.closure.local_name == "tally"
    assert "python" in cell.closure.python.id
    assert str(cell.closure.python.hash).startswith("dsm:ipfs:")

    # one bring-up, N ops, hermetic home
    with cell.session() as s:
        pid = s.handle.pid
        for _ in range(20):
            assert cell.bump(s) == "ok"
            assert s.handle.pid == pid  # same process across all ops
        assert (s.home / ".local" / "state" / "count").read_text() == "20"
        home = s.home
    assert not home.exists()  # default session is ephemeral -> discarded

    # a fresh session starts from zero (no leak from the one above)
    with cell.session() as s:
        assert cell.bump(s) == "ok"
        assert (s.home / ".local" / "state" / "count").read_text() == "1"

    # a persistent home accumulates across sessions
    with cell.session(home=Persistent(name="run")) as s:
        for _ in range(3):
            cell.bump(s)
    with cell.session(home=Persistent(name="run")) as s:
        cell.bump(s)
        assert (s.home / ".local" / "state" / "count").read_text() == "4"

    # the §6 bug-repro shape, on a real cell: a declared config overlay with a 6
    # makes the 6th message emit an output containing "Foo" -- the stable
    # predicate a peer would re-verify by re-injecting the identical input.
    conf = tmp_path / "app.conf"
    conf.write_text("threshold=6")
    with cell.session(home=Ephemeral(overlay={".config/app.conf": conf})) as s:
        assert s.declared_inputs[".config/app.conf"] == conf
        outputs = [cell.bump(s) for _ in range(6)]
    assert "Foo" in outputs[-1]
    assert all("Foo" not in o for o in outputs[:-1])
