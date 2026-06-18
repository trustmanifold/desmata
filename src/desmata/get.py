"""The public entry point for resolving cells by hash.

The README's aspiration is ``from desmata.get import from_hash``. The current
implementation lives in :mod:`desmata.cell_archive` (unpack a cell's nucleus by
its content hash, load its Cell class, and build it). This module re-exports it
as the stable public name; discovery (fetching the bundle from a peer given only
the hash) and an ``interface=`` check are the remaining layers.
"""

from desmata.cell_archive import from_hash, load_cell_class, nucleus_hash, pack_cell

__all__ = ["from_hash", "load_cell_class", "nucleus_hash", "pack_cell"]
