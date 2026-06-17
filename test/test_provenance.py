"""Provenance capture: a NarInfo per store path that is both the nix-build
projection of a general Attestation and a Trustix-compatible wire record.

This is Phase 2's cheap first step (see agent_primers/phase-2.md): desmata
already computes these fields in NixPathInfo; here we persist them in a canonical,
Trustix-shaped, attestation-ready form. The general Attestation shape is what
keeps the future runtime-provenance goal reachable
(agent_primers/verifiable-computation.md).
"""

import json
from pathlib import Path

from injector import Injector

from desmata.builtins.cell import DesmataBuiltins
from desmata.fs import DesmataFiles
from desmata.lower_protocols import UserspaceFiles
from desmata.messages import NixPathInfo
from desmata.nix import Nix
from desmata.provenance import Attestation, NarInfo, closure_provenance, load, save
from desmata.log import TestLoggers


def _sample_info() -> NixPathInfo:
    return NixPathInfo(
        path=Path("/nix/store/aaa-kubo"),
        narHash="sha256-deadbeef=",
        narSize=42,
        # deliberately unsorted, with a self-reference, to test canonicalization
        references=[Path("/nix/store/ccc-z"), Path("/nix/store/aaa-kubo"),
                    Path("/nix/store/bbb-a")],
        deriver=Path("/nix/store/ddd-kubo.drv"),
        registrationTime=0,
    )


def test_narinfo_canonicalizes_references_and_keeps_deriver():
    ni = NarInfo.from_path_info(_sample_info())
    # references are sorted and kept verbatim (self-reference retained, as nix/
    # Trustix do)
    assert ni.references == (
        "/nix/store/aaa-kubo",
        "/nix/store/bbb-a",
        "/nix/store/ccc-z",
    )
    assert ni.deriver == "/nix/store/ddd-kubo.drv"


def test_trustix_key_and_value_shape():
    ni = NarInfo.from_path_info(_sample_info())
    # Key is the store path bytes
    assert ni.trustix_key() == b"/nix/store/aaa-kubo"
    # Value is compact JSON in Trustix's field order (path, narHash, narSize,
    # references) with no whitespace — so digests match Go's json.Marshal
    value = ni.trustix_value()
    assert b" " not in value
    assert value.index(b'"path"') < value.index(b'"narHash"') \
        < value.index(b'"narSize"') < value.index(b'"references"')
    decoded = json.loads(value)
    assert decoded == {
        "path": "/nix/store/aaa-kubo",
        "narHash": "sha256-deadbeef=",
        "narSize": 42,
        "references": [
            "/nix/store/aaa-kubo",
            "/nix/store/bbb-a",
            "/nix/store/ccc-z",
        ],
    }
    # deriver is intentionally NOT in the Trustix Value
    assert "deriver" not in decoded


def test_fingerprint_format():
    ni = NarInfo.from_path_info(_sample_info())
    assert ni.fingerprint() == (
        b"1;/nix/store/aaa-kubo;sha256-deadbeef=;42;"
        b"/nix/store/aaa-kubo,/nix/store/bbb-a,/nix/store/ccc-z"
    )


def test_narinfo_lifts_to_general_attestation():
    ni = NarInfo.from_path_info(_sample_info())
    att = ni.to_attestation()
    # a build is one instance of a verifiable computation
    assert isinstance(att, Attestation)
    assert att.runner == "nix"
    assert att.recipe == "/nix/store/ddd-kubo.drv"          # the .drv recipe
    assert att.determinism == "exact-hash"
    # output is the built path; inputs are its references
    assert [o.store_path for o in att.outputs] == ["/nix/store/aaa-kubo"]
    assert {i.store_path for i in att.inputs} == set(ni.references)


def test_closure_provenance_over_builtin_tool(builtins: DesmataBuiltins):
    nix = Nix(cwd=Path.cwd(), log=TestLoggers().proc)
    records = closure_provenance(nix, builtins.closure.ipfs)

    # one record per store path in the closure
    assert len(records) == 4
    by_name = {r.path.rsplit("-", 1)[-1]: r for r in records}
    kubo = next(r for r in records if "kubo" in r.path)

    # captured real provenance: content hash, size, recipe, and sorted refs
    assert kubo.nar_hash.startswith("sha256-")
    assert kubo.nar_size > 0
    assert kubo.deriver and kubo.deriver.endswith(".drv")
    assert list(kubo.references) == sorted(kubo.references)
    # kubo references its data deps
    assert any("tzdata" in r for r in kubo.references)


def test_save_load_round_trip(tmp_path: Path):
    files = DesmataFiles.sandbox(tmp_path, log=TestLoggers())
    record = NarInfo.from_path_info(_sample_info())
    save(files, [record])
    loaded = load(files)
    # keyed by store path, and survives the round trip (incl. deriver)
    assert loaded[record.path] == record


def test_build_persists_provenance(
    builtins: DesmataBuiltins, session_components: Injector
):
    # building a cell captures provenance for its tools' closures
    stored = load(session_components.get(UserspaceFiles))
    assert len(stored) >= 4
    kubo = next((r for p, r in stored.items() if "kubo" in p), None)
    assert kubo is not None
    assert kubo.deriver and kubo.deriver.endswith(".drv")
