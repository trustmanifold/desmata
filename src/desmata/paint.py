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

Blobs the strokes refer to travel beside them: witnessing an invocation
(:func:`witnessed_call`) stashes the component bytes in a content-addressed
outbox, and :func:`ship_blobs` POSTs the outbox to the node's
``/api/put_data`` so its ``cell-wasm`` verify runner can dereference the
claim's component hash and re-execute. (``builds_to`` artifacts are *not*
shipped: their runner is ``nix`` — rebuild the recipe — which no SP node
implements yet, so shipping bytes nobody dereferences would be noise.)
"""

import base64
import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from desmata import wave, wit
from desmata.exceptions import PublishMismatch
from desmata.invoke import Invoker
from desmata.keys import PeerKey
from desmata.lower_protocols import UserspaceFiles
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
from desmata.wit import WitExtractor, WitUnavailable

# The SP node admin surface's publish path (SemanticPaint's generated
# publish service; routed in spd/node/web.gleam as ["api", "publish"]).
PUBLISH_PATH = "/api/publish"

# Its content-addressed blob intake (same surface; returns the sha256 the
# node derived from the bytes it received).
PUT_DATA_PATH = "/api/put_data"


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


# --- witnessing invocations, and the blob outbox ----------------------------


def blob_dir(files: UserspaceFiles) -> Path:
    """The outbox: bytes that published claims dereference, keyed by the
    sha256-hex the claims carry (the same key SP's blob store uses)."""
    return files.data / "paint" / "blobs"


def stash_blob(files: UserspaceFiles, data: bytes) -> str:
    """Content-address ``data`` into the outbox; returns its sha256 hex."""
    digest = hashlib.sha256(data).hexdigest()
    directory = blob_dir(files)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / digest
    if not path.exists():
        path.write_bytes(data)
    return digest


def witnessed_call(
    files: UserspaceFiles,
    invoker: Invoker,
    component: Path,
    function: str,
    args: Sequence[Any],
) -> tuple[Any, Brushstroke]:
    """Invoke a lightweight-cell function and witness the act as an
    ``evaluates_to(C, F, X, Y)`` claim in the provenance ledger.

    C is the sha256 of the component bytes (stashed in the outbox so a later
    ``dsm paint`` ships them where a verifier can dereference C); F, X, Y are
    the function, the WAVE argument list exactly as the engine received it,
    and the engine's canonical WAVE result exactly as it printed it. A
    verifier's own engine must reproduce Y byte-for-byte (the color's
    determinism policy is exact-hash — a zero-import component call is
    bit-deterministic), so nothing here is re-encoded.

    Returns the decoded result (what plain ``invoke`` would have returned)
    and the witnessed stroke."""
    data = Path(component).read_bytes()
    component_hash = stash_blob(files, data)
    args_wave = wave.encode_args(args)
    raw = invoker.invoke_raw(component, function, args_wave)
    stroke = Brushstroke(
        color=SP_EVALUATES_TO,
        palette=SP_REPRODUCIBILITY_PALETTE,
        args=(component_hash, function, args_wave, raw),
        created_at=Brushstroke.now_ms(),
    )
    save_brushstrokes(files, [stroke])
    return wave.decode(raw), stroke


# --- witnessing interfaces (the interface/v1 palette) ------------------------


def interface_strokes(
    component_hash: str, function: str, wit_json: str
) -> list[Brushstroke]:
    """Project a component's WIT world into ``interface/v1`` strokes for one
    exported ``function`` (SP ROADMAP §3.2). Given the component's sha256 (the
    same C ``evaluates_to`` carries) and its ``wasm-tools component wit --json``
    text, mint:

    - ``type_def(sha256(ParamsText), ParamsText)`` and
      ``type_def(sha256(ResultText), ResultText)`` — each type carries its own
      canonical WIT preimage, so any node confirms the hash offline;
    - ``exports(C, F, ParamsT, ResultT)`` — the wiring fact, joined SP-side into
      ``compatible`` over shared type hashes.

    Pure and abstaining: returns ``[]`` when :func:`desmata.wit.
    canonical_signature` can't project this function (missing, or a type the
    renderer won't guess). The hashes match SP's ``wit-parse`` runner because
    both render the same version-pinned ``wasm-tools`` output the same way."""
    signature = wit.canonical_signature(wit_json, function)
    if signature is None:
        return []
    params_text, result_text = signature
    params_t = hashlib.sha256(params_text.encode()).hexdigest()
    result_t = hashlib.sha256(result_text.encode()).hexdigest()
    minted = Brushstroke.now_ms()

    def type_def(digest: str, text: str) -> Brushstroke:
        return Brushstroke(
            color=SP_TYPE_DEF,
            palette=SP_INTERFACE_PALETTE,
            args=(digest, text),
            created_at=minted,
        )

    return [
        type_def(params_t, params_text),
        type_def(result_t, result_text),
        Brushstroke(
            color=SP_EXPORTS,
            palette=SP_INTERFACE_PALETTE,
            args=(component_hash, function, params_t, result_t),
            created_at=minted,
        ),
    ]


def witness_interface(
    files: UserspaceFiles,
    extractor: WitExtractor,
    component: Path,
    function: str,
) -> list[Brushstroke]:
    """Witness a component's interface for ``function`` into the provenance
    ledger, beside the ``evaluates_to`` claim :func:`witnessed_call` mints.

    Extracts the WIT world with ``extractor`` (the ``wasm-tools`` seam), renders
    :func:`interface_strokes`, saves them, and stashes the component bytes in
    the outbox under C so a later ``dsm paint`` ships them where the ``wit-parse``
    runner can dereference the claim (like ``evaluates_to``, and idempotent when
    ``witnessed_call`` already stashed them). Best-effort: extraction failure
    (:class:`WitUnavailable`) or an unprojectable interface mints nothing and
    returns ``[]`` — the *absent → trust fallback* posture, never a failed call.

    ``dsm publish`` (a cell never called locally) can call this alone to mint
    interface facts without an accompanying evaluation."""
    data = Path(component).read_bytes()
    component_hash = stash_blob(files, data)
    try:
        wit_json = extractor.extract(component)
    except WitUnavailable:
        return []
    strokes = interface_strokes(component_hash, function, wit_json)
    save_brushstrokes(files, strokes)
    return strokes


def ship_blobs(files: UserspaceFiles, node_url: str, timeout: float = 30.0) -> list[str]:
    """POST every outbox blob to the node's put_data endpoint; returns the
    shipped hashes. The node re-derives each blob's sha256 from the bytes it
    received and we require it to equal the outbox key — content addressing
    round-tripped, like publish round-trips content ids. Idempotent: the
    node's blob store is a plain hash-keyed insert."""
    directory = blob_dir(files)
    if not directory.exists():
        return []
    shipped: list[str] = []
    for path in sorted(directory.iterdir()):
        placed = post_put_data(node_url, path.read_bytes(), timeout=timeout)
        if placed != path.name:
            raise PublishMismatch(
                f"node at {node_url} stored a blob we address as {path.name} "
                f"under {placed}: one of us is not hashing sha256 over the bytes"
            )
        shipped.append(placed)
    return shipped


def post_put_data(node_url: str, data: bytes, timeout: float = 30.0) -> str:
    """POST bytes to the node's put_data endpoint; returns the hash of the
    DataRef the node stored them under."""
    body = json.dumps(
        {"bytes_b64": base64.b64encode(data).decode()},
        separators=(",", ":"),
    ).encode()
    request = urllib.request.Request(
        node_url.rstrip("/") + PUT_DATA_PATH,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())["ref"]["hash"]
