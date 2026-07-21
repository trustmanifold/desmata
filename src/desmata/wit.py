"""The interface witness: project a component's WIT world into gossip.

Every lightweight cell (``agent_primers/lightweight-cells.md``) already carries
its interface — the WIT world is embedded in the pinned component bytes and
mechanically extractable (``wasm-tools component wit``). WIT *is* the
language-agnostic type layer, so desmata does not invent a metadata format; it
**witnesses** the interface the same way :mod:`desmata.paint` witnesses an
``evaluates_to`` evaluation (``agent_primers/interface-palette.md``).

Two halves, split exactly as :mod:`desmata.invoke` splits WAVE from the CLI:

* the **seam** (:class:`WitExtractor`) shells ``wasm-tools component wit
  --json`` — one subprocess, read-only, zero-capability, foundry-only, the
  cell's flake pins the ``wasm-tools`` version its author vouches for (as it
  pins ``wasmtime`` for :mod:`desmata.invoke`);
* the **pure renderer** (:func:`canonical_signature`) turns that JSON into the
  canonical type text an ``exports`` stroke hashes over.

The renderer is the load-bearing contract. SemanticPaint's ``wit-parse`` runner
(``node/src/spd/verify/wit.gleam``) re-derives the very same text and holds its
sha256 against the ``type_def`` hashes a witness minted. Because both sides run
the *same version-pinned* ``wasm-tools`` and render by the *same* rules, the
hashes agree byte-for-byte — the discipline ``wave.py``/``invoke.py`` keep for
values, kept here for types. The canonical projection is pinned in SP
``docs/design/interface_palette.md`` §4:

* the exported function is found by its **bare name** (as in ``evaluates_to``);
* **param names are dropped** — compatibility is structural — leaving a
  parenthesized, comma-separated tuple ``(t1, t2, ...)`` (empty ``()``);
* a resolved type renders as a **primitive** verbatim, ``list<T>``, or
  ``record { field: T, ... }`` in declaration order;
* **anything else abstains** (option/result/tuple/variant/enum/flags/handles,
  or a name resolving to a non-primitive alias) — the renderer returns
  ``None`` and the witness mints nothing, never a guessed encoding. Same rule
  ``wave.encode`` follows and ``verify.gleam``'s ``encode_wave`` follows.

Cross-repo agreement is proven by shared byte-vectors: ``test/test_wit.py``
runs this renderer over ``wasm-tools --json`` captures of the real gnize/sha256
cells (``test/fixtures/*_wit.json``, the same fixtures SP's runner tests use)
and asserts the same canonical texts.
"""

import json
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

from desmata.cell_utils import get_nix
from desmata.lower_protocols import CellContext
from desmata.tool import Tool

# generous by default: extraction is a read-only parse of already-built bytes,
# but a foundry would rather see a hang become an error than wait forever
DEFAULT_WIT_TIMEOUT = 30.0

# The primitive WIT type keywords rendered verbatim. Kept byte-identical to
# SP's `is_primitive` (node/src/spd/verify/wit.gleam) — the two lists are one
# contract.
_PRIMITIVES = frozenset(
    {
        "bool", "s8", "u8", "s16", "u16", "s32", "u32", "s64", "u64",
        "f32", "f64", "char", "string",
    }
)

# sentinel: a function whose `result` field is absent or null (no result)
_NO_RESULT = object()


class WitUnavailable(Exception):
    """The WIT world could not be extracted — no ``wasm-tools`` (the cell's
    flake doesn't pin it), or the bytes are not a parseable component. Interface
    witnessing abstains rather than failing the call: the same *absent → trust
    fallback* posture SP's node takes when ``wasm-tools`` is off its PATH
    (``spd_wit_ffi`` → ``ToolUnavailable`` → ``unavailable``)."""


# --- the pure renderer ------------------------------------------------------


def canonical_signature(json_text: str, function: str) -> tuple[str, str] | None:
    """Canonical ``(ParamsT text, ResultT text)`` for the exported ``function``
    in the ``wasm-tools component wit --json`` output ``json_text``.

    Returns ``None`` (abstain) on a parse failure, a missing function, or any
    type the renderer cannot inline faithfully — the caller then mints nothing.
    Param names are dropped; a missing/null result renders to ``""`` (which
    SP's runner also produces, so the hashes still agree). A **single** param
    renders as its bare type (no wrapping parens): WIT has no 1-tuples, so a
    unary function's input type *is* that type, which lets a producer's ResultT
    hash-equal a unary consumer's ParamsT (the ``compatible`` join). Mirrors
    SP's ``wit.gleam`` ``render_params`` byte-for-byte."""
    try:
        wit = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(wit, dict):
        return None
    worlds, interfaces, types = wit.get("worlds"), wit.get("interfaces"), wit.get("types")
    if not (isinstance(worlds, list) and isinstance(interfaces, list) and isinstance(types, list)):
        return None

    found = _find_function(function, _export_ids(worlds), interfaces)
    if found is None:
        return None
    params, result = found

    param_texts: list[str] = []
    for ref in params:
        text = _render_ref(ref, types)
        if text is None:
            return None
        param_texts.append(text)

    if result is _NO_RESULT:
        result_text = ""
    else:
        result_text = _render_ref(result, types)
        if result_text is None:
            return None

    if len(param_texts) == 1:
        params_text = param_texts[0]
    else:
        params_text = "(" + ", ".join(param_texts) + ")"
    return params_text, result_text


