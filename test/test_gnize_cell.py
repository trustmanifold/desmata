"""Test A of the lightweight-cell pilot (gnize-cell/PLAN.md): the foundry path.

Desmata fetches gnize-cell by hash, realizes its pinned wasm component, calls
`fingerprints(...)` through the invoker seam, and pipes the typed records --
as JSON -- into nushell-cell: two cells composed, cross-checked against the
native `gn --json` binary as an independent oracle.

The variant test deletes the fetched blob and re-realizes it via the recipe
path: nix builds the component, the output is held against the nucleus pin,
and the act mints a locally-witnessed builds_to(nucleus, artifact) --
the attestation heavy peers gossip so light peers can skip the build.
"""

import os
from pathlib import Path

import pytest
from desmata.builtins.cell import DesmataBuiltins
from desmata.cell_archive import load_cell_class
from desmata.get import from_hash, publish_cell
from desmata.higher_protocols import CellFactory
from desmata.provenance import SP_BUILDS_TO, load_brushstrokes
from injector import Injector

from gnize_oracle import oracle_records

GNIZE_CELL_SRC = os.environ.get("GNIZE_CELL_SRC")
NUSHELL_CELL_SRC = os.environ.get("NUSHELL_CELL_SRC")

pytestmark = [
    pytest.mark.wasm,
    pytest.mark.skipif(
        GNIZE_CELL_SRC is None or NUSHELL_CELL_SRC is None,
        reason="GNIZE_CELL_SRC / NUSHELL_CELL_SRC are not set "
        "(the dev shell exports the pinned cell fixtures)",
    ),
]

# deterministic, longer than the default ladder's smallest width (15)
DATA = (
    b"the quick brown fox jumps over the lazy dog; "
    b"pack my box with five dozen liquor jugs; "
    b"how vexingly quick daft zebras jump"
)


def test_gnize_composes_with_nushell_and_matches_the_native_oracle(
    builtins: DesmataBuiltins, components: Injector, tmp_path: Path
):
    # publish gnize-cell, then resolve it by hash alone into a fresh location
    hashes = publish_cell(builtins.ipfs, Path(GNIZE_CELL_SRC))
    factory = components.get(CellFactory)
    cell = from_hash(
        builtins.ipfs, factory, str(hashes.cell_hash), into=tmp_path / "fetched"
    )

    # the typed surface: WIT records as Python dicts
    records = cell.fingerprints(DATA, channel=0, widths=(), min_zeros=0)
    assert records, "expected fingerprints for data wider than the ladder's first rung"
    assert all(set(r) == {"channel", "width", "value", "score"} for r in records)

    # compose with nushell-cell: gnize's JSON through a nushell pipeline
    nu = factory.get(load_cell_class(Path(NUSHELL_CELL_SRC)))
    max_via_nu = nu.str_to_str(
        stages=["from json", "get score", "math max", "to text"],
        input=cell.fingerprints_json(DATA, channel=0, widths=(), min_zeros=0),
    )
    max_via_python = max(r["score"] for r in records)
    assert int(max_via_nu) == max_via_python

    # the native binary, built from the same source rev, is the oracle
    oracle = oracle_records(Path(GNIZE_CELL_SRC), DATA, channel=0)
    assert max(r["score"] for r in oracle) == max_via_python
    assert sorted((r["width"], r["score"]) for r in oracle) == sorted(
        (r["width"], r["score"]) for r in records
    )


def test_deleted_blob_re_realizes_via_recipe_and_mints_builds_to(
    builtins: DesmataBuiltins, components: Injector, tmp_path: Path
):
    hashes = publish_cell(builtins.ipfs, Path(GNIZE_CELL_SRC))
    factory = components.get(CellFactory)
    fetched = tmp_path / "fetched"
    from_hash(builtins.ipfs, factory, str(hashes.cell_hash), into=fetched)

    # a light peer's worst case: the pin arrived but the bytes didn't
    (fetched / "gnize_wasm.wasm").unlink()

    # re-realizing walks the recipe path: nix build, verify against the pin
    cell = factory.get(load_cell_class(fetched))
    blob = fetched / "gnize_wasm.wasm"
    assert blob.exists(), "the recipe path must materialize the blob again"
    assert builtins.ipfs.hash_path(blob) == cell.closure.component.hash

    # and the act was witnessed: builds_to(nucleus, artifact) is now provenance
    strokes = [s for s in load_brushstrokes(factory.userspace) if s.color == SP_BUILDS_TO]
    assert any(str(cell.closure.component.hash) in s.args for s in strokes)

    # the re-realized component still answers
    records = cell.fingerprints(b"hi", channel=7, widths=(2,), min_zeros=0)
    assert records and records[0]["width"] == 2 and records[0]["channel"] == 7
