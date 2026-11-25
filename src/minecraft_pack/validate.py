"""Offline pack source, exact-version snapshot and archive consistency checks."""

import hashlib
import re
import zipfile
from pathlib import Path

from . import json_guard as json
from .data import Metadata, child, index_entries, mod_specs, read_toml, require, sha256
from .fabric_deps import validate_fabric_dependencies
from .settings import ValidationSettings, validate_kind
from .versions import Artifact, Snapshot, check_versions


def validate_source(
    root: Path, kind: str, settings: ValidationSettings
) -> dict[str, Artifact]:
    validate_kind(kind)
    snapshot = Snapshot.parse(json.load(root / "modrinth-lock.json"))
    provenance = check_versions(root, snapshot)
    specs = mod_specs(root)
    validate_fabric_dependencies(
        minecraft=snapshot.minecraft,
        loader=snapshot.loader,
        specs=specs,
        sha512_by_path={
            path: json.text(artifact.hashes.get("sha512"))
            for path, artifact in provenance.items()
        },
        fabric_by_hash=snapshot.fabric,
        java_version=settings.java_version,
    )
    settings.check_pins(specs)
    return provenance


def validate_pack(
    root: Path, kind: str = "mod", *, settings: ValidationSettings
) -> None:
    validate_archive(root, validate_source(root, kind, settings))


def validate_archive(root: Path, provenance: dict[str, Artifact]) -> None:
    pack = read_toml(root / "pack.toml")
    require(pack.get("pack-format") == "packwiz:1.1.0", "Unsupported packwiz format")
    index_reference = child(pack, "index")
    require(
        index_reference.get("file") == "index.toml"
        and index_reference.get("hash-format") == "sha256",
        "Unsupported pack index",
    )
    require(
        index_reference.get("hash") == sha256(root / "index.toml"), "Stale source index"
    )
    require(
        read_toml(root / "index.toml").get("hash-format") == "sha256",
        "Unsupported index hash format",
    )
    entries = index_entries(root)
    require(
        len({entry.file for entry in entries}) == len(entries),
        "Duplicate indexed files",
    )
    with zipfile.ZipFile(root / "client.mrpack") as archive:
        manifest = json.record(
            json.loads(archive.read("modrinth.index.json").decode("utf-8"))
        )
        require(
            manifest.get("formatVersion") == 1 and manifest.get("game") == "minecraft",
            "Invalid mrpack format",
        )
        require(
            manifest.get("name") == pack.get("name")
            and manifest.get("versionId") == pack.get("version"),
            "Archive name/version differs from source",
        )
        versions = child(pack, "versions")
        require(
            manifest.get("dependencies")
            == {
                "minecraft": versions.get("minecraft"),
                "fabric-loader": versions.get("fabric"),
            },
            "Archive dependency mismatch",
        )
        files = [json.record(item) for item in json.array(manifest.get("files"))]
        paths = [json.text(item.get("path")) for item in files]
        require(len(set(paths)) == len(paths), "Duplicate archive downloads")
        downloads: list[str] = []
        overrides: list[str] = []
        projects: list[str] = []
        for entry in entries:
            require(
                re.fullmatch(
                    r"(mods|config|resourcepacks|shaderpacks)/[^\\]+", entry.file
                )
                is not None
                and not any(part in ("", ".", "..") for part in entry.file.split("/")),
                "Invalid indexed path",
            )
            source = root / entry.file
            require(entry.hash == sha256(source), f"Stale metadata hash: {entry.file}")
            if entry.metafile:
                require(entry.file.endswith(".pw.toml"), "Invalid metafile suffix")
                metadata = Metadata(entry.file, read_toml(source))
                require(
                    Path(metadata.filename).name == metadata.filename,
                    "Invalid download filename",
                )
                require(
                    list(child(metadata.values, "update")) == ["modrinth"],
                    "Only Modrinth metadata is accepted",
                )
                require(
                    metadata.download.get("hash-format") == "sha512",
                    "Expected sha512 metadata",
                )
                url = json.text(metadata.download.get("url"))
                require(
                    url.startswith(
                        f"https://cdn.modrinth.com/data/{metadata.project}/versions/{metadata.version}/"
                    ),
                    "Download URL does not match Modrinth metadata",
                )
                target = str(Path(entry.file).parent / metadata.filename)
                matches = [item for item in files if item.get("path") == target]
                require(len(matches) == 1, f"Expected one archive entry: {target}")
                selected = matches[0]
                require(
                    selected.get("downloads") == [url], "Archive download URL mismatch"
                )
                hashes = child(selected, "hashes")
                sha512 = json.text(hashes.get("sha512"))
                sha1 = json.text(hashes.get("sha1"))
                require(
                    sha512 == metadata.download.get("hash")
                    and re.fullmatch("[a-f0-9]{128}", sha512) is not None,
                    "Invalid sha512 download hash",
                )
                require(
                    re.fullmatch("[a-f0-9]{40}", sha1) is not None,
                    "Invalid sha1 download hash",
                )
                size = json.integer(selected.get("fileSize"))
                require(size > 0, "Invalid download size")
                require(
                    metadata.side in ("client", "server", "both"),
                    "Invalid installation side for this pack",
                )
                if entry.file.startswith(("resourcepacks/", "shaderpacks/")):
                    require(
                        metadata.side == "client",
                        "Resource and shader packs must be client-only",
                    )
                require(
                    selected.get("env")
                    == {
                        "client": "unsupported"
                        if metadata.side == "server"
                        else "required",
                        "server": "unsupported"
                        if metadata.side == "client"
                        else "required",
                    },
                    "Stale archive side assignment",
                )
                if entry.file.startswith("mods/"):
                    verified = provenance[entry.file]
                    require(
                        sha1 == verified.hashes.get("sha1") and size == verified.size,
                        "Artifact provenance mismatch",
                    )
                downloads.append(target)
                projects.append(metadata.project)
            else:
                require(
                    entry.file.startswith("config/"),
                    "Only configuration files may be bundled",
                )
                target = "overrides/" + entry.file
                require(
                    hashlib.sha256(archive.read(target)).hexdigest() == entry.hash,
                    "Stale bundled configuration",
                )
                overrides.append(target)
        require(sorted(paths) == sorted(downloads), "Unexpected archive downloads")
        archived = [item.filename for item in archive.infolist() if not item.is_dir()]
        require(
            sorted(archived) == sorted(["modrinth.index.json", *overrides]),
            "Unexpected archived files",
        )
    source_files: list[str] = []
    for path in root.rglob("*"):
        require(not path.is_symlink(), "Pack sources must not contain symlinks")
        require(
            path.is_dir() or path.is_file(),
            "Pack sources must contain only regular files and directories",
        )
        if path.is_file():
            source_files.append(str(path.relative_to(root)))
    require(
        sorted(source_files)
        == sorted(
            [
                ".packwizignore",
                "pack.toml",
                "index.toml",
                "client.mrpack",
                "modrinth-lock.json",
                *[entry.file for entry in entries],
            ]
        ),
        "Unindexed source files or runtime state in pack",
    )
