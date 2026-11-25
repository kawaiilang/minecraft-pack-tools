# minecraft-pack-tools

English | [日本語](README-jp.md)

Guarded CLI, GTK4 GUI, and offline build-time validator for [packwiz](https://packwiz.infra.link/) Fabric Minecraft packs.

## Features

- **Transactional pack updates**: Stages every mutation in a temporary copy, verifies hashes, exact Minecraft/Fabric compatibility, Fabric dependency constraints (`fabric.mod.json`, nested Jar-in-Jar, `depends`/`breaks`/`conflicts`, per-side client/server checks), and exports `client.mrpack` before atomically exchanging the pack directory.
- **File locking (`flock`)**: Prevents concurrent mutations via a shared reader / exclusive writer lock next to the pack directory.
- **GTK4 desktop front-end (`minecraft-pack-manager`)**: Delegates every operation to the guarded `minecraft-pack` CLI so CLI and GUI enforce identical invariants.
- **Offline Nix build-time validator (`lib.mkValidatedPack`)**: Verifies pack sources, hashes, compatibility lock, and exported `.mrpack` during Nix builds without network access.

## Usage

### CLI

```bash
minecraft-pack --pack-dir ./pack list
minecraft-pack --pack-dir ./pack validate
minecraft-pack --pack-dir ./pack install sodium
minecraft-pack --pack-dir ./pack side sodium client
minecraft-pack --pack-dir ./pack update
minecraft-pack --pack-dir ./pack remove sodium
```

You can also set `MINECRAFT_PACK_DIR` instead of passing `--pack-dir`.

### GTK4 GUI

```bash
minecraft-pack-manager --pack-dir ./pack
```

### Nix Flake

```nix
{
  inputs = {
    minecraft-pack-tools = {
      url = "github:kawaiilang/minecraft-pack-tools";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };
}
```

Available outputs:

- `packages.<system>.default`: CLI, validator, GTK4 GUI, and `.desktop` entry.
- `packages.<system>.cli`: Headless CLI and validator (no GTK4 dependency).
- `packages.<system>.gui`: GTK4 GUI and `.desktop` entry.
- `lib.mkValidatedPack { inherit pkgs; pack = ./pack; kind = "mod"; }`: Build-time validated `.mrpack` derivation.

## Development & Testing

```bash
nix flake check
```

## License

MIT ([LICENSE](LICENSE))
