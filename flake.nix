{
  description = "desmata";

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs = {
        pyproject-nix.follows = "pyproject-nix";
        uv2nix.follows = "uv2nix";
        nixpkgs.follows = "nixpkgs";
      };
    };

    # the standalone example cell: not built here, just a pinned source
    # fixture for the peer-to-peer tests (test/test_peer_discovery.py) --
    # a cell authored outside this repo, published and fetched by hash
    nushell-cell = {
      url = "git+file:///Users/matt/src/nushell-cell";
      flake = false;
    };

    # the lightweight example cell (artifact-pinned wasm component): the
    # fixture for the wasm-invoker tests (test/test_gnize_cell.py)
    gnize-cell = {
      url = "git+file:///Users/matt/src/gnize-cell";
      flake = false;
    };
  };

  outputs =
    { self, nixpkgs, flake-utils, pyproject-nix, uv2nix, pyproject-build-systems, nushell-cell, gnize-cell }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python312;

        # Load the uv workspace (pyproject.toml + uv.lock).
        workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

        # Overlay that turns the locked workspace into nix packages.
        # Prefer prebuilt wheels to avoid building pure-python deps from source.
        pyprojectOverlay = workspace.mkPyprojectOverlay {
          sourcePreference = "wheel";
        };

        # The base python package set: build-system packages (hatchling, etc.)
        # composed with the workspace overlay.
        pythonSet =
          (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope
            (pkgs.lib.composeManyExtensions [
              pyproject-build-systems.overlays.default
              pyprojectOverlay
            ]);

        # For the dev shell: install desmata itself as an editable package so
        # edits under ./src are picked up without reinstalling.
        editableOverlay = workspace.mkEditablePyprojectOverlay {
          root = "$REPO_ROOT";
        };

        editableHatchling = final: prev: {
          desmata = prev.desmata.overrideAttrs (old: {
            nativeBuildInputs =
              old.nativeBuildInputs
              ++ final.resolveBuildSystem { editables = [ ]; };
          });
        };

        editablePythonSet = pythonSet.overrideScope (
          pkgs.lib.composeManyExtensions [
            editableOverlay
            editableHatchling
          ]
        );
      in
      {
        # A virtualenv with desmata and its runtime deps installed.
        packages.default = pythonSet.mkVirtualEnv "desmata" workspace.deps.default;

        devShells.default = pkgs.mkShell {
          buildInputs = [
            # editable desmata + all deps (including the dev group)
            (editablePythonSet.mkVirtualEnv "desmata-dev" workspace.deps.all)
            pkgs.uv
            pkgs.ruff
            pkgs.pyright
            pkgs.nixpkgs-fmt
          ];
          env = {
            # let nix manage the environment; uv only resolves/locks
            UV_NO_SYNC = "1";
            UV_PYTHON = python.interpreter;
            UV_PYTHON_DOWNLOADS = "never";
            # the pinned nushell-cell fixture for the peer-to-peer tests
            NUSHELL_CELL_SRC = "${nushell-cell}";
            # the pinned gnize-cell fixture for the wasm-invoker tests
            GNIZE_CELL_SRC = "${gnize-cell}";
          };
          shellHook = ''
            export REPO_ROOT=$(pwd)
          '';
        };
      });
}
