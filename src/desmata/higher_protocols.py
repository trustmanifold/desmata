"a protocol is a 'higher' protocol if it depends on something in desmata.interfaces"
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from desmata.lower_protocols import DependencyHash, CellHash, NucleusHash, CellHashes
from desmata.interface import Cell, Closure, Dependency

SpecificCell = TypeVar("SpecificCell", bound=Cell)

@runtime_checkable
class CellFactory(Protocol):
    def get(self, CellType: type[SpecificCell]) -> SpecificCell:
        raise NotImplementedError()


class Hasher(Protocol):

    def get_dependency_hash(self, dep: Dependency) -> DependencyHash:
        raise NotImplementedError()

    def get_cell_hash(self, closure: Closure) -> CellHash:
        raise NotImplementedError()

    def get_nucleus_hash(self, closure: Closure) -> NucleusHash:
        raise NotImplementedError()


class Storage(Protocol):

    def pack_cell(self, closure: Closure) -> CellHashes:
        raise NotImplementedError()

    def unpack_cell(self, hash: CellHash, into: Path) -> CellHashes:
        raise NotImplementedError()

