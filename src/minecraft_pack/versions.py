"""Bind saved metadata to exact Minecraft/Fabric versions and artifact hashes."""

import json as encoder
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import json_guard as json
from .data import child, mod_specs, read_toml, require
from .fabric_deps import FabricMod


@dataclass(frozen=True)
class Artifact:
    filename: str
    url: str
    hashes: dict[str, str]
    size: int

    @classmethod
    def parse(cls, value: object) -> "Artifact":
        data = json.record(value)
        return cls(
            json.text(data.get("filename")),
            json.text(data.get("url")),
            {
                key: json.text(item)
                for key, item in json.record(data.get("hashes")).items()
            },
            json.integer(data.get("size")),
        )


@dataclass(frozen=True)
class Version:
    id: str
    project_id: str
    game_versions: list[str]
    loaders: list[str]
    files: list[Artifact]

    @classmethod
    def parse(cls, value: object) -> "Version":
        data = json.record(value)
        return cls(
            json.text(data.get("id")),
            json.text(data.get("project_id")),
            json.strings(data.get("game_versions")),
            json.strings(data.get("loaders")),
            [Artifact.parse(item) for item in json.array(data.get("files"))],
        )


LOCK_SCHEMA = 3


@dataclass(frozen=True)
class Snapshot:
    schema: int
    minecraft: str
    versions: list[Version]
    loader: FabricMod
    fabric: dict[str, FabricMod]

    @classmethod
    def parse(cls, value: object) -> "Snapshot":
        data = json.record(value)
        schema = json.integer(data.get("schema"))
        require(
            schema == LOCK_SCHEMA,
            "Incompatible version snapshot; run export to regenerate it",
        )
        return cls(
            schema,
            json.text(data.get("minecraft")),
            [Version.parse(item) for item in json.array(data.get("versions"))],
            FabricMod.parse(data.get("loader")),
            {
                digest: FabricMod.parse(item)
                for digest, item in json.record(data.get("fabric")).items()
            },
        )

    def json(self) -> str:
        payload: dict[str, object] = {
            "schema": self.schema,
            "minecraft": self.minecraft,
            "loader": self.loader.to_dict(),
            "versions": [asdict(version) for version in self.versions],
            "fabric": {
                digest: self.fabric[digest].to_dict() for digest in sorted(self.fabric)
            },
        }
        return encoder.dumps(payload, indent=2, ensure_ascii=False)


def target_version(root: Path) -> str:
    pack = read_toml(root / "pack.toml")
    versions = child(pack, "versions")
    target = json.text(versions.get("minecraft"))
    require(
        bool(target) and bool(json.text(versions.get("fabric"))),
        "Minecraft version and Fabric are required",
    )
    options = json.record(pack.get("options", {}))
    extra = json.strings(options.get("acceptable-game-versions", []))
    require(
        all(version == target for version in extra),
        "Do not widen acceptable-game-versions beyond the current Minecraft version",
    )
    return target


def check_versions(root: Path, snapshot: Snapshot) -> dict[str, Artifact]:
    target = target_version(root)
    require(
        snapshot.schema == LOCK_SCHEMA and snapshot.minecraft == target,
        "Incompatible version snapshot",
    )
    loader_version = child(read_toml(root / "pack.toml"), "versions").get("fabric")
    require(
        snapshot.loader.id == "fabricloader"
        and snapshot.loader.version == loader_version,
        "Fabric Loader snapshot differs from pack.toml",
    )
    return selected_artifacts(root, snapshot.versions)


def selected_artifacts(root: Path, versions: list[Version]) -> dict[str, Artifact]:
    target = target_version(root)
    specs = mod_specs(root)
    ids = [spec.version for spec in specs]
    require(len(set(ids)) == len(ids), "Duplicate selected versions")
    require(
        sorted(version.id for version in versions) == sorted(ids),
        "Snapshot version IDs do not match selected metadata",
    )
    result: dict[str, Artifact] = {}
    for spec in specs:
        version = next(version for version in versions if version.id == spec.version)
        require(
            version.project_id == spec.project, f"{spec.path}: wrong Modrinth project"
        )
        require(
            target in version.game_versions,
            f"{spec.path}: Modrinth version {version.id} does not explicitly support Minecraft {target}",
        )
        require("fabric" in version.loaders, f"{spec.path}: Fabric support is required")
        files = [
            artifact for artifact in version.files if artifact.filename == spec.filename
        ]
        require(len(files) == 1, f"{spec.path}: expected one selected artifact")
        artifact = files[0]
        require(
            artifact.url == spec.download.get("url")
            and artifact.url.startswith(
                f"https://cdn.modrinth.com/data/{spec.project}/versions/{spec.version}/"
            ),
            f"{spec.path}: artifact URL mismatch",
        )
        require(
            spec.download.get("hash-format") == "sha512", "Expected sha512 metadata"
        )
        require(
            artifact.hashes.get("sha512") == spec.download.get("hash"),
            f"{spec.path}: artifact hash mismatch",
        )
        result[spec.path] = artifact
    return result


def fetch_versions(ids: list[str]) -> list[Version]:
    result: list[Version] = []
    for offset in range(0, len(ids), 100):
        query = urlencode(
            {"ids": encoder.dumps(ids[offset : offset + 100], separators=(",", ":"))}
        )
        request = Request(
            "https://api.modrinth.com/v2/versions?" + query,
            headers={
                "User-Agent": "minecraft-pack-tools/1 (packwiz compatibility check)"
            },
        )
        with urlopen(request, timeout=30) as response:
            contents = response.read().decode("utf-8")
        result.extend(Version.parse(item) for item in json.array(json.loads(contents)))
    return result
