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
      # The default version of Django in NixOS 25.05 is 4.2.26, and
      # same is true for NixOS 25.11. However, I was using Django
      # 5.2.3 (installed via `pip`) before I switched to using NixOS,
      # and I want to keep using Django 5.  For future reference, I
      # get following error when I tried to run `./manage.py
      # makemigrations && ./manage.py migrate` with Django 4.2.26:
      #
      # ```
      #   File "/nix/store/jd20rkmqmkfkcvk2wl2lmzz7acq4svlr-python3-3.12.12/lib/python3.12/importlib/__init__.py", line 90, in import_module
      #     return _bootstrap._gcd_import(name[level:], package, level)
      #            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
      #   File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
      #   File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
      #   File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
      #   File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
      #   File "<frozen importlib._bootstrap_external>", line 999, in exec_module
      #   File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
      #   File "/home/benv/git/sponsoredissues.org-dev/sponsoredissues/migrations/0009_sponsoramount_cents_usd_positive.py", line 7, in <module>
      #     class Migration(migrations.Migration):
      #   File "/home/benv/git/sponsoredissues.org-dev/sponsoredissues/migrations/0009_sponsoramount_cents_usd_positive.py", line 17, in Migration
      #     constraint=models.CheckConstraint(condition=models.Q(('cents_usd__gt', 0)), name='cents_usd_positive'),
      #                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
      # TypeError: CheckConstraint.__init__() got an unexpected keyword argument 'condition'
      # ```
      python3WithDjango5 = pkgs.python3.override {
        packageOverrides = self: super: {
          django = super.django_5;
        };
      };
    in {
      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = [
          (python3WithDjango5.withPackages(pypkgs: with pypkgs; [
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