{
  description = "Guarded CLI, GTK4 GUI, and build-time validator for packwiz Fabric Minecraft packs";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      baseSrc =
        lib:
        lib.fileset.toSource {
          root = ./.;
          fileset = lib.fileset.unions [
            ./pyproject.toml
            ./README.md
            ./src
          ];
        };

      mkCli =
        { pkgs }:
        let
          inherit (pkgs) lib;
          runtimePath = lib.makeBinPath [
            pkgs.packwiz
            pkgs.coreutils
          ];
        in
        pkgs.python3Packages.buildPythonApplication {
          pname = "minecraft-pack-cli";
          version = "1.0.0";
          pyproject = true;
          src = baseSrc lib;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = [ pkgs.python3Packages.tomli-w ];
          makeWrapperArgs = [
            "--prefix"
            "PATH"
            ":"
            runtimePath
          ];
          postInstall = ''
            rm -f "$out/bin/minecraft-pack-manager"
          '';
          doCheck = false;
          doInstallCheck = true;
          installCheckPhase = ''
            runHook preInstallCheck
            env -i HOME="$TMPDIR" "$out/bin/minecraft-pack" --help >/dev/null
            env -i HOME="$TMPDIR" "$out/bin/minecraft-pack-validate" --help >/dev/null
            runHook postInstallCheck
          '';
          meta = {
            description = "Guarded CLI and offline validator for packwiz Fabric packs";
            license = lib.licenses.mit;
            mainProgram = "minecraft-pack";
            platforms = lib.platforms.linux;
          };
        };

      mkGui =
        {
          pkgs,
          cli ? mkCli { inherit pkgs; },
        }:
        let
          inherit (pkgs) lib;
          python = pkgs.python3.withPackages (ps: [
            ps.pygobject3
            ps.tomli-w
          ]);
        in
        pkgs.stdenvNoCC.mkDerivation {
          pname = "minecraft-pack-manager";
          version = "1.0.0";
          src = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./src
              ./share
            ];
          };
          nativeBuildInputs = [
            pkgs.makeWrapper
            pkgs.wrapGAppsHook4
            pkgs.gobject-introspection
          ];
          buildInputs = [
            pkgs.gtk4
            python
          ];
          dontWrapGApps = true;
          installPhase = ''
            runHook preInstall
            mkdir -p "$out/share/minecraft-pack-manager"
            cp -r src/minecraft_pack "$out/share/minecraft-pack-manager/"
            install -Dm644 share/minecraft-pack-manager.desktop "$out/share/applications/minecraft-pack-manager.desktop"
            substituteInPlace "$out/share/applications/minecraft-pack-manager.desktop" \
              --replace-fail 'Exec=minecraft-pack-manager' "Exec=$out/bin/minecraft-pack-manager"
            runHook postInstall
          '';
          preFixup = ''
            makeWrapper ${lib.getExe python} "$out/bin/minecraft-pack-manager" \
              "''${gappsWrapperArgs[@]}" \
              --prefix PYTHONPATH : "$out/share/minecraft-pack-manager" \
              --set MINECRAFT_PACK_BIN "${cli}/bin" \
              --add-flags "-m minecraft_pack.gui"
          '';
          doInstallCheck = true;
          installCheckPhase = ''
            runHook preInstallCheck
            env -i HOME="$TMPDIR" "$out/bin/minecraft-pack-manager" --help >/dev/null
            runHook postInstallCheck
          '';
          meta = {
            description = "GTK4 front-end for minecraft-pack";
            license = lib.licenses.mit;
            mainProgram = "minecraft-pack-manager";
            platforms = lib.platforms.linux;
          };
        };

      mkDefault =
        { pkgs }:
        let
          cli = mkCli { inherit pkgs; };
          gui = mkGui { inherit pkgs cli; };
        in
        pkgs.symlinkJoin {
          name = "minecraft-pack-tools-1.0.0";
          paths = [
            cli
            gui
          ];
          meta = {
            description = "Guarded CLI, GTK4 GUI, and build-time validator for packwiz Fabric Minecraft packs";
            license = pkgs.lib.licenses.mit;
            mainProgram = "minecraft-pack-manager";
            platforms = pkgs.lib.platforms.linux;
          };
        };

      mkValidatedPack =
        {
          pkgs,
          pack,
          kind ? "mod",
          javaVersion,
          policy ? null,
          name ? "minecraft-${kind}.mrpack",
        }:
        let
          inherit (pkgs) lib;
          cli = mkCli { inherit pkgs; };
          validatorArgs = [
            "${pack}"
            kind
            "--java-version"
            javaVersion
          ]
          ++ lib.optionals (policy != null) [
            "--policy"
            "${policy}"
          ];
        in
        pkgs.runCommandLocal name { src = pack + "/client.mrpack"; } ''
          export PYTHONDONTWRITEBYTECODE=1
          ${lib.getExe' cli "minecraft-pack-validate"} ${lib.escapeShellArgs validatorArgs}
          cp "$src" "$out"
        '';
    in
    {
      lib = {
        inherit
          mkCli
          mkGui
          mkDefault
          mkValidatedPack
          ;
      };

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-tree);

      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          cli = mkCli { inherit pkgs; };
          gui = mkGui { inherit pkgs cli; };
          default = mkDefault { inherit pkgs; };
        in
        {
          inherit cli gui default;
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          inherit (pkgs) lib;
          python = pkgs.python3.withPackages (ps: [ ps.tomli-w ]);
          guiPython = pkgs.python3.withPackages (ps: [
            ps.pygobject3
            ps.tomli-w
          ]);
        in
        {
          default = self.packages.${system}.default;

          validated-pack = mkValidatedPack {
            inherit pkgs;
            kind = "demo";
            javaVersion = "21";
            pack = pkgs.runCommand "example-fabric-pack" { nativeBuildInputs = [ python ]; } ''
              export PYTHONPATH="${./src}:${./tests/fixtures}"
              python3 -c 'import sys; from pathlib import Path; from minecraft_pack_fixture import fixture; fixture(Path(sys.argv[1]))' "$out"
            '';
          };

          unit-tests =
            pkgs.runCommand "minecraft-pack-tools-unit-tests"
              {
                nativeBuildInputs = [
                  python
                  pkgs.coreutils
                ];
              }
              ''
                export PYTHONDONTWRITEBYTECODE=1
                export PYTHONPATH="${./src}:${./tests}:${./tests/fixtures}"
                python3 -m unittest -v test_cli test_i18n test_fabric test_transaction
                touch "$out"
              '';

          gui-test =
            pkgs.runCommand "minecraft-pack-tools-gui-test"
              {
                nativeBuildInputs = [
                  guiPython
                  pkgs.xvfb-run
                  pkgs.gobject-introspection
                  pkgs.wrapGAppsHook4
                ];
                buildInputs = [ pkgs.gtk4 ];
              }
              ''
                export PYTHONDONTWRITEBYTECODE=1
                export PYTHONPATH="${./src}:${./tests}"
                export GI_TYPELIB_PATH="${pkgs.gtk4}/lib/girepository-1.0:${pkgs.pango.out}/lib/girepository-1.0:${pkgs.harfbuzz}/lib/girepository-1.0:${pkgs.gdk-pixbuf}/lib/girepository-1.0:${pkgs.graphene}/lib/girepository-1.0:${pkgs.glib.out}/lib/girepository-1.0:$GI_TYPELIB_PATH"
                export XDG_DATA_DIRS="${pkgs.gtk4}/share/gsettings-schemas/${pkgs.gtk4.name}:${pkgs.gsettings-desktop-schemas}/share/gsettings-schemas/${pkgs.gsettings-desktop-schemas.name}:$XDG_DATA_DIRS"
                xvfb-run -a ${lib.getExe guiPython} ${./tests/test_gui.py}
                touch "$out"
              '';

          lint-python =
            pkgs.runCommand "minecraft-pack-tools-lint-python"
              {
                src = lib.fileset.toSource {
                  root = ./.;
                  fileset = lib.fileset.unions [
                    ./pyproject.toml
                    ./pyrightconfig.json
                    ./src
                    ./tests
                  ];
                };
                nativeBuildInputs = [
                  python
                  pkgs.ruff
                  pkgs.pyright
                ];
              }
              ''
                cd "$src"
                export HOME="$TMPDIR"
                ruff check --no-cache src tests
                ruff format --check --no-cache src tests
                pyright --project pyrightconfig.json --pythonpath ${lib.getExe python}
                touch "$out"
              '';

          lint-nix =
            pkgs.runCommand "minecraft-pack-tools-lint-nix"
              {
                src = lib.fileset.toSource {
                  root = ./.;
                  fileset = lib.fileset.fileFilter (file: file.hasExt "nix") ./.;
                };
                nativeBuildInputs = [
                  pkgs.nixfmt-tree
                  pkgs.statix
                  pkgs.deadnix
                ];
              }
              ''
                cp -r "$src" "$TMPDIR/src"
                chmod -R u+w "$TMPDIR/src"
                cd "$TMPDIR/src"
                export HOME="$TMPDIR"
                ${lib.getExe pkgs.nixfmt-tree} --no-cache --tree-root . --ci
                statix check .
                deadnix --fail .
                touch "$out"
              '';

          lint-workflows =
            pkgs.runCommand "minecraft-pack-tools-lint-workflows"
              {
                src = lib.fileset.toSource {
                  root = ./.;
                  fileset = ./.github;
                };
                nativeBuildInputs = [
                  pkgs.actionlint
                  pkgs.shellcheck
                  pkgs.zizmor
                ];
              }
              ''
                cd "$src"
                export HOME="$TMPDIR"
                find .github/workflows -type f \( -name '*.yml' -o -name '*.yaml' \) \
                  -exec actionlint -shellcheck ${lib.getExe pkgs.shellcheck} {} +
                zizmor --offline .github
                touch "$out"
              '';
        }
      );
    };
}
