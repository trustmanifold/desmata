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
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from desmata.exceptions import PublishMismatch
from desmata.fs import DesmataFiles
from desmata.keys import fingerprint, key_path, peer_key
from desmata.log import TestLoggers
from desmata.paint import PUBLISH_PATH, publish_strokes, sign_all
from desmata.provenance import Brushstroke, save_brushstrokes


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
    real request shape, derives content ids the way spd's store does (sha256
    over canonical bytes), and echoes them as ``placed``."""

    def __init__(self, placed_override=None):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                assert self.path == PUBLISH_PATH
                body = self.rfile.read(int(self.headers["Content-Length"]))
                decoded = json.loads(body)
                outer.requests.append(decoded)
                placed = placed_override or [
                    Brushstroke.from_dict(d).content_id()
                    for d in decoded["brushstrokes"]
                ]
                payload = json.dumps({"placed": placed}).encode()
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
