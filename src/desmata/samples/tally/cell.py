"""A *serverful* sample desmata cell: a long-running counter server.

Where greeter wraps a one-shot tool (cowsay), tally wraps a process you keep
alive and message. :meth:`~desmata.interface.Cell.session` brings the server
up once, you send N ``bump``\\s, and teardown stops it -- the serverful runtime
shape (``agent_primers/session-cells.md`` §3.1) on a real, nix-built cell.

Two things the session seam makes observable here:

* **One bring-up per N ops.** The server is spawned in :meth:`setup`, not per
  call; 100 bumps hit one process.
* **The home is declared, not ambient.** The counter lives in the session
  home, so an :class:`~desmata.session.Ephemeral` session starts at zero and a
  :class:`~desmata.session.Persistent` one resumes. An overlay config carrying
  ``threshold=N`` makes the Nth bump emit an exception-shaped ``BOOM:Foo`` --
  the §6 bug-repro shape (``contains(out, "Foo")`` on a declared input), real.
"""

import subprocess
from pathlib import Path

from desmata.cell_utils import get_nix
from desmata.content import ContentBackend
from desmata.interface import Cell, Closure, Dependency
from desmata.lower_protocols import CellContext, DependencyHash
from desmata.session import Session
from desmata.tool import Tool

# One long-running process. Each stdin line increments a counter in $HOME; if a
# config overlay set a threshold, the message that reaches it emits an
# exception-shaped line containing "Foo". Reads only os/sys (python3Minimal).
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


class Tools:
    class Python(Tool):
        def __init__(self, root: Path, context: CellContext):
            loggers = context.loggers.specialize("python")
            bin_dir = root / "bin"
            super().__init__(
                name="python",
                path=bin_dir / "python3",
                log=loggers.proc,
                env_filter=context.get_env_filter(exec_path=bin_dir),
            )


class Deps:
    class Python(Dependency):
        @staticmethod
        def build_or_get(context: CellContext, hasher: ContentBackend) -> "Deps.Python":
            nix = get_nix(context)
            root, _ = nix.build("python")
            return Deps.Python(
                id=Dependency.get_id(root),
                hash=DependencyHash(hasher.hash_path(root)),
                root=root,
                immediate_dependencies={},
            )

        def get_tool(self, context: CellContext) -> "Tools.Python":
            return Tools.Python(root=Path(self.root), context=context)


class TallyClosure(Closure):
    python: Deps.Python


class TallyCell(Cell[TallyClosure]):
    python: Tools.Python

    def __init__(self, closure: TallyClosure, context: CellContext):
        super().__init__(closure, context)
        self.python = closure.python.get_tool(context)

    def setup(self, home: Path) -> subprocess.Popen:
        """Spawn the counter server, its ``HOME`` pointed at the session home so
        its state lives there (and is discarded/kept per the home policy)."""
        return subprocess.Popen(
            [str(self.python.path), "-u", "-c", SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env={"HOME": str(home)},
        )

    def teardown(self, handle: subprocess.Popen | None) -> None:
        if handle is not None:
            handle.stdin.close()
            handle.wait(timeout=10)

    def bump(self, session: Session) -> str:
        """Send one message to the running server and return its reply
        (``ok``, or ``BOOM:Foo`` when the overlay threshold is reached)."""
        server: subprocess.Popen = session.handle
        server.stdin.write("bump\n")
        server.stdin.flush()
        return server.stdout.readline().strip()
