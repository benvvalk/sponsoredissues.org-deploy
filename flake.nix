# Note: I wrote this file by following Vimjoyer's
# tutorial "The Best Way to Use Python On NixOS":
# https://www.youtube.com/watch?v=6fftiTJ2vuQ
{
  description = "Nix flake for Python dev environment";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.05";
  };

  outputs = { self, nixpkgs, ... }:
    let
      pkgs = nixpkgs.legacyPackages."x86_64-linux";
    in {
      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = [
          (pkgs.python3.withPackages(pypkgs: with pypkgs; [
            asgiref
            certifi
            cffi
            charset-normalizer
            cryptography
            dj-database-url
            django
            django-allauth
            django-environ
            gunicorn
            idna
            oauthlib
            packaging
            psycopg2
            pycparser
            pyjwt
            requests
            requests-oauthlib
            sqlparse
            urllib3
            whitenoise
          ]))

        ];
      };
    };
      
}