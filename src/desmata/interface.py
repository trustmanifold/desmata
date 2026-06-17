from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from desmata.lower_protocols import DependencyId, DependencyHash, InternalPath, NucleusHash, CellHash, CellContext


class Dependency(BaseModel, ABC):
    id: DependencyId
    hash: DependencyHash
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


class Closure(BaseModel, ABC):
    local_name: str
    # These are populated once the cell-hashing machinery (Hasher) is
    # implemented; until then a closure can be constructed from its
    # dependencies alone.
    hash: CellHash | None = None
    nucleus_hash: NucleusHash | None = None


    @property
    @staticmethod
    def nucleus() -> list[str]:
        return ["./flake.nix", "./flake.lock"]

    @property
    @staticmethod
    def membrane() -> list[str]:
        return ["./cell.py"]


SpecificClosure = TypeVar("SpecificClosure", bound=Closure)


class Cell(ABC, Generic[SpecificClosure]):
    closure: SpecificClosure

    def __init__(self, closure: Closure, context: CellContext):
        self.closure = closure
        self.context = context

SpecificCell = TypeVar("SpecificCell", bound=Cell)
