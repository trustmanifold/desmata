"""The interface witness: the canonical WIT renderer and interface/v1 strokes.

The renderer is a cross-repo contract (SP ROADMAP §3.2): SemanticPaint's
`wit-parse` runner re-derives the same canonical text and holds its sha256
against the type hashes minted here. These tests pin desmata's half of that
contract against the *same* byte-vectors SP's runner tests use — the
`wasm-tools component wit --json` captures of the real gnize/sha256 cells in
test/fixtures/ — so the two implementations can't drift.
"""

import hashlib
import json
from pathlib import Path

from desmata.paint import interface_strokes
from desmata.provenance import SP_EXPORTS, SP_INTERFACE_PALETTE, SP_TYPE_DEF
from desmata.wit import canonical_signature

FIXTURES = Path(__file__).parent / "fixtures"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# Schema-faithful stand-in for `wasm-tools component wit --json` (mirrors SP's
# `canned_wit`): one exported interface, functions exercising primitives, list,
# record, multi-param, names-dropped params, and an unsupported (flags) type.
CANNED_WIT = json.dumps(
    {
        "worlds": [{"exports": {"e": {"interface": {"id": 0}}}}],
        "interfaces": [
            {
                "name": "digester",
                "functions": {
                    "digest": {"params": [{"name": "data", "type": 0}], "result": "string"},
                    "shape": {
                        "params": [{"name": "d", "type": 0}, {"name": "c", "type": "u32"}],
                        "result": 1,
                    },
                    "opaque": {"params": [], "result": 3},
                    "nullary": {"params": []},
                },
            }
        ],
        "types": [
            {"kind": {"list": "u8"}},
            {"kind": {"list": 2}},
            {"kind": {"record": {"fields": [
                {"name": "channel", "type": "u32"},
                {"name": "score", "type": "u8"},
            ]}}},
            {"kind": {"flags": {"flags": []}}},
        ],
    }
)


# --- the renderer (parity with SP's wit_parse_test) --------------------------


def test_renderer_digest_signature():
    assert canonical_signature(CANNED_WIT, "digest") == ("(list<u8>)", "string")


def test_renderer_record_and_multiparam():
    assert canonical_signature(CANNED_WIT, "shape") == (
        "(list<u8>, u32)",
        "list<record { channel: u32, score: u8 }>",
    )


def test_renderer_abstains_on_unsupported_type():
    assert canonical_signature(CANNED_WIT, "opaque") is None


def test_renderer_abstains_on_missing_function():
    assert canonical_signature(CANNED_WIT, "absent") is None


def test_renderer_empty_params_and_no_result():
    # nullary: `()` params, and a missing `result` field renders to "" (which
    # SP's runner also produces, so the hashes still agree)
    assert canonical_signature(CANNED_WIT, "nullary") == ("()", "")


def test_renderer_rejects_non_json():
    assert canonical_signature("not json", "digest") is None


# --- the renderer over REAL wasm-tools output (shared with SP's runner) ------


def test_renderer_matches_real_sha256_component():
    text = (FIXTURES / "sha256_wit.json").read_text()
    assert canonical_signature(text, "digest") == ("(list<u8>)", "string")


def test_renderer_matches_real_gnize_component():
    text = (FIXTURES / "gnize_wit.json").read_text()
    assert canonical_signature(text, "fingerprints") == (
        "(list<u8>, u32, list<u32>, u8)",
        "list<record { channel: u32, width: u32, value: u64, score: u8 }>",
    )


# --- interface_strokes: the interface/v1 projection --------------------------


def test_interface_strokes_mints_type_defs_and_exports():
    strokes = interface_strokes("comp-hash", "digest", CANNED_WIT)
    params_t, result_t = _digest("(list<u8>)"), _digest("string")

    # two type_def preimages and one exports fact, all interface/v1
    assert all(s.palette == SP_INTERFACE_PALETTE for s in strokes)
    type_defs = [s for s in strokes if s.color == SP_TYPE_DEF]
    exports = [s for s in strokes if s.color == SP_EXPORTS]

    assert {s.args for s in type_defs} == {
        (params_t, "(list<u8>)"),
        (result_t, "string"),
    }
    assert [s.args for s in exports] == [("comp-hash", "digest", params_t, result_t)]
    # every type_def carries its own preimage: T is the sha256 of WitText
    for s in type_defs:
        assert s.args[0] == _digest(s.args[1])


def test_interface_strokes_abstains_when_unprojectable():
    # a function the renderer won't project (unsupported result type) mints
    # nothing — never a guessed hash
    assert interface_strokes("comp-hash", "opaque", CANNED_WIT) == []
    assert interface_strokes("comp-hash", "absent", CANNED_WIT) == []


def test_interface_strokes_hashes_match_a_real_component():
    # the gnize fixture end to end: the exports value hashes equal the type_def
    # keys, and those are the sha256 of the canonical texts SP's runner renders
    text = (FIXTURES / "gnize_wit.json").read_text()
    strokes = interface_strokes("gnize-comp", "fingerprints", text)
    (exports,) = [s for s in strokes if s.color == SP_EXPORTS]
    _, _, params_t, result_t = exports.args
    assert params_t == _digest("(list<u8>, u32, list<u32>, u8)")
    assert result_t == _digest(
        "list<record { channel: u32, width: u32, value: u64, score: u8 }>"
    )
