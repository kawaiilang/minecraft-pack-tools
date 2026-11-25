"""Typed views of packwiz metadata, retaining fields owned by packwiz."""

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import json_guard as json


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        value: object = tomllib.load(stream)
    return json.record(value)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def child(data: dict[str, object], name: str) -> dict[str, object]:
    return json.record(data.get(name))


@dataclass(frozen=True)
class IndexEntry:
    file: str
    hash: str
    metafile: bool


def index_entries(root: Path) -> list[IndexEntry]:
    result: list[IndexEntry] = []
    for value in json.array(read_toml(root / "index.toml").get("files")):
        data = json.record(value)
        result.append(
            IndexEntry(
                json.text(data.get("file")),
                json.text(data.get("hash")),
                json.boolean(data.get("metafile", False)),
            )
        )
    return result


@dataclass(frozen=True)
class Metadata:
    path: str
    values: dict[str, object]

    @property
    def modrinth(self) -> dict[str, object]:
        return child(child(self.values, "update"), "modrinth")

    @property
    def project(self) -> str:
        return json.text(self.modrinth.get("mod-id"))

    @property
    def version(self) -> str:
        return json.text(self.modrinth.get("version"))

    @property
    def filename(self) -> str:
        return json.text(self.values.get("filename"))

    @property
    def side(self) -> str:
        return json.text(self.values.get("side"))

    @property
    def pinned(self) -> bool:
        return json.boolean(self.values.get("pin", False))

    @property
    def download(self) -> dict[str, object]:
        return child(self.values, "download")


def entries(root: Path) -> list[Metadata]:
    result: list[Metadata] = []
    for entry in index_entries(root):
        if entry.metafile:
            require(
                re.fullmatch(
                    r"(mods|resourcepacks|shaderpacks)/[^/\\]+[.]pw[.]toml", entry.file
                )
                is not None,
                "Invalid metadata path",
            )
            result.append(Metadata(entry.file, read_toml(root / entry.file)))
    return result


def mod_specs(root: Path) -> list[Metadata]:
    result: list[Metadata] = []
    for entry in index_entries(root):
        if entry.file.startswith("mods/"):
            require(entry.metafile, "Mods must use Modrinth metadata, not bundled JARs")
            require(
                re.fullmatch(r"mods/[^\\]+[.]pw[.]toml", entry.file) is not None
                and not any(part in ("", ".", "..") for part in entry.file.split("/")),
                "Invalid mod metadata path",
            )
            result.append(Metadata(entry.file, read_toml(root / entry.file)))
    return result


def list_pack(root: Path) -> dict[str, object]:
    pack = read_toml(root / "pack.toml")
    versions = child(pack, "versions")
    listed: list[dict[str, object]] = []
    for entry in entries(root):
        listed.append(
            {
                "id": Path(entry.path).name.removesuffix(".pw.toml"),
                "name": json.text(entry.values.get("name")),
                "category": entry.path.split("/")[0],
                "side": entry.side,
                "version": entry.version,
                "filename": entry.filename,
                "pinned": entry.pinned,
                "project": entry.project,
            }
        )
    listed.sort(
        key=lambda entry: (json.text(entry["category"]), json.text(entry["name"]))
    )
    return {
        "name": json.text(pack.get("name")),
        "minecraft": json.text(versions.get("minecraft")),
        "fabric": json.text(versions.get("fabric")),
        "entries": listed,
    }
