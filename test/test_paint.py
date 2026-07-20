"""Publishing witnessed brushstrokes to a Semantic Paint node.

The peer key is the userspace's single signing identity (keys.py); paint.py
signs the provenance ledger's strokes under it and POSTs them to a node's
``/api/publish``, then requires the node's ``placed`` ids to equal the ids we
derive locally — so every publish round-trips the canonical-serialization
contract (test_provenance.py pins the byte vector itself).

The node here is a stub speaking the real wire shapes; the live-node round
trip is the cross-repo test that lands with the SP-side cell-wasm runner.
"""

import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from desmata import wave
from desmata.exceptions import PublishMismatch
from desmata.fs import DesmataFiles
from desmata.keys import fingerprint, key_path, peer_key
from desmata.log import TestLoggers
from desmata.paint import (
    PUBLISH_PATH,
    PUT_DATA_PATH,
    blob_dir,
    publish_strokes,
    ship_blobs,
    sign_all,
    stash_blob,
    witness_interface,
    witnessed_call,
)
from desmata.provenance import (
    SP_EVALUATES_TO,
    SP_EXPORTS,
    SP_INTERFACE_PALETTE,
    SP_REPRODUCIBILITY_PALETTE,
    SP_TYPE_DEF,
    Brushstroke,
    load_brushstrokes,
    save_brushstrokes,
)
from desmata.wit import WitUnavailable


def _files(tmp_path: Path) -> DesmataFiles:
    return DesmataFiles.sandbox(tmp_path, log=TestLoggers())


def _witnessed() -> list[Brushstroke]:
    return [
        Brushstroke(
            color="builds_to",
            palette="reproducibility/v1",
            args=("dsm:ipfs:nucleus", "dsm:ipfs:artifact"),
            created_at=1_752_200_000_000,
        ),
        Brushstroke(
            color="references",
            palette="reproducibility/v1",
            args=("/nix/store/aaa-kubo", "/nix/store/bbb-a"),
            created_at=1_752_200_000_001,
        ),
    ]


# --- the peer key -----------------------------------------------------------

def test_peer_key_is_minted_once_and_reloads(tmp_path: Path):
    files = _files(tmp_path)
    first = peer_key(files)
    again = peer_key(files)
    # same key both times: one identity per userspace
    assert first.placer == again.placer
    assert first.public_key == again.public_key
    # the placer handle is SP's fingerprint format: hex sha256 of the raw pubkey
    assert first.placer == fingerprint(first.public_key)
    assert len(first.placer) == 64 and set(first.placer) <= set("0123456789abcdef")
    # key material is private to the user
    assert key_path(files).stat().st_mode & 0o777 == 0o600


def test_signed_stroke_verifies_under_the_peer_public_key(tmp_path: Path):
    key = peer_key(_files(tmp_path))
    (signed,) = sign_all(_witnessed()[:1], key)
    assert signed.placer == key.placer
    # raw 64-byte ed25519 over the canonical bytes, as SP's verify_sig checks it
    Ed25519PublicKey.from_public_bytes(key.public_key).verify(
        base64.b64decode(signed.sig), signed.canonical_bytes()
    )


# --- the wire ---------------------------------------------------------------

