{
  description = "cict — Cost-of-goods Inventory Cost Tracker";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        # cict is pure Go (no C, no CGO) — build a static binary.
        cict = pkgs.buildGoModule {
          pname = "cict";
          version = "0.1.0";
          src = ./.;
          # go:embed templates/* is relative to the package source, so the
          # module hash covers the embedded files too.
          vendorHash = null;
          CGO_ENABLED = 0;
          subPackages = [ "." ];
          # buildGoModule needs go.sum; the repo has one (generated on first
          # `go mod tidy`). If go.sum is empty (no external deps), vendorHash
          # null + allow go to figure it out.
        };
        # A tiny container image: just the static binary + a writable /data
        # for the SQLite db. No shell, no layers of tooling.
        cictImage = pkgs.dockerTools.buildLayeredImage {
          name = "cict";
          tag = "latest";
          # The binary stores cict.db in its working directory; /data is the
          # PVC mount point so the database survives pod restarts.
          contents = [
            cict
            pkgs.cacert
          ];
          config = {
            WorkingDir = "/data";
            ExposedPorts = { "8080/tcp" = {}; };
            Cmd = [ "${cict}/bin/cict" ];
            Env = [
              "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            ];
          };
        };
      in {
        packages = {
          inherit cict cictImage;
          default = cict;
        };
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [ go ];
        };
      });
}