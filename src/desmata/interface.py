from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar, Generic, Iterator, TypeVar

from pydantic import BaseModel

from desmata.lower_protocols import DependencyId, DependencyHash, InternalPath, NucleusHash, CellHash, CellContext
from desmata.session import Ephemeral, HomePolicy, Session


class Dependency(BaseModel, ABC):
    id: DependencyId
    # None only transiently: a peer-bootstrapped dependency exists on disk
    # before its content address has been computed.
    hash: DependencyHash | None = None
    root: InternalPath
    immediate_dependencies: dict[DependencyId, 'Dependency']

    @staticmethod
    @abstractmethod
    def build_or_get(context: CellContext) -> 'Dependency':
        raise NotImplementedError()

    @staticmethod
    def get_id(root: Path) -> str:
        if root.parts[:3] != ("/", "nix", "store"):
            raise NotImplementedError(
                f"Unable to determine a suitable dependency ID from {root.resolve()}, "
                "please override get_id(Path) on the corresponding subclass of Dependency"
            )
        else:
            return root.parts[3]


# The nucleus: a cell's stable, defining files -- hashed and shared. The membrane
# is everything else in the cell directory (the small, forkable part you audit).
# NUCLEUS is the mandatory core; an author widens the nucleus with an optional
# declaration file (NUCLEUS_DECLARATION: one relative path per line, itself part
# of the nucleus when present -- the boundary lives inside the hash, so forks
# can't disagree about where it sits). Enforcement and hashing live in
# desmata.cell_archive. See agent_primers/nucleus-membrane.md.
NUCLEUS: tuple[str, ...] = ("flake.nix", "flake.lock", "cell.py")
NUCLEUS_DECLARATION = "nucleus"


class Closure(BaseModel, ABC):
    local_name: str
    # Content addresses of the cell. ``nucleus_hash`` covers only the nucleus
    # files (invariant to membrane changes); ``hash`` covers the whole cell
    # (nucleus + membrane). Populated by the cell-hashing machinery
    # (desmata.cell_archive); a closure can be constructed without them.
    hash: CellHash | None = None
    nucleus_hash: NucleusHash | None = None

    # the mandatory nucleus file names (an author widens the set via the
    # NUCLEUS_DECLARATION file, not here: reading a ClassVar means executing
    # cell.py, and computing a hash must never run code)
    nucleus: ClassVar[tuple[str, ...]] = NUCLEUS


SpecificClosure = TypeVar("SpecificClosure", bound=Closure)


class Cell(ABC, Generic[SpecificClosure]):
    closure: SpecificClosure

    def __init__(self, closure: Closure, context: CellContext):
        self.closure = closure
        self.context = context

    @contextmanager
    def session(self, *, home: HomePolicy | None = None) -> Iterator[Session]:
        """Bring the cell's processes up, yield a :class:`~desmata.session.Session`
        you drive with N messages, tear them down on exit -- the unit of
        setup/teardown is the session, not the call, so a serverful cell pays
        one bring-up per batch instead of one per operation.

        ``home`` declares where the session's state lives; the default is
        :class:`~desmata.session.Ephemeral` (a fresh scratch home, discarded),
        so a session is hermetic unless you opt into accumulation. A pure
        cell inherits the default :meth:`setup`/:meth:`teardown` (no processes)
        and its session is the degenerate one-call scope -- calling this on the
        greeter just gives you a throwaway home and ``session.cell``."""
        policy = home if home is not None else Ephemeral()
        with self.context.home_for(policy) as home_path:
            handle = self.setup(home_path)
            try:
                yield Session(
                    cell=self,
                    home=home_path,
                    handle=handle,
                    declared_inputs=dict(policy.overlay),
                )
            finally:
                self.teardown(handle)

    def setup(self, home: Path) -> object | None:
        """Bring the cell's processes up against ``home`` and return a handle to
        them (a ``Popen``, a client, a compose/k8s fleet handle). Default: none
        -- a pure cell has nothing to start, so its session is a single call.
        A serverful cell overrides this to spawn (the ``serve.py`` shape),
        pointing its processes' ``HOME`` at ``home``."""
        return None

    def teardown(self, handle: object | None) -> None:
        """Tear down what :meth:`setup` brought up. Default: nothing."""
        return None

SpecificCell = TypeVar("SpecificCell", bound=Cell)
