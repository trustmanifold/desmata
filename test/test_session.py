"""Session lifecycle + hermetic home (agent_primers/session-cells.md §3.1-3.2).

Validates the *mechanism* with a real subprocess server -- a genuine
long-running process you send N messages to, not a mock of the interface --
plus real file-level home materialization. It does not exercise a nix-backed
cell or the deferred gossip half; wiring the ipfs daemon (serve.py) through
this seam and a nix serverful sample cell are the next validating steps.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from desmata.cell_factory import BasicContext
from desmata.exceptions import CellUnavailable
from desmata.fs import DesmataFiles
from desmata.interface import Cell, Closure
from desmata.log import TestLoggers
from desmata.session import Ephemeral, FromSnapshot, Persistent


def make_context(root: Path, name: str = "probe") -> BasicContext:
    log = TestLoggers()
    userspace = DesmataFiles.sandbox(root, log=log)
    return BasicContext(name=name, cell_dir=root, userspace=userspace, loggers=log)


# --- home policy, no cell needed -------------------------------------------


def test_ephemeral_home_is_discarded(tmp_path: Path):
    ctx = make_context(tmp_path)
    with ctx.home_for(Ephemeral()) as home:
        assert home.exists()
        assert (home / ".config").is_dir()  # XDG subdirs materialized
        (home / ".local" / "state" / "x").write_text("scratch")
        captured = home
    assert not captured.exists()  # hermetic: gone on teardown


def test_persistent_home_accumulates(tmp_path: Path):
    ctx = make_context(tmp_path)
    with ctx.home_for(Persistent(name="s1")) as home:
        (home / ".local" / "state" / "count").write_text("1")
    with ctx.home_for(Persistent(name="s1")) as home:
        assert (home / ".local" / "state" / "count").read_text() == "1"


def test_overlay_injects_declared_input(tmp_path: Path):
    ctx = make_context(tmp_path)
    src = tmp_path / "app.conf"
    src.write_text("threshold=6")
    with ctx.home_for(Ephemeral(overlay={".config/app.conf": src})) as home:
        assert (home / ".config" / "app.conf").read_text() == "threshold=6"


def test_missing_overlay_input_fails_loud(tmp_path: Path):
    ctx = make_context(tmp_path)
    with pytest.raises(CellUnavailable):
        with ctx.home_for(Ephemeral(overlay={".config/app.conf": tmp_path / "nope"})):
            pass  # a declared input that isn't there is an error, not a silent skip


def test_missing_snapshot_source_fails_loud(tmp_path: Path):
    ctx = make_context(tmp_path)
    with pytest.raises(CellUnavailable):
        with ctx.home_for(FromSnapshot(source=tmp_path / "no-such-dir")):
            pass


def test_snapshot_seeds_from_source(tmp_path: Path):
    ctx = make_context(tmp_path)
    snap = tmp_path / "snap"
    (snap / ".local" / "state").mkdir(parents=True)
    (snap / ".local" / "state" / "count").write_text("7")
    with ctx.home_for(FromSnapshot(source=snap)) as home:
        assert (home / ".local" / "state" / "count").read_text() == "7"
        home.name  # a copy: mutating it must not touch the source
        (home / ".local" / "state" / "count").write_text("8")
    assert (snap / ".local" / "state" / "count").read_text() == "7"


# --- a real serverful cell over the session seam ---------------------------

# One long-running process. Each stdin line increments a counter in its HOME;
# if a config overlay set a threshold, the message that reaches it emits an
# exception-shaped line containing "Foo" -- the §6 bug-repro example, real.
SERVER = (
    "import os, sys\n"
    "conf = os.path.join(os.environ['HOME'], '.config', 'app.conf')\n"
    "threshold = None\n"
    "if os.path.exists(conf):\n"
    "    for kv in open(conf):\n"
    "        if kv.startswith('threshold='):\n"
    "            threshold = int(kv.split('=', 1)[1])\n"
    "p = os.path.join(os.environ['HOME'], '.local', 'state', 'count')\n"
    "for line in sys.stdin:\n"
    "    n = (int(open(p).read()) if os.path.exists(p) else 0) + 1\n"
    "    open(p, 'w').write(str(n))\n"
    "    hit = threshold is not None and n == threshold\n"
    "    sys.stdout.write(('BOOM:Foo' if hit else 'ok') + '\\n')\n"
    "    sys.stdout.flush()\n"
)


class _NoDeps(Closure):
    pass


class CounterServerCell(Cell[_NoDeps]):
    def setup(self, home: Path):
        return subprocess.Popen(
            [sys.executable, "-u", "-c", SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env={**os.environ, "HOME": str(home)},  # server's state lives in the session home
        )

    def teardown(self, handle) -> None:
        if handle is not None:
            handle.stdin.close()
            handle.wait(timeout=10)

    def bump(self, session) -> str:
        session.handle.stdin.write("bump\n")
        session.handle.stdin.flush()
        return session.handle.stdout.readline().strip()


def make_cell(tmp_path: Path, name: str = "counter") -> CounterServerCell:
    return CounterServerCell(_NoDeps(local_name=name), make_context(tmp_path, name=name))


def test_one_bringup_per_n_ops(tmp_path: Path):
    cell = make_cell(tmp_path)
    with cell.session() as s:
        pid = s.handle.pid
        for _ in range(100):
            assert cell.bump(s) == "ok"
            assert s.handle.pid == pid  # same process across all 100 ops -- one bring-up
        assert (s.home / ".local" / "state" / "count").read_text() == "100"
        home = s.home
    assert not home.exists()  # default session is ephemeral


def test_ephemeral_sessions_do_not_leak(tmp_path: Path):
    cell = make_cell(tmp_path)
    for _ in range(2):
        with cell.session() as s:
            for _ in range(3):
                cell.bump(s)
            assert (s.home / ".local" / "state" / "count").read_text() == "3"
    # each of the two sessions started from zero: no accumulation across them


def test_persistent_session_accumulates(tmp_path: Path):
    cell = make_cell(tmp_path)
    with cell.session(home=Persistent(name="run")) as s:
        for _ in range(3):
            cell.bump(s)
    with cell.session(home=Persistent(name="run")) as s:
        for _ in range(2):
            cell.bump(s)
        assert (s.home / ".local" / "state" / "count").read_text() == "5"


def test_bug_repro_shape(tmp_path: Path):
    """§6 door, concrete: a declared config overlay containing a 6 drives the
    app to emit an output containing 'Foo' on the 6th message. `contains(out,
    "Foo")` is the stable predicate a peer would re-verify by re-injecting the
    identical declared input -- surfaced on the session as a declared, (later)
    content-addressable input. The gossip/predicate-color half is deferred."""
    cell = make_cell(tmp_path)
    conf = tmp_path / "app.conf"
    conf.write_text("threshold=6")
    with cell.session(home=Ephemeral(overlay={".config/app.conf": conf})) as s:
        assert s.declared_inputs[".config/app.conf"] == conf
        outputs = [cell.bump(s) for _ in range(6)]
    assert "Foo" in outputs[-1]
    assert all("Foo" not in o for o in outputs[:-1])
