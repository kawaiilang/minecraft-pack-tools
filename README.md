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
export MINECRAFT_JAVA_VERSION=21  # Set this to the pack's target runtime.
minecraft-pack --pack-dir ./pack list
minecraft-pack --pack-dir ./pack validate
minecraft-pack --pack-dir ./pack install sodium
minecraft-pack --pack-dir ./pack side sodium client
minecraft-pack --pack-dir ./pack update
minecraft-pack --pack-dir ./pack remove sodium
```

You can also set `MINECRAFT_PACK_DIR` instead of passing `--pack-dir`.

Use `install <project-id-or-slug> [version-id]` for Modrinth projects. For `side <mod-name> <side>`, `update [mod-name]` and `remove <mod-name>`, use the metadata ID from `list` (for example, `sodium` for `sodium.pw.toml`). Omitting the name from `update` updates all eligible entries.

`list` and `--help` work without a Java target. Validation (including a bare `validate`), export and every edit now require the pack's Java runtime version. Pass `--java-version` or set `MINECRAFT_JAVA_VERSION`. The CLI option takes precedence. Use the same target with the offline validator, for example `minecraft-pack-validate ./pack --java-version 21`.

#### Optional release policy

Pass `--policy ./policy.json` or set `MINECRAFT_PACK_POLICY` to require specific pins for your pack. CLI options override environment settings in the manager, GUI and offline validator. Keep the policy outside the indexed pack directory. Each rule matches any listed Modrinth **version ID** and requires the specified **project ID** to be present and pinned. The reason is optional. Without a policy, only the usual pack and dependency checks apply.

```json
{
  "conditional_pins": [{
    "when_any_version_ids": ["v1Example"],
    "require_pinned_project_id": "projectA",
    "reason": "Release compatibility hold"
  }]
}
```

### GTK4 GUI

```bash
minecraft-pack-manager --pack-dir ./pack --java-version 21
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
- `lib.mkValidatedPack { inherit pkgs; pack = ./pack; javaVersion = "21"; kind = "mod"; }`: Build-time validated `.mrpack` derivation.

  Pass `policy = ./policy.json;` to apply the same rules during the build.

## Development & Testing

```bash
nix flake check
```

## License

MIT ([LICENSE](LICENSE))
