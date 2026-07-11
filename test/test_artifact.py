"""Lightweight-cell machinery, fast half: artifact pins parse and verify with
an arbitrary file standing in for the blob, the WasmComponent resolution order
runs against a faked build step, publish refuses a lying cell, and the WAVE
codec round-trips the canonical JSON<->WIT mapping. No wasm is executed here;
the wasm-executing half is the marked gnize-cell test (test_gnize_cell.py).
"""

import hashlib
import shutil
from pathlib import Path
from types import SimpleNamespace

import desmata.artifact as artifact_mod
import desmata.samples.greeter.cell as greeter
import pytest
from desmata import wave
from desmata.artifact import WasmComponent
from desmata.builtins.cell import DesmataBuiltins
from desmata.cell_archive import InvalidCell, artifact_pins, publish_cell, verify_artifacts
from desmata.content import Backend, Hash
from desmata.exceptions import ArtifactPinMismatch, CellUnavailable
from desmata.provenance import SP_BUILDS_TO

GREETER_DIR = Path(greeter.__file__).parent


class FakeHasher:
    """Content-addresses by sha256 -- deterministic, no ipfs involved."""

    backend = Backend.ipfs

    def hash_path(self, path: Path) -> Hash:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return Hash(backend=Backend.ipfs, digest=digest)

    def dag_put(self, obj):  # pragma: no cover - only here to satisfy _dag_capable
        raise NotImplementedError()


def fake_pin(data: bytes) -> Hash:
    return Hash(backend=Backend.ipfs, digest=hashlib.sha256(data).hexdigest())


# --- artifact manifest: parse ------------------------------------------------

def test_artifact_pins_parses_paths_comments_and_blanks(tmp_path: Path):
    (tmp_path / "artifact").write_text(
        "# path  hash\n"
        "\n"
        "gnize_wasm.wasm  dsm:ipfs:QmAAA\n"
        "nested/other.wasm  dsm:ipfs:QmBBB\n"
    )
    pins = artifact_pins(tmp_path)
    assert pins == {
        "gnize_wasm.wasm": Hash(backend=Backend.ipfs, digest="QmAAA"),
        "nested/other.wasm": Hash(backend=Backend.ipfs, digest="QmBBB"),
    }


def test_artifact_pins_empty_without_manifest(tmp_path: Path):
    assert artifact_pins(tmp_path) == {}


def test_artifact_pins_rejects_malformed_lines(tmp_path: Path):
    (tmp_path / "artifact").write_text("just-a-path-no-hash\n")
    with pytest.raises(InvalidCell, match="relative-path"):
        artifact_pins(tmp_path)


# --- artifact manifest: verify ----------------------------------------------

def test_verify_artifacts_accepts_true_pins(tmp_path: Path):
    blob = b"any bytes at all"
    (tmp_path / "blob.wasm").write_bytes(blob)
    (tmp_path / "artifact").write_text(f"blob.wasm  {fake_pin(blob)}\n")
    pins = verify_artifacts(FakeHasher(), tmp_path)
    assert pins == {"blob.wasm": fake_pin(blob)}


def test_verify_artifacts_rejects_mismatched_blob(tmp_path: Path):
    (tmp_path / "blob.wasm").write_bytes(b"actual bytes")
    (tmp_path / "artifact").write_text(f"blob.wasm  {fake_pin(b'pinned bytes')}\n")
    with pytest.raises(ArtifactPinMismatch, match="blob.wasm"):
        verify_artifacts(FakeHasher(), tmp_path)


def test_verify_artifacts_rejects_missing_blob(tmp_path: Path):
    (tmp_path / "artifact").write_text(f"blob.wasm  {fake_pin(b'anything')}\n")
    with pytest.raises(ArtifactPinMismatch, match="no such file"):
        verify_artifacts(FakeHasher(), tmp_path)


def test_verify_artifacts_no_manifest_is_fine(tmp_path: Path):
    assert verify_artifacts(FakeHasher(), tmp_path) == {}


# --- WasmComponent resolution order (build step faked) -----------------------

BLOB = b"\0asm pretend component"
FAKE_NUCLEUS = Hash(backend=Backend.ipfs, digest="QmNucleus")


class Component(WasmComponent):
    artifact_path = "component.wasm"
    flake_output = "component"
    built_path = "lib/component.wasm"


def make_cell(tmp_path: Path, *, blob: bytes | None, pin: bytes = BLOB) -> SimpleNamespace:
    cell_dir = tmp_path / "cell"
    cell_dir.mkdir()
    (cell_dir / "artifact").write_text(f"component.wasm  {fake_pin(pin)}\n")
    if blob is not None:
        (cell_dir / "component.wasm").write_bytes(blob)
    return SimpleNamespace(cell_dir=cell_dir)


def fake_build(tmp_path: Path, monkeypatch, *, output: bytes):
    """Make the recipe path produce ``output`` without touching nix."""
    built_root = tmp_path / "store" / "component"
    (built_root / "lib").mkdir(parents=True)
    (built_root / "lib" / "component.wasm").write_bytes(output)

    calls = []

    def get_nix(context):
        def build(flake_output):
            calls.append(flake_output)
            return built_root, []

        return SimpleNamespace(build=build)

    monkeypatch.setattr(artifact_mod, "get_nix", get_nix)
    monkeypatch.setattr(artifact_mod, "nucleus_hash", lambda hasher, cell_dir: FAKE_NUCLEUS)
    monkeypatch.setattr(artifact_mod.shutil, "which", lambda name: "/fake/bin/nix")
    return calls


