{ pkgs, claude-code-tarball, claude-code-version }:
pkgs.stdenv.mkDerivation {
  name = "claude-code";
  version = claude-code-version;
  src = claude-code-tarball;
  dontConfigure = true;
  dontBuild = true;
  installPhase = ''
    mkdir -p $out/bin
    mkdir -p $out/lib/node_modules/@anthropic-ai/claude-code

    ls ${claude-code-tarball}
    cp -r ${claude-code-tarball}/* $out/lib/node_modules/@anthropic-ai/claude-code/

    cat > $out/bin/claude-code << EOF
    #!/bin/sh
    exec ${pkgs.nodejs}/bin/node $out/lib/node_modules/@anthropic-ai/claude-code/cli.js "\$@"
    EOF

    # Make the wrapper script executable
    chmod +x $out/bin/claude-code

    # Make the ripgrep binaries executable for all platforms
    chmod +x $out/lib/node_modules/@anthropic-ai/claude-code/vendor/ripgrep/*/rg*
  '';

  buildInputs = [ pkgs.nodejs ];
}