class _StubNode:
    """A Semantic Paint node's publish surface, minus the node: decodes the
    real request shapes, derives content ids (publish) and blob hashes
    (put_data) the way spd's store does, and echoes them back."""

    def __init__(self, placed_override=None, blob_hash_override=None):
        self.requests: list[dict] = []
        self.blobs: dict[str, bytes] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                decoded = json.loads(body)
                if self.path == PUBLISH_PATH:
                    outer.requests.append(decoded)
                    placed = placed_override or [
                        Brushstroke.from_dict(d).content_id()
                        for d in decoded["brushstrokes"]
                    ]
                    payload = json.dumps({"placed": placed}).encode()
                elif self.path == PUT_DATA_PATH:
                    data = base64.b64decode(decoded["bytes_b64"])
                    digest = blob_hash_override or hashlib.sha256(data).hexdigest()
                    outer.blobs[digest] = data
                    payload = json.dumps(
                        {"ref": {"hash": digest, "suite": "sha256"}}
                    ).encode()
                else:
                    raise AssertionError(f"unexpected POST {self.path}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def test_publish_round_trips_ids_with_the_node(tmp_path: Path):
    files = _files(tmp_path)
    save_brushstrokes(files, _witnessed())
    key = peer_key(files)
    with _StubNode() as node:
        published = publish_strokes(files, key, node.url)

    assert len(published) == 2
    # every stroke went out signed, attributed to this peer
    (request,) = node.requests
    for wire in request["brushstrokes"]:
        assert wire["placer"] == key.placer
        assert wire["suite"] == "v1-ed25519-sha256"
        assert wire["sig"]
        assert set(wire) == {
            "color", "palette", "args", "placer", "created_at",
            "suite", "sig", "arg_versions",
        }
    # and the ids the node acknowledged are the ids we derived
    for p in published:
        assert p.content_id == p.stroke.content_id()


def test_publish_is_idempotent(tmp_path: Path):
    # ed25519 is deterministic and created_at was stamped at mint, so painting
    # twice sends byte-identical strokes -- the node's set-union store dedups
    files = _files(tmp_path)
    save_brushstrokes(files, _witnessed())
    key = peer_key(files)
    with _StubNode() as node:
        first = publish_strokes(files, key, node.url)
        again = publish_strokes(files, key, node.url)
    assert [p.content_id for p in first] == [p.content_id for p in again]
    assert node.requests[0] == node.requests[1]


def test_publish_refuses_a_disagreeing_node(tmp_path: Path):
    files = _files(tmp_path)
    save_brushstrokes(files, _witnessed())
    key = peer_key(files)
    with _StubNode(placed_override=["not", "ours"]) as node:
        with pytest.raises(PublishMismatch):
            publish_strokes(files, key, node.url)


def test_publish_with_empty_ledger_makes_no_request(tmp_path: Path):
    files = _files(tmp_path)
    key = peer_key(files)
    # no server at this url; an attempt to POST would raise
    assert publish_strokes(files, key, "http://127.0.0.1:1") == []


# --- witnessing invocations ---------------------------------------------------


class _ScriptedEngine:
    """An Invoker whose canonical WAVE rendering is scripted: the witness must
    carry the engine's exact bytes, so the test controls what those are."""

    def __init__(self, raw: str = "[7, 9]"):
        self.raw = raw
        self.calls: list[tuple[Path, str, str]] = []

    def invoke_raw(self, component, function, args_wave, *, timeout=None) -> str:
        self.calls.append((Path(component), function, args_wave))
        return self.raw

    def invoke(self, component, function, args, *, timeout=None):
        return wave.decode(
            self.invoke_raw(component, function, wave.encode_args(args))
        )


def test_witnessed_call_mints_an_evaluates_to_claim(tmp_path: Path):
    files = _files(tmp_path)
    component = tmp_path / "component.wasm"
    component.write_bytes(b"pretend-component-bytes")
    engine = _ScriptedEngine(raw="[7, 9]")

    result, stroke = witnessed_call(
        files, engine, component, "fingerprints", [[104, 105], 0, [], 0]
    )

    # the caller sees what plain invoke() would have returned
    assert result == [7, 9]
    # the claim: C = sha256 of the component bytes; F, X exactly as the engine
    # received them; Y exactly as the engine rendered it
    expected_hash = hashlib.sha256(b"pretend-component-bytes").hexdigest()
    assert stroke.color == SP_EVALUATES_TO
    assert stroke.palette == SP_REPRODUCIBILITY_PALETTE
    assert stroke.args == (expected_hash, "fingerprints", "[104, 105], 0, [], 0", "[7, 9]")
    assert engine.calls == [(component, "fingerprints", "[104, 105], 0, [], 0")]
    # witnessed into the ledger, ready for the next paint
    assert stroke in load_brushstrokes(files)
    # and the bytes the claim dereferences sit in the outbox under their hash
    assert (blob_dir(files) / expected_hash).read_bytes() == b"pretend-component-bytes"


# --- witnessing interfaces (interface/v1) -------------------------------------

# A WIT world with one exported function over a list<u8> -> string signature,
# the shape `wasm-tools component wit --json` produces (the real-fixture
# agreement is pinned in test_wit.py).
_WIT_JSON = json.dumps(
    {
        "worlds": [{"exports": {"e": {"interface": {"id": 0}}}}],
        "interfaces": [{"functions": {
            "digest": {"params": [{"name": "data", "type": 0}], "result": "string"},
        }}],
        "types": [{"kind": {"list": "u8"}}],
    }
)


class _StubExtractor:
    """A WitExtractor whose JSON is scripted — the wasm-tools shell-out's
    stand-in, exactly as _ScriptedEngine stands in for the wasmtime CLI."""

    def __init__(self, wit_json: str | None = _WIT_JSON):
        self.wit_json = wit_json
        self.calls: list[Path] = []

    def extract(self, component, *, timeout=None) -> str:
        self.calls.append(Path(component))
        if self.wit_json is None:
            raise WitUnavailable("scripted: no wasm-tools")
        return self.wit_json


def test_witness_interface_mints_interface_strokes(tmp_path: Path):
    files = _files(tmp_path)
    component = tmp_path / "component.wasm"
    component.write_bytes(b"pretend-component-bytes")

    strokes = witness_interface(files, _StubExtractor(), component, "digest")

    params_t = hashlib.sha256(b"(list<u8>)").hexdigest()
    result_t = hashlib.sha256(b"string").hexdigest()
    kinds = {(s.color, s.args) for s in strokes}
    assert kinds == {
        (SP_TYPE_DEF, (params_t, "(list<u8>)")),
        (SP_TYPE_DEF, (result_t, "string")),
        (SP_EXPORTS, (hashlib.sha256(b"pretend-component-bytes").hexdigest(), "digest", params_t, result_t)),
    }
    assert all(s.palette == SP_INTERFACE_PALETTE for s in strokes)
    # witnessed into the ledger and the component stashed for the next paint
    ledger = load_brushstrokes(files)
    assert all(s in ledger for s in strokes)
    expected_hash = hashlib.sha256(b"pretend-component-bytes").hexdigest()
    assert (blob_dir(files) / expected_hash).exists()


def test_witness_interface_abstains_when_extractor_unavailable(tmp_path: Path):
    files = _files(tmp_path)
    component = tmp_path / "component.wasm"
    component.write_bytes(b"pretend-component-bytes")

    # no wasm-tools (WitUnavailable): mint nothing, don't raise — the component
    # bytes are still stashed (a later extractor could witness them)
    assert witness_interface(files, _StubExtractor(wit_json=None), component, "digest") == []
    assert load_brushstrokes(files) == []
    assert (blob_dir(files) / hashlib.sha256(b"pretend-component-bytes").hexdigest()).exists()


# --- shipping blobs -----------------------------------------------------------


def test_ship_blobs_round_trips_content_addressing(tmp_path: Path):
    files = _files(tmp_path)
    a = stash_blob(files, b"component-a")
    b = stash_blob(files, b"component-b")
    with _StubNode() as node:
        shipped = ship_blobs(files, node.url)
        again = ship_blobs(files, node.url)
    assert sorted(shipped) == sorted([a, b]) == sorted(again)
    assert node.blobs[a] == b"component-a"
    assert node.blobs[b] == b"component-b"


def test_ship_blobs_refuses_a_disagreeing_node(tmp_path: Path):
    files = _files(tmp_path)
    stash_blob(files, b"component-a")
    with _StubNode(blob_hash_override="not-sha256-of-the-bytes") as node:
        with pytest.raises(PublishMismatch):
            ship_blobs(files, node.url)


def test_ship_blobs_with_empty_outbox_makes_no_request(tmp_path: Path):
    files = _files(tmp_path)
    # no server at this url; an attempt to POST would raise
    assert ship_blobs(files, "http://127.0.0.1:1") == []


def test_witness_then_paint_lands_claim_and_component_on_the_node(tmp_path: Path):
    # the full desmata half of the verification story: dsm call (witness) then
    # dsm paint (ship blobs + publish strokes), against the stub node
    files = _files(tmp_path)
    component = tmp_path / "component.wasm"
    component.write_bytes(b"pretend-component-bytes")
    _, stroke = witnessed_call(
        files, _ScriptedEngine(), component, "fingerprints", [[104, 105], 0, [], 0]
    )
    key = peer_key(files)
    with _StubNode() as node:
        shipped = ship_blobs(files, node.url)
        published = publish_strokes(files, key, node.url)

    # the node holds the component bytes under the hash the claim carries
    assert shipped == [stroke.args[0]]
    assert node.blobs[stroke.args[0]] == b"pretend-component-bytes"
    # and the signed claim itself, attributed to this peer
    (request,) = node.requests
    (wire,) = request["brushstrokes"]
    assert wire["color"] == SP_EVALUATES_TO
    assert tuple(wire["args"]) == stroke.args
    assert wire["placer"] == key.placer
    assert (published[0].content_id) == published[0].stroke.content_id()
