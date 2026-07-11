"""The public entry point for resolving cells by hash.

The README's aspiration is ``from desmata.get import from_hash``. The current
implementation lives in :mod:`desmata.cell_archive` (unpack a cell -- nucleus
and membrane -- by its content hash, load its Cell class, and build it).
``from_hash`` accepts the hash's string form (``"dsm:ipfs:..."``) and resolves
offline-first: local content needs no daemon, anything else is fetched from
peers via ``dsm serve`` (:class:`CellUnavailable` says so when it can't). The
``interface=`` check is the remaining layer.
"""

from desmata.cell_archive import (
    InvalidCell,
    artifact_pins,
    cell_hash,
    cell_hashes,
    from_hash,
    from_peer,
    load_cell_class,
    membrane_files,
    nucleus_hash,
    pack_cell,
    publish_cell,
    verify_artifacts,
    verify_has_nucleus,
)
from desmata.exceptions import ArtifactPinMismatch, CellUnavailable

__all__ = [
    "ArtifactPinMismatch",
    "CellUnavailable",
    "InvalidCell",
    "artifact_pins",
    "cell_hash",
    "cell_hashes",
    "from_hash",
    "from_peer",
    "load_cell_class",
    "membrane_files",
    "nucleus_hash",
    "pack_cell",
    "publish_cell",
    "verify_artifacts",
    "verify_has_nucleus",
]
