{
  description = "A serverful sample desmata cell: a long-running counter server";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    { self
    , nixpkgs
    , flake-utils
    }:
    flake-utils.lib.eachDefaultSystem (system:
    let
      pkgs = import nixpkgs {
        inherit system;
      };
    in
    {
      # the server's runtime: a plain python interpreter. The cell keeps one
      # `python` process alive per session and messages it (see cell.py).
      packages = {
        python = pkgs.python3Minimal;
      };

      devShells.default = pkgs.mkShell {
        packages = with pkgs; [ python3Minimal ];
      };

    });
}
