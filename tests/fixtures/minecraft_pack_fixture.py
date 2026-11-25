"""Shared offline fixtures for Minecraft pack tests."""

import hashlib
import json
import os
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from typing import override

import tomli_w

from minecraft_pack import json_guard as repo_json
from minecraft_pack.fabric_deps import FabricMod
from minecraft_pack.settings import ValidationSettings
from minecraft_pack.versions import Version

TEST_SETTINGS = ValidationSettings(java_version="25")


def modded_pack_dir() -> Path:
    return Path(os.environ["MINECRAFT_PACK_MODDED"]).resolve()


def read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        value: object = tomllib.load(stream)
    return repo_json.record(value)


def write_toml(path: Path, value: dict[str, object]) -> None:
    path.write_text(tomli_w.dumps(value))


def fabric_mod(
    name: str, version: str = "1", aliases: list[str] | None = None
) -> FabricMod:
    return FabricMod(name, version, "*", aliases or [], {}, {}, {}, [])


def fixture(
    root: Path,
    sides: tuple[str, ...] = ("client", "server", "both"),
    release: str = "v1",
) -> list[Version]:
    (root / "mods").mkdir(parents=True)
    (root / ".packwizignore").write_text("")
    versions: list[dict[str, object]] = []
    files: list[dict[str, object]] = []
    fabric: dict[str, object] = {}
    for side in sides:
        filename = f"{side}-{release}.jar"
        version = release + side
        sha512 = hashlib.sha512(side.encode()).hexdigest()
        sha1 = hashlib.sha1(side.encode()).hexdigest()
        url = f"https://cdn.modrinth.com/data/{side}/versions/{version}/{filename}"
        path = f"mods/{side}.pw.toml"
        write_toml(
            root / path,
            {
                "name": side,
                "filename": filename,
                "side": side,
                "download": {"url": url, "hash-format": "sha512", "hash": sha512},
                "update": {"modrinth": {"mod-id": side, "version": version}},
            },
        )
        versions.append(
            {
                "id": version,
                "project_id": side,
                "game_versions": ["fixture-game"],
                "loaders": ["fabric"],
                "files": [
                    {
                        "filename": filename,
                        "url": url,
                        "hashes": {"sha1": sha1, "sha512": sha512},
                        "size": 10,
                    }
                ],
            }
        )
        fabric[sha512] = FabricMod(
            id=f"fixture_{side}",
            version="1.0.0",
            environment="*",
            provides=[],
            depends={"minecraft": ["*"], "fabricloader": ["*"]},
            breaks={},
            conflicts={},
            jars=[],
        ).to_dict()
        files.append({"file": path, "metafile": True, "hash": ""})
    write_toml(root / "index.toml", {"hash-format": "sha256", "files": files})
    write_toml(
        root / "pack.toml",
        {
            "name": "Fixture",
            "version": release,
            "pack-format": "packwiz:1.1.0",
            "index": {"file": "index.toml", "hash-format": "sha256", "hash": ""},
            "versions": {"minecraft": "fixture-game", "fabric": "fixture-loader"},
        },
    )
    (root / "modrinth-lock.json").write_text(
        json.dumps(
            {
                "schema": 3,
                "loader": fabric_mod("fabricloader", "fixture-loader").to_dict(),
                "minecraft": "fixture-game",
                "versions": versions,
                "fabric": fabric,
            },
            indent=2,
        )
    )
    refresh(root)
    export(root)
    return [Version.parse(item) for item in versions]


def refresh(root: Path) -> None:
    index = read_toml(root / "index.toml")
    files = [repo_json.record(item) for item in repo_json.array(index["files"])]
    for entry in files:
        entry["hash"] = hashlib.sha256(
            (root / repo_json.text(entry["file"])).read_bytes()
        ).hexdigest()
    index["files"] = files
    write_toml(root / "index.toml", index)
    pack = read_toml(root / "pack.toml")
    reference = repo_json.record(pack["index"])
    reference["hash"] = hashlib.sha256((root / "index.toml").read_bytes()).hexdigest()
    pack["index"] = reference
    write_toml(root / "pack.toml", pack)


def export(root: Path) -> None:
    pack = read_toml(root / "pack.toml")
    versions = repo_json.record(pack["versions"])
    entries: list[dict[str, object]] = []
    for path in sorted((root / "mods").glob("*.pw.toml")):
        meta = read_toml(path)
        download = repo_json.record(meta["download"])
        side = repo_json.text(meta["side"])
        entries.append(
            {
                "path": "mods/" + repo_json.text(meta["filename"]),
                "downloads": [download["url"]],
                "hashes": {
                    "sha512": download["hash"],
                    "sha1": hashlib.sha1(
                        path.stem.removesuffix(".pw").encode()
                    ).hexdigest(),
                },
                "fileSize": 10,
                "env": {
                    "client": "unsupported" if side == "server" else "required",
                    "server": "unsupported" if side == "client" else "required",
                },
            }
        )
    manifest = {
        "formatVersion": 1,
        "game": "minecraft",
        "name": pack["name"],
        "versionId": pack["version"],
        "dependencies": {
            "minecraft": versions["minecraft"],
            "fabric-loader": versions["fabric"],
        },
        "files": entries,
    }
    with zipfile.ZipFile(root / "client.mrpack", "w") as archive:
        archive.writestr("modrinth.index.json", json.dumps(manifest))


def runner(stage: Path, config: Path, arguments: list[str]) -> None:
    if config.read_text() != "":
        raise ValueError("User configuration leaked into transaction")
    if arguments == ["refresh"]:
        refresh(stage)
    elif arguments == ["modrinth", "export", "-o", "client.mrpack"]:
        export(stage)


class PackTestCase(unittest.TestCase):
    temporary: tempfile.TemporaryDirectory[str]
    work: Path
    root: Path
    versions: list[Version]

    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.root = self.work / "pack"
        self.versions = fixture(self.root)

    def fetch(self, ids: list[str]) -> list[Version]:
        self.assertEqual(sorted(ids), sorted(version.id for version in self.versions))
        return self.versions

    def fetch_fabric(
        self, artifacts: list[tuple[str, str, int]], cached: dict[str, FabricMod]
    ) -> dict[str, FabricMod]:
        result: dict[str, FabricMod] = {}
        for digest, _url, _size in artifacts:
            if digest in cached:
                result[digest] = cached[digest]
            else:
                result[digest] = FabricMod(
                    id=f"fetched_{digest[:8]}",
                    version="1.0.0",
                    environment="*",
                    provides=[],
                    depends={"minecraft": ["*"], "fabricloader": ["*"]},
                    breaks={},
                    conflicts={},
                    jars=[],
                )
        return result
