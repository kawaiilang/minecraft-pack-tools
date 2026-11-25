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
minecraft-pack --pack-dir ./pack list
minecraft-pack --pack-dir ./pack validate
minecraft-pack --pack-dir ./pack install sodium
minecraft-pack --pack-dir ./pack side sodium client
minecraft-pack --pack-dir ./pack update
minecraft-pack --pack-dir ./pack remove sodium
```

`--pack-dir` の代わりに環境変数 `MINECRAFT_PACK_DIR` でも指定できます。

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

主な出力:

- `packages.<system>.default`: CLI・バリデータ・GTK4 GUI・`.desktop` エントリの全部入り。
- `packages.<system>.cli`: GTK4 依存なしの CLI・バリデータ。
- `packages.<system>.gui`: GTK4 GUI と `.desktop` エントリ。
- `lib.mkValidatedPack { inherit pkgs; pack = ./pack; kind = "mod"; }`: Nix ビルド時に検証済みの `.mrpack` を出力するビルダー関数。

## 開発・テスト

```bash
nix flake check
```

## ライセンス

MIT（[LICENSE](LICENSE)）
