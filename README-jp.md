# minecraft-pack-tools

[English](README.md) | 日本語

[packwiz](https://packwiz.infra.link/) 形式の Fabric Minecraft パックを安全に管理・検証するための CLI・GTK4 GUI・オフライン検証ツールです。

## 特徴

- **トランザクション保護付きの更新**: 変更操作をすべて一時ディレクトリでステージングし、ハッシュ整合性・Minecraft / Fabric バージョン一致・Fabric 依存関係（`fabric.mod.json`、入れ子 JAR、`depends`/`breaks`/`conflicts`、client/server 別の欠損検出）と `client.mrpack` の生成がすべて通った場合のみアトミックに反映します。
- **`flock` 排他ロック**: パック親ディレクトリのロックファイルで同時編集による破損を防ぎます。
- **GTK4 GUI（`minecraft-pack-manager`）**: すべての操作を `minecraft-pack` CLI に委譲するため、CLI と GUI で完全に同一の保護・検証が働きます。
- **Nix ビルド時オフライン検証（`lib.mkValidatedPack`）**: ネットワーク通信なしでソース・ハッシュ・互換ロック・`.mrpack` の整合性を Nix ビルド時に検証します。

## 使い方

### CLI

```bash
export MINECRAFT_JAVA_VERSION=21  # 対象パックの Java バージョンを指定します。
minecraft-pack --pack-dir ./pack list
minecraft-pack --pack-dir ./pack validate
minecraft-pack --pack-dir ./pack install sodium
minecraft-pack --pack-dir ./pack side sodium client
minecraft-pack --pack-dir ./pack update
minecraft-pack --pack-dir ./pack remove sodium
```

`--pack-dir` の代わりに環境変数 `MINECRAFT_PACK_DIR` でも指定できます。

`install <project-id-or-slug> [version-id]` には Modrinth のプロジェクトを指定します。`side <mod-name> <side>`、`update [mod-name]`、`remove <mod-name>` には `list` に表示されるメタデータ ID を指定します（例：`sodium.pw.toml` は `sodium`）。`update` で名前を省略すると更新可能な全項目を更新します。

`list` と `--help` は Java の指定なしで使えます。引数なしの `validate` を含む検証・エクスポート・編集には、対象パックを実行する Java バージョンが必要になりました。`--java-version` または `MINECRAFT_JAVA_VERSION` で指定し、両方ある場合は引数が優先されます。オフライン検証にも同じ値を渡します（例：`minecraft-pack-validate ./pack --java-version 21`）。

#### 任意のリリース保留ポリシー

パック固有のピン留め要件は `--policy ./policy.json` または `MINECRAFT_PACK_POLICY` で指定できます。CLI・GUI・オフライン検証のいずれも引数を環境変数より優先します。ポリシーは索引対象のパックディレクトリの外に置きます。各ルールは列挙した Modrinth **バージョン ID** のいずれかと一致すると、指定した **プロジェクト ID** の存在とピン留めを要求します。理由は省略できます。ポリシーを省略すると通常のパック検証と依存検証が適用されます。

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

GUI は英語（`en`）・日本語（`ja`）に対応しており、`src/minecraft_pack/locales/` の JSON カタログから読み込みます。システムロケール（`LC_ALL` / `LANGUAGE` / `LC_MESSAGES` / `LANG`、未対応時は英語にフォールバック）を自動検出し、`--lang en` / `--lang ja` で明示指定もできます。

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

主な出力:

- `packages.<system>.default`: CLI・バリデータ・GTK4 GUI・`.desktop` エントリの全部入り。
- `packages.<system>.cli`: GTK4 依存なしの CLI・バリデータ。
- `packages.<system>.gui`: GTK4 GUI と `.desktop` エントリ。
- `lib.mkValidatedPack { inherit pkgs; pack = ./pack; javaVersion = "21"; kind = "mod"; }`: Nix ビルド時に検証済みの `.mrpack` を出力するビルダー関数。

  `policy = ./policy.json;` を渡すとビルド時にも同じルールを適用します。

## 開発・テスト

```bash
nix flake check
```

## ライセンス

MIT（[LICENSE](LICENSE)）
