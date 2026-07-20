{
  description = "desmata";

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # A second, deliberately-separate nixpkgs used ONLY to build the rust
    # nushell plugin (nu_plugin_desmata) and to provide `nu` in the dev shell.
    # The main `nixpkgs` above keeps building the python side via uv2nix,
    # untouched, so the hybrid rust does not perturb the python packaging.
    #
    # Pinned to the exact rev gnize-cell resolves (nushell 0.113.1, rustc
    # 1.96.1): a nu plugin is minor-version-locked to the nushell it runs
    # under, and gnize-cell's dev shell -- where `dsm anatomy` is actually run
    # -- uses this same rev, so the plugin's 0.113 protocol matches. Bumping
    # nushell means bumping this rev AND nu-plugin/nu-protocol in the crate's
    # Cargo.toml in lockstep (agent_primers/nushell-plugin.md).
    nixpkgs-tools.url = "github:NixOS/nixpkgs/0bb7ec54c8483066ec9d7720e780a5caa71f8612";

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
    { self, nixpkgs, nixpkgs-tools, flake-utils, pyproject-nix, uv2nix, pyproject-build-systems, nushell-cell, gnize-cell }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python312;

        # The rust tools live in their own nixpkgs (see the input comment): a
        # matching `nu` and a rustc new enough for the nu-plugin crates.
        toolsPkgs = import nixpkgs-tools { inherit system; };

        # desmata's first rust: a generic nushell-plugin bridge over the Python
        # `dsm`. It shells out to `dsm <cmd> ... --output json` and reshapes the
        # result into nushell values, so the python CLI stays the single source
        # of truth for every command (anatomy, cells, check, inspect, ...). One
        # generic json->Value converter serves them all; adding a command is a
        # thin declaration, not a reimplementation. Built from the vendored
        # Cargo.lock -- no network, no crates.io hash to maintain by hand.
        nuPluginDesmata = toolsPkgs.rustPlatform.buildRustPackage {
          pname = "nu_plugin_desmata";
          version = "0.1.0";
          src = ./rust/nu_plugin_desmata;
          cargoLock.lockFile = ./rust/nu_plugin_desmata/Cargo.lock;
        };

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

        # The nushell plugin, so peers (e.g. gnize-cell's dev shell) can pull it
        # in and register it. Version-locked to nushell 0.113.x (nixpkgs-tools).
        packages.nu-plugin-desmata = nuPluginDesmata;

        devShells.default = pkgs.mkShell {
          buildInputs = [
            # editable desmata + all deps (including the dev group)
            (editablePythonSet.mkVirtualEnv "desmata-dev" workspace.deps.all)
            pkgs.uv
            pkgs.ruff
            pkgs.pyright
            pkgs.nixpkgs-fmt
            # nushell (0.113.1) + the plugin that makes `dsm <cmd>` structured.
            # `nu` here matches the plugin's protocol version, so registering it
            # in this shell's nu just works (see shellHook).
            toolsPkgs.nushell
            nuPluginDesmata
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
            # full path to the nushell plugin, for `plugin add` (see shellHook)
            DESMATA_NU_PLUGIN = "${nuPluginDesmata}/bin/nu_plugin_desmata";
          };
          shellHook = ''
            export REPO_ROOT=$(pwd)
            # to stderr, so `nix develop --command dsm ... -o json | ...` stays clean
            cat >&2 <<'EOF'
    nushell 0.113.1 + the desmata plugin are on PATH. To get structured
    `dsm anatomy`/`dsm cells`/... inside nu (a matching 0.113 REPL), once:
        plugin add $env.DESMATA_NU_PLUGIN
        plugin use desmata
    Then e.g.  dsm anatomy $env.GNIZE_CELL_SRC | get nucleus
    EOF
          '';
        };
      });
}