def _export_ids(worlds: list) -> list[int]:
    """The interface ids the first world exports (an ``exports`` map entry of
    shape ``{"interface": {"id": N}}``); func/type exports are ignored, matching
    SP's ``world_exports_decoder``."""
    if not worlds:
        return []
    first = worlds[0]
    exports = first.get("exports") if isinstance(first, dict) else None
    if not isinstance(exports, dict):
        return []
    ids: list[int] = []
    for entry in exports.values():
        if isinstance(entry, dict):
            iface = entry.get("interface")
            if isinstance(iface, dict) and isinstance(iface.get("id"), int):
                ids.append(iface["id"])
    return ids


def _find_function(function: str, export_ids: list[int], interfaces: list):
    """The first exported interface carrying ``function``, as ``(params,
    result)``: params a list of type refs (names dropped), result a type ref or
    the ``_NO_RESULT`` sentinel. ``None`` if no export defines it."""
    for iid in export_ids:
        if 0 <= iid < len(interfaces):
            iface = interfaces[iid]
            functions = iface.get("functions") if isinstance(iface, dict) else None
            if isinstance(functions, dict) and function in functions:
                fn = functions[function]
                params = [p.get("type") for p in fn.get("params", []) if isinstance(p, dict)]
                raw_result = fn.get("result")
                result = raw_result if raw_result is not None else _NO_RESULT
                return params, result
    return None


def _render_ref(ref, types: list) -> str | None:
    """A type reference — a primitive keyword (``"u8"``) or an index into the
    ``types`` table — rendered to canonical text, or ``None`` to abstain."""
    if isinstance(ref, bool):  # bool is an int subclass; never a valid ref
        return None
    if isinstance(ref, int):
        if 0 <= ref < len(types):
            entry = types[ref]
            if isinstance(entry, dict):
                return _render_kind(entry.get("kind"), types)
        return None
    if isinstance(ref, str):
        return ref if ref in _PRIMITIVES else None
    return None


def _render_kind(kind, types: list) -> str | None:
    """A ``types`` table entry's ``kind`` rendered to canonical text. Supported:
    a bare-string alias to a primitive, ``list<T>``, and ``record { f: T, ...
    }`` in field order; everything else abstains (``None``)."""
    if isinstance(kind, str):  # `type foo = bar`: renders iff bar is primitive
        return kind if kind in _PRIMITIVES else None
    if isinstance(kind, dict):
        if "list" in kind:
            inner = _render_ref(kind["list"], types)
            return None if inner is None else f"list<{inner}>"
        record = kind.get("record")
        if isinstance(record, dict) and isinstance(record.get("fields"), list):
            parts: list[str] = []
            for field in record["fields"]:
                if not isinstance(field, dict):
                    return None
                name, text = field.get("name"), _render_ref(field.get("type"), types)
                if not isinstance(name, str) or text is None:
                    return None
                parts.append(f"{name}: {text}")
            return "record { " + ", ".join(parts) + " }"
    return None


# --- the extraction seam ----------------------------------------------------


@runtime_checkable
class WitExtractor(Protocol):
    """Anything that can resolve a component's WIT world to ``wasm-tools
    component wit --json`` text. The seam matters more than the impl behind it:
    a ``wasm-tools``-less browser or ember tier re-implements this without the
    rest of desmata noticing, exactly as :class:`desmata.invoke.Invoker`
    abstracts the runner."""

    def extract(self, component: Path, *, timeout: float | None = DEFAULT_WIT_TIMEOUT) -> str:
        """Return the ``--json`` WIT rendering of the component at
        ``component``. Raises :class:`WitUnavailable` when the tool is absent or
        the bytes are not a parseable component."""
        raise NotImplementedError()


class WasmToolsCli(Tool, WitExtractor):
    """The ``wasm-tools`` CLI as a desmata tool and the reference
    :class:`WitExtractor`.

    Extraction is a read-only parse, never an execution — no filesystem,
    network, or env is granted beyond the component path handed in, an even
    lower trust bar than :class:`desmata.invoke.WasmtimeCli`."""

    def __init__(self, root: Path, context: CellContext):
        loggers = context.loggers.specialize("wasm-tools")
        bin_dir = root / "bin"
        super().__init__(
            name="wasm-tools",
            path=bin_dir / "wasm-tools",
            log=loggers.proc,
            env_filter=context.get_env_filter(exec_path=bin_dir),
        )

    def extract(self, component: Path, *, timeout: float | None = DEFAULT_WIT_TIMEOUT) -> str:
        try:
            return self(
                "component", "wit", "--json", str(Path(component).resolve()),
                timeout=timeout,
            )
        except subprocess.CalledProcessError as e:
            # not a component, or a tool error — abstain, don't crash the call
            raise WitUnavailable(f"wasm-tools could not extract WIT: {e}") from e


def build_wit_extractor(context: CellContext) -> WasmToolsCli:
    """Build the cell's pinned ``wasm-tools`` (its flake's ``wasm-tools``
    output) and wrap it as a :class:`WitExtractor`. Foundry-only, like
    :func:`desmata.invoke.build_invoker`. Raises if the cell's flake exports no
    ``wasm-tools`` — callers treat that as *no extractor* (interface witnessing
    is skipped, the ``evaluates_to`` witness is unaffected)."""
    root, _ = get_nix(context).build("wasm-tools")
    return WasmToolsCli(root=root, context=context)
