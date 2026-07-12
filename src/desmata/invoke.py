"""The invoker seam: call a function on a wasm component, typed values in and
out.

For lightweight cells (agent_primers/lightweight-cells.md) the canonical
callable surface is the component's WIT world, so desmata needs exactly one
new capability: ``invoke(component_path, function, args) -> value``. That seam
matters more than any implementation behind it -- it is what a future
``wasmtime-py`` backend, a browser runner, or an ember-tier interpreter each
re-implement without the rest of desmata noticing.

The first implementation shells out to the ``wasmtime`` CLI
(``run --invoke``), with values crossing the boundary as WAVE
(:mod:`desmata.wave`). Spike outcome, 2026-07: ``wasmtime-py`` is not among
desmata's locked dependencies and its component-model bindgen maturity is the
open question, while the CLI path is proven end to end (hello-wasm's flake
check, SemanticPaint's gnize-wasm-invoke check). The CLI costs a subprocess
per call, which is fine for the foundry tier -- the only tier that runs
Python at all.

``wasmtime`` itself is acquired the way desmata acquires ipfs: built with nix
from a flake. By convention a lightweight cell's flake exports a ``wasmtime``
package (the cell's flake.lock -- part of its nucleus -- thereby pins the
runner version its author vouches for); :func:`build_invoker` builds it from
the cell dir.
"""

from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from desmata import wave
from desmata.cell_utils import get_nix
from desmata.lower_protocols import CellContext
from desmata.tool import Tool

# generous by default: component calls are pure compute, but a foundry would
# rather see a hang become an error than wait on it forever
DEFAULT_INVOKE_TIMEOUT = 60.0


@runtime_checkable
class Invoker(Protocol):
    """Anything that can call a function on a wasm component."""

    def invoke(
        self,
        component: Path,
        function: str,
        args: Sequence[Any],
        *,
        timeout: float | None = DEFAULT_INVOKE_TIMEOUT,
    ) -> Any:
        """Call ``function`` exported by the component at ``component`` with
        ``args`` (JSON-shaped Python values), returning the JSON-shaped result.
        The mapping between those values and WIT values is canonical and lives
        in :mod:`desmata.wave`."""
        raise NotImplementedError()

    def invoke_raw(
        self,
        component: Path,
        function: str,
        args_wave: str,
        *,
        timeout: float | None = DEFAULT_INVOKE_TIMEOUT,
    ) -> str:
        """Like :meth:`invoke`, but the arguments arrive as a ready WAVE
        argument list (:func:`desmata.wave.encode_args`) and the result is the
        engine's canonical WAVE rendering, stripped, undecoded. This is the
        form an ``evaluates_to(C, F, X, Y)`` witness carries: Y must be the
        engine's own bytes, not a desmata re-encoding, because a verifier
        holds its engine's rendering against Y byte-for-byte."""
        raise NotImplementedError()


class WasmtimeCli(Tool, Invoker):
    """The wasmtime CLI as a desmata tool and the reference :class:`Invoker`.

    Sandboxing is the point: the component gets no filesystem, no network, and
    no env unless explicitly granted (desmata grants none), so invoking a
    stranger's lightweight cell is a far lower trust bar than executing their
    ``cell.py``."""

    def __init__(self, root: Path, context: CellContext):
        loggers = context.loggers.specialize("wasmtime")
        bin_dir = root / "bin"
        super().__init__(
            name="wasmtime",
            path=bin_dir / "wasmtime",
            log=loggers.proc,
            env_filter=context.get_env_filter(exec_path=bin_dir),
        )

    def invoke(
        self,
        component: Path,
        function: str,
        args: Sequence[Any],
        *,
        timeout: float | None = DEFAULT_INVOKE_TIMEOUT,
    ) -> Any:
        raw = self.invoke_raw(
            component, function, wave.encode_args(args), timeout=timeout
        )
        return wave.decode(raw)

    def invoke_raw(
        self,
        component: Path,
        function: str,
        args_wave: str,
        *,
        timeout: float | None = DEFAULT_INVOKE_TIMEOUT,
    ) -> str:
        expr = f"{function}({args_wave})"
        # -C cache=n: no cache dir writes, so calls are hermetic wherever HOME
        # points (the same flags the flake checks pin down).
        out = self(
            "run", "-C", "cache=n", "--invoke", expr, str(Path(component).resolve()),
            timeout=timeout,
        )
        return out.strip()


def build_invoker(context: CellContext) -> WasmtimeCli:
    """Build the cell's pinned wasmtime (its flake's ``wasmtime`` output) and
    wrap it as an :class:`Invoker`. Foundry-only, like everything nix."""
    root, _ = get_nix(context).build("wasmtime")
    return WasmtimeCli(root=root, context=context)
