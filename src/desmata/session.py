"""Session lifecycle and home policy for cells.

The non-deferred half of session cells
(``agent_primers/session-cells.md`` §3.1-3.2). Two ideas, both additive --
nothing that only calls a pure cell need change:

* **Session** -- the unit of setup/teardown is a *session*, not a call. A
  serverful cell brings its process(es) up once, you send N messages, and it
  tears them down once (generalizing ``serve.py``'s ``running()`` off the ipfs
  tool onto the cell). A pure cell's session is the degenerate one-call case,
  so :meth:`desmata.interface.Cell.session` defaults to a no-op setup.

* **HomePolicy** -- a cell's home is *declared*, not ambient. The default is
  :class:`Ephemeral`: a fresh scratch home, discarded on teardown, so the
  common path is reproducible and accidental reliance on left-over state is
  impossible-by-default. :class:`Persistent` opts into accumulation under a
  name; :class:`FromSnapshot` starts from a declared directory. Any policy may
  carry an ``overlay`` -- files placed into the home before bring-up, the
  declared inputs of the session (the config file of a bug repro). A declared
  input that can't be found fails loudly (:class:`CellUnavailable`) rather than
  letting the run silently succeed off whatever the home happened to contain --
  the "works on my machine" bug, turned into an error.

Reserved for the deferred gossip half (SP ROADMAP §5, ``cell-session`` runner +
predicate colors): the overlay is where a serverful ``evaluates_to``-shaped
claim gets its input identity. Content-*addressing* the overlay (a
``dsm:<backend>:...`` ref that travels to peers by hash, so a verifier
re-injects the identical bytes) rides SP §4's blob co-shipping; here the
overlay is a set of local source files, and :attr:`Session.declared_inputs`
surfaces them so the shape is present. Hashing them into a wire content-ref is
left for that later work -- the door, not the room.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class HomePolicy(BaseModel):
    """How a session's home directory is chosen and seeded.

    ``overlay`` maps a home-relative path to a local source file placed into
    the home before the cell's processes start -- the session's *declared*
    inputs. A missing source is a loud :class:`~desmata.exceptions.CellUnavailable`,
    never a silent skip.
    """

    overlay: dict[str, Path] = {}


class Ephemeral(HomePolicy):
    """A fresh scratch home, discarded on teardown. The default: hermetic, so
    the common path reproduces and left-over state cannot leak into a run."""


class Inherit(HomePolicy):
    """Use the cell's own fixed home (``context.home``), unchanged and not
    discarded. For a cell whose home is durable *identity*, not per-session
    scratch -- the ipfs builtin's repo (keys) is the motivating case: a session
    over it should serve that repo, the way ``dsm serve`` does, not spin up a
    fresh peer each time. The default only where a cell opts into it; the
    base default stays :class:`Ephemeral`."""


class Persistent(HomePolicy):
    """A named home that survives teardown and accumulates across sessions.
    Opt-in: you get accumulation only by asking for it, by name."""

    name: str


class FromSnapshot(HomePolicy):
    """Start from a copy of a declared directory (a home snapshotted earlier).

    ``source`` is a local path today; the content-addressed variant -- a
    ``dsm`` hash a peer resolves and re-injects to reproduce the exact input --
    is the deferred gossip half (module docstring). A ``source`` that does not
    exist fails loudly (:class:`~desmata.exceptions.CellUnavailable`)."""

    source: Path


@dataclass
class Session:
    """A live session over a cell: its home for the duration, and whatever
    handle the cell's :meth:`~desmata.interface.Cell.setup` returned (a
    ``Popen``, a client, a fleet handle -- cell-defined). How you message the
    session is the cell's business; this is only the scope that owns the
    processes and the home."""

    cell: Any
    home: Path
    handle: Any = None
    # the overlay that seeded this home: the session's declared inputs, surfaced
    # so "declared, not ambient" is inspectable (and the seam the gossip half's
    # input identity attaches to).
    declared_inputs: dict[str, Path] = field(default_factory=dict)
