from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Generic, TypeVar

from pydantic import BaseModel

from desmata.lower_protocols import DependencyId, DependencyHash, InternalPath, NucleusHash, CellHash, CellContext


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
# is everything else in the cell directory (config/glue you fork). Enforcement and
# hashing live in desmata.cell_archive; this is the canonical file list.
NUCLEUS: tuple[str, ...] = ("flake.nix", "flake.lock", "cell.py")


class Closure(BaseModel, ABC):
    local_name: str
    # Content addresses of the cell. ``nucleus_hash`` covers only the nucleus
    # files (invariant to membrane changes); ``hash`` covers the whole cell
    # (nucleus + membrane). Populated by the cell-hashing machinery
    # (desmata.cell_archive); a closure can be constructed without them.
    hash: CellHash | None = None
    nucleus_hash: NucleusHash | None = None

    # the nucleus file names (the membrane is "everything else in the cell dir")
    nucleus: ClassVar[tuple[str, ...]] = NUCLEUS


SpecificClosure = TypeVar("SpecificClosure", bound=Closure)


class Cell(ABC, Generic[SpecificClosure]):
    closure: SpecificClosure

    def __init__(self, closure: Closure, context: CellContext):
        self.closure = closure
        self.context = context

SpecificCell = TypeVar("SpecificCell", bound=Cell)