def test_present_verified_blob_wins_without_any_build(tmp_path: Path, monkeypatch):
    context = make_cell(tmp_path, blob=BLOB)
    # if the build path were taken, this would blow up loudly
    monkeypatch.setattr(
        artifact_mod, "get_nix", lambda c: (_ for _ in ()).throw(AssertionError("built!"))
    )
    dep = Component.build_or_get(context, FakeHasher())
    assert Path(dep.root) == context.cell_dir / "component.wasm"
    assert dep.hash == fake_pin(BLOB)
    assert dep.witnessed == []


def test_missing_blob_builds_verifies_and_mints_builds_to(tmp_path: Path, monkeypatch):
    context = make_cell(tmp_path, blob=None)
    calls = fake_build(tmp_path, monkeypatch, output=BLOB)

    dep = Component.build_or_get(context, FakeHasher())

    assert calls == ["component"]
    # the verified bytes were materialized where the pin says they live
    assert (context.cell_dir / "component.wasm").read_bytes() == BLOB
    assert Path(dep.root) == context.cell_dir / "component.wasm"
    # the build minted the locally-witnessed builds_to(nucleus, artifact)
    (stroke,) = dep.witnessed
    assert stroke.color == SP_BUILDS_TO
    assert stroke.args == (str(FAKE_NUCLEUS), str(fake_pin(BLOB)))


def test_mismatched_blob_is_replaced_via_the_recipe(tmp_path: Path, monkeypatch):
    context = make_cell(tmp_path, blob=b"tampered bytes")
    fake_build(tmp_path, monkeypatch, output=BLOB)
    dep = Component.build_or_get(context, FakeHasher())
    assert (context.cell_dir / "component.wasm").read_bytes() == BLOB
    assert dep.hash == fake_pin(BLOB)


def test_recipe_that_contradicts_the_pin_is_a_hard_error(tmp_path: Path, monkeypatch):
    context = make_cell(tmp_path, blob=None)
    fake_build(tmp_path, monkeypatch, output=b"the recipe builds something else")
    with pytest.raises(ArtifactPinMismatch, match="lying"):
        Component.build_or_get(context, FakeHasher())


def test_no_blob_and_no_nix_is_cell_unavailable(tmp_path: Path, monkeypatch):
    context = make_cell(tmp_path, blob=None)
    monkeypatch.setattr(artifact_mod.shutil, "which", lambda name: None)
    with pytest.raises(CellUnavailable):
        Component.build_or_get(context, FakeHasher())


def test_undeclared_artifact_is_an_error(tmp_path: Path):
    cell_dir = tmp_path / "cell"
    cell_dir.mkdir()
    (cell_dir / "artifact").write_text("")  # manifest exists but pins nothing
    with pytest.raises(ArtifactPinMismatch, match="does not pin"):
        Component.build_or_get(SimpleNamespace(cell_dir=cell_dir), FakeHasher())


# --- publish refuses a lying cell ---------------------------------------------

def test_publish_refuses_bad_pin(builtins: DesmataBuiltins, tmp_path: Path):
    cell_dir = tmp_path / "liar"
    shutil.copytree(GREETER_DIR, cell_dir)
    (cell_dir / "blob.wasm").write_bytes(b"these bytes")
    (cell_dir / "artifact").write_text("blob.wasm  dsm:ipfs:QmNotTheseBytes\n")
    with pytest.raises(ArtifactPinMismatch):
        publish_cell(builtins.ipfs, cell_dir)


def test_publish_accepts_true_pin(builtins: DesmataBuiltins, tmp_path: Path):
    cell_dir = tmp_path / "honest"
    shutil.copytree(GREETER_DIR, cell_dir)
    (cell_dir / "blob.wasm").write_bytes(b"these bytes")
    pin = builtins.ipfs.hash_path(cell_dir / "blob.wasm")
    (cell_dir / "artifact").write_text(f"blob.wasm  {pin}\n")
    hashes = publish_cell(builtins.ipfs, cell_dir)
    assert hashes.cell_hash is not None


# --- WAVE codec ----------------------------------------------------------------

def test_wave_decodes_gnize_shaped_output():
    text = (
        "[{channel: 0, width: 15, value: 16943752126273778883, score: 4}, "
        "{channel: 0, width: 15, value: 3404358160416860246, score: 0}]"
    )
    assert wave.decode(text) == [
        {"channel": 0, "width": 15, "value": 16943752126273778883, "score": 4},
        {"channel": 0, "width": 15, "value": 3404358160416860246, "score": 0},
    ]


def test_wave_encodes_call_arguments():
    assert wave.encode([104, 105]) == "[104, 105]"
    assert wave.encode(b"hi") == "[104, 105]"
    assert wave.encode("hi \"there\"\n") == '"hi \\"there\\"\\n"'
    assert wave.encode({"min-zeros": 0, "on": True}) == "{min-zeros: 0, on: true}"


@pytest.mark.parametrize(
    "value",
    [
        [],
        [1, 2, 3],
        "plain",
        'quo"te\\s\n',
        True,
        False,
        -42,
        1.5,
        {"a": [1, {"b": "c"}], "d-e": None},
    ],
)
def test_wave_roundtrips(value):
    assert wave.decode(wave.encode(value)) == value


def test_wave_decodes_the_rest_of_the_mapping():
    assert wave.decode("") is None  # no-result function
    assert wave.decode("none") is None
    assert wave.decode("some(3)") == 3
    assert wave.decode("'x'") == "x"
    assert wave.decode("(1, 2)") == [1, 2]  # tuple -> array
    assert wave.decode("red") == "red"  # enum case -> string
    assert wave.decode('"\\u{1F600}"') == "\U0001f600"


def test_wave_rejects_what_it_cannot_represent():
    with pytest.raises(ValueError):
        wave.encode(object())
    with pytest.raises(ValueError):
        wave.encode({"not an ident!": 1})
    with pytest.raises(ValueError):
        wave.decode("err(1)")  # variants aren't part of the canonical mapping
