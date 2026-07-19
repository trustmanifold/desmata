"""The native `gn` binary as an oracle for the lightweight-cell pilot tests.

Both proof tests (Test A: foundry path via wasmtime; Test B: browser path via
the runner page) hold their fingerprints against the same native binary,
built from the cell checkout itself -- gnize's Rust workspace lives at
gnize-cell's root (the sha256-cell shape), so the cell's own recipe and
Cargo.lock are the one source of truth about which gnize the pilot is
talking about.
"""

import json
import subprocess
from pathlib import Path


def native_gn(gnize_cell_src: Path) -> Path:
    flake_url = f"git+file://{Path(gnize_cell_src).resolve()}"
    out = subprocess.run(
        [
            "nix", "--extra-experimental-features", "nix-command",
            "--extra-experimental-features", "flakes",
            "build", "--no-link", "--print-out-paths", f"{flake_url}#gnize-cli",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return Path(out) / "bin" / "gn"


def oracle_records(gnize_cell_src: Path, data: bytes, *, channel: int = 0) -> list[dict]:
    """`gn --json --all` on ``data``: every window, default width ladder --
    the parameters the pilot tests use on the wasm side too."""
    out = subprocess.run(
        [str(native_gn(gnize_cell_src)), "--json", "--all", "--channel", str(channel)],
        input=data,
        check=True,
        capture_output=True,
    ).stdout
    return json.loads(out)
