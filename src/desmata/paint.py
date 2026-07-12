"""Publish witnessed brushstrokes to a Semantic Paint node.

This is the desmata side of the SP trust layer's ingestion path
(``agent_primers/semantic-paint-trust-layer.md``): take the locally-witnessed
strokes from the provenance ledger (:mod:`desmata.provenance`), sign each
under the peer key (:mod:`desmata.keys`), and POST them to an SP node's
``/api/publish``. The node stores strokes that arrive signed *as they are* --
attributed to this peer's placer fingerprint, not re-signed as the node.

The node's ``placed`` reply carries the content ids *it* derived (sha256 over
its canonical bytes, ``spd/core/canonical.gleam``); we recompute ours and
require an exact match. Every publish therefore round-trips the
canonical-serialization contract that signature verification depends on --
drift raises :class:`~desmata.exceptions.PublishMismatch` instead of silently
storing strokes nobody can verify.

Publishing is idempotent: Ed25519 is deterministic and ``created_at`` was
stamped at mint, so re-publishing yields byte-identical strokes and the node's
set-union store lands them on the same ids.

Blobs the strokes refer to (e.g. the wasm component behind a ``builds_to``)
are not shipped here: a verifier dereferences those content-addressed, which
arrives with the SP-side ``cell-wasm`` verify runner.
"""

import json
import urllib.request
from dataclasses import dataclass

from desmata.exceptions import PublishMismatch
from desmata.keys import PeerKey
from desmata.lower_protocols import UserspaceFiles
from desmata.provenance import Brushstroke, load_brushstrokes

# The SP node admin surface's publish path (SemanticPaint's generated
# publish service; routed in spd/node/web.gleam as ["api", "publish"]).
PUBLISH_PATH = "/api/publish"


@dataclass(frozen=True)
class Published:
    """One stroke as it went over the wire, with its node-confirmed id."""

    stroke: Brushstroke  # the signed wire form
    content_id: str  # confirmed equal on both sides


def sign_all(strokes: list[Brushstroke], key: PeerKey) -> list[Brushstroke]:
    """Stamp and sign witnessed strokes for the wire (placer = the peer's SP
    fingerprint; suite = SP's v1-ed25519-sha256)."""
    return [s.signed(key.placer, key.sign) for s in strokes]


def publish_strokes(
    files: UserspaceFiles,
    key: PeerKey,
    node_url: str,
    timeout: float = 30.0,
) -> list[Published]:
    """Sign every witnessed brushstroke in the ledger and publish it to the
    SP node at ``node_url``. Returns one :class:`Published` per stroke, in
    ledger order; empty ledger publishes nothing."""
    strokes = load_brushstrokes(files)
    if not strokes:
        return []
    signed = sign_all(strokes, key)
    placed = post_publish(node_url, signed, timeout=timeout)
    expected = [s.content_id() for s in signed]
    if placed != expected:
        raise PublishMismatch(
            f"node at {node_url} placed {placed}, expected {expected}: "
            "canonical serializations have drifted (see "
            "provenance.Brushstroke.canonical_bytes vs spd/core/canonical.gleam)"
        )
    return [Published(stroke=s, content_id=i) for s, i in zip(signed, expected)]


def post_publish(
    node_url: str, strokes: list[Brushstroke], timeout: float = 30.0
) -> list[str]:
    """POST signed strokes to the node's publish endpoint; returns the ids the
    node placed them under (in request order, as spd's store fold preserves)."""
    body = json.dumps(
        {"brushstrokes": [s.to_dict() for s in strokes]},
        separators=(",", ":"),
    ).encode()
    request = urllib.request.Request(
        node_url.rstrip("/") + PUBLISH_PATH,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())["placed"]
