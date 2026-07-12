"""Provenance capture: a NarInfo per store path that is both the nix-build
projection of a general Attestation and a Trustix-compatible wire record.

This is Phase 2's cheap first step (see agent_primers/phase-2.md): desmata
already computes these fields in NixPathInfo; here we persist them in a canonical,
Trustix-shaped, attestation-ready form. The general Attestation shape is what
keeps the future runtime-provenance goal reachable
(agent_primers/verifiable-computation.md).
"""

import base64
import json
from pathlib import Path

from injector import Injector

from desmata.builtins.cell import DesmataBuiltins
from desmata.fs import DesmataFiles
from desmata.lower_protocols import UserspaceFiles
from desmata.messages import NixPathInfo
from desmata.nix import DerivationInfo, Nix
from desmata.provenance import (
    SP_SUITE,
    Attestation,
    Brushstroke,
    NarInfo,
    closure_provenance,
    derivation_root,
    load,
    load_brushstrokes,
    load_derivations,
    save,
    save_brushstrokes,
    save_derivations,
)
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


def _sample_stroke() -> Brushstroke:
    return Brushstroke(
        color="builds_to",
        palette="reproducibility/v1",
        args=("/nix/store/ddd-kubo.drv", "sha256-deadbeef="),
        created_at=1_752_200_000_000,
    )


def test_brushstroke_canonical_bytes_match_sp():
    # Pinned against SemanticPaint's spd/core/canonical.gleam: a compact JSON
    # *array* of the identity-bearing fields in fixed order (color, palette,
    # args, placer, created_at, suite, arg_versions-as-pairs), sig excluded,
    # absent arg_versions rendered []. A change here breaks SP signature
    # verification — change canonical.gleam first, then this vector.
    stroke = _sample_stroke()
    stamped = stroke.signed("a" * 64, lambda msg: b"sig-over:" + msg)
    assert stamped.canonical_bytes() == (
        b'["builds_to","reproducibility/v1",'
        b'["/nix/store/ddd-kubo.drv","sha256-deadbeef="],'
        b'"' + b"a" * 64 + b'",1752200000000,"v1-ed25519-sha256",[]]'
    )


def test_brushstroke_signed_covers_canonical_bytes_with_sig_empty():
    stroke = _sample_stroke()
    signed = stroke.signed("f" * 64, lambda msg: b"MAC:" + msg)
    # placer and suite are stamped; the signature covers the canonical bytes of
    # the stamped-but-unsigned stroke (sig plays no part in the canonical form,
    # exactly as in canonical.gleam's sign/verify pair)
    assert signed.placer == "f" * 64
    assert signed.suite == SP_SUITE
    assert base64.b64decode(signed.sig) == b"MAC:" + signed.canonical_bytes()


def test_brushstroke_wire_form_matches_sp_decoder():
    # Field names as spd/generated/brushstroke.gleam decodes them (snake_case).
    d = _sample_stroke().signed("f" * 64, lambda _: b"s").to_dict()
    assert set(d) == {
        "color", "palette", "args", "placer", "created_at",
        "suite", "sig", "arg_versions",
    }
    assert d["arg_versions"] is None
    assert Brushstroke.from_dict(d) == _sample_stroke().signed("f" * 64, lambda _: b"s")


def test_rewitnessing_a_fact_dedups_but_timestamps_differ(tmp_path: Path):
    files = DesmataFiles.sandbox(tmp_path, log=TestLoggers())
    first = _sample_stroke()
    again = Brushstroke(
        color=first.color,
        palette=first.palette,
        args=first.args,
        created_at=first.created_at + 5_000,
    )
    assert first.fact() == again.fact()
    assert first.canonical_bytes() != again.canonical_bytes()
    save_brushstrokes(files, [first])
    save_brushstrokes(files, [again])
    # same fact -> one file (the later witness), like the narinfo store
    (loaded,) = load_brushstrokes(files)
    assert loaded == again


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


def test_derivation_closure_is_the_build_graph():
    import desmata.builtins.cell as bc

    nix = Nix(cwd=Path(bc.__file__).parent, log=TestLoggers().proc)
    graph = nix.derivation_closure(".#ipfs")

    # the build closure dwarfs the 4-path runtime closure
    assert len(graph) > 100
    # it includes the shared toolchain (go) — the dedup target across tools
    assert any("go-" in di.name for di in graph.values())
    # and content-addressed sources
    srcs = {s for di in graph.values() for s in di.input_srcs}
    assert srcs
    # the root derivation is the one that outputs the kubo store path
    kubo_root = next(p for p, di in graph.items() if di.name == "kubo-0.28.0")
    assert "out" in graph[kubo_root].outputs


def test_derivations_save_load_round_trip(tmp_path: Path):
    files = DesmataFiles.sandbox(tmp_path, log=TestLoggers())
    graph = {
        "/nix/store/a.drv": DerivationInfo(
            drv_path="/nix/store/a.drv",
            name="a",
            system="aarch64-darwin",
            outputs={"out": "/nix/store/aaa-a"},
            input_drvs=("/nix/store/b.drv",),
            input_srcs=("/nix/store/src",),
        )
    }
    save_derivations(files, "/nix/store/a.drv", graph)
    loaded = load_derivations(files)
    assert loaded["/nix/store/a.drv"] == graph
    # the root is locatable by its output store path
    assert derivation_root(graph, "/nix/store/aaa-a") == "/nix/store/a.drv"
