{
  description = "A sample desmata cell: wraps cowsay as a tool";

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
      packages = {
        cowsay = pkgs.cowsay;
      };

      devShells.default = pkgs.mkShell {
        packages = with pkgs; [ cowsay ];
      };

    });
}
