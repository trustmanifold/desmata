{
  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    claude-code-tarball = {
      url = "https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-0.2.45.tgz";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, flake-utils, poetry2nix, claude-code-tarball }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { 
          inherit system;
        };
        inherit (poetry2nix.lib.mkPoetry2Nix { inherit pkgs; }) mkPoetryApplication;
        inherit (poetry2nix.lib.mkPoetry2Nix { inherit pkgs; }) mkPoetryEditablePackage;

        desmata = mkPoetryApplication { projectDir = ./.; };
        desmata-dev = mkPoetryEditablePackage {
          python = pkgs.python312;
          projectDir = ./.;
          editablePackageSources = {
            desmata = ./src;
          };
        };

        claude-code = import ./claude.nix { 
          inherit pkgs claude-code-tarball; 
          claude-code-version = "0.2.45";
        };
      in
      {

        packages = {
          default = desmata;
        };

        devShells.default = pkgs.mkShell {
          inputsFrom = [ desmata desmata-dev ];
          packages = with pkgs; [ 
            ruff
            python312Packages.python-lsp-ruff
            pyright
            nixpkgs-fmt 
            claude-code
          ];
        };
      });
}
