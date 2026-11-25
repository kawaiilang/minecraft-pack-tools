"""Stage, validate and atomically exchange a pack under the caller's lock."""

import ctypes
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from . import json_guard as json
from .data import child, entries, mod_specs, read_toml, require, sha256
from .fabric_deps import FabricMod, fetch_fabric_metadata, fetch_loader
from .settings import ValidationSettings
from .validate import validate_archive, validate_source
from .versions import (
    LOCK_SCHEMA,
    Snapshot,
    Version,
    fetch_versions,
    selected_artifacts,
    target_version,
)

Runner = Callable[[Path, Path, list[str]], None]
Fetcher = Callable[[list[str]], list[Version]]
FabricFetcher = Callable[
    [list[tuple[str, str, int]], dict[str, FabricMod]], dict[str, FabricMod]
]


def packwiz_arguments(
    operation: str, project: str | None, version: str | None
) -> list[str]:
    if project is not None:
        require(
            re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", project) is not None,
            "Use a Modrinth project slug/ID or metadata name, not flags or paths",
        )
    if version is not None:
        require(
            re.fullmatch("[A-Za-z0-9]+", version) is not None,
            "Use a Modrinth version ID, not a version number",
        )
    if operation == "install":
        if project is None:
            raise ValueError("install requires a project slug/ID")
        return (
            ["modrinth", "install", project]
            if version is None
            else [
                "modrinth",
                "install",
                "--project-id",
                project,
                "--version-id",
                version,
            ]
        )
    if operation == "update":
        require(version is None, "update takes at most one project")
        return ["update", "--all" if project is None else project]
    if operation == "export":
        require(project is None and version is None, "export takes no arguments")
        return []
    if operation in ("pin", "unpin", "remove"):
        if project is None or version is not None:
            raise ValueError(f"{operation} requires exactly one metadata name")
        return [operation, project]
    if operation == "side":
        if project is None or version not in ("client", "server", "both"):
            raise ValueError("side requires a metadata name and client, server or both")
        return ["side", project, version]
    raise ValueError("Use install, update, export, pin, unpin, remove or side")


@dataclass(frozen=True)
class TreeEntry:
    path: str
    kind: str
    hash: str | None


def tree_snapshot(root: Path) -> list[TreeEntry]:
    require(stat.S_ISDIR(root.lstat().st_mode), "Expected a real pack directory")
    result: list[TreeEntry] = []
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        require(
            stat.S_ISREG(mode) or stat.S_ISDIR(mode),
            "Pack sources must contain only regular files and directories",
        )
        regular = stat.S_ISREG(mode)
        result.append(
            TreeEntry(
                str(path.relative_to(root)),
                "file" if regular else "dir",
                sha256(path) if regular else None,
            )
        )
    return result


def _cached_metadata(root: Path) -> tuple[FabricMod | None, dict[str, FabricMod]]:
    try:
        data = json.record(json.load(root / "modrinth-lock.json"))
        if data.get("schema") != LOCK_SCHEMA:
            return None, {}
        raw_mods = json.record(data.get("fabric"))
    except (OSError, ValueError):
        return None, {}
    try:
        loader = FabricMod.parse(data.get("loader"))
    except ValueError:
        loader = None
    mods: dict[str, FabricMod] = {}
    for digest, value in raw_mods.items():
        try:
            mods[digest] = FabricMod.parse(value)
        except ValueError:
            continue
    return loader, mods


def transact(
    root: Path,
    command: list[str],
    runner: Runner,
    fetcher: Fetcher,
    kind: str = "mod",
    exchange: str = "mv",
    fabric_fetcher: FabricFetcher = fetch_fabric_metadata,
    loader_fetcher: Callable[[str], FabricMod] = fetch_loader,
    *,
    settings: ValidationSettings,
) -> None:
    root = Path(os.path.abspath(root))
    before = tree_snapshot(root)
    target_version(root)
    work = Path(tempfile.mkdtemp(prefix=".minecraft-pack-work.", dir=root.parent))
    candidate = work / "pack"
    try:
        shutil.copytree(root, candidate)
        for path in [candidate, *candidate.rglob("*")]:
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
        config = work / "config.toml"
        config.write_text("")
        if command[:1] == ["side"]:
            matches = [
                entry
                for entry in entries(candidate)
                if Path(entry.path).name == f"{command[1]}.pw.toml"
            ]
            require(len(matches) == 1, "Unknown or ambiguous side target")
            entry = matches[0]
            entry.values["side"] = command[2]
            (candidate / entry.path).write_text(tomli_w.dumps(entry.values))
        elif command:
            runner(candidate, config, command)
        if command[:1] in (["modrinth"], ["update"]):
            original = entries(root)
            for entry in entries(candidate):
                previous = next(
                    (old for old in original if old.project == entry.project), None
                )
                if previous is not None and previous.side != entry.side:
                    entry.values["side"] = previous.side
                    (candidate / entry.path).write_text(tomli_w.dumps(entry.values))
        if command[:2] == ["modrinth", "install"]:
            pinned = {spec.project for spec in mod_specs(root) if spec.pinned}
            for spec in mod_specs(candidate):
                if spec.project in pinned and not spec.pinned:
                    spec.values["pin"] = True
                    (candidate / spec.path).write_text(tomli_w.dumps(spec.values))
        runner(candidate, config, ["refresh"])
        versions = fetcher([spec.version for spec in mod_specs(candidate)])
        provenance = selected_artifacts(candidate, versions)
        artifacts = [
            (json.text(artifact.hashes.get("sha512")), artifact.url, artifact.size)
            for artifact in provenance.values()
        ]
        loader, cached = _cached_metadata(root)
        loader_version = json.text(
            child(read_toml(candidate / "pack.toml"), "versions").get("fabric")
        )
        if (
            loader is None
            or loader.id != "fabricloader"
            or loader.version != loader_version
        ):
            loader = loader_fetcher(loader_version)
        snapshot = Snapshot(
            LOCK_SCHEMA,
            target_version(candidate),
            sorted(versions, key=lambda v: v.id),
            loader,
            fabric_fetcher(artifacts, cached),
        )
        (candidate / "modrinth-lock.json").write_text(snapshot.json(), encoding="utf-8")
        checked = validate_source(candidate, kind, settings)
        source = [
            entry for entry in tree_snapshot(candidate) if entry.path != "client.mrpack"
        ]
        runner(candidate, config, ["modrinth", "export", "-o", "client.mrpack"])
        require(
            source
            == [
                entry
                for entry in tree_snapshot(candidate)
                if entry.path != "client.mrpack"
            ],
            "Export changed validated pack sources",
        )
        validate_archive(candidate, checked)
        require(
            tree_snapshot(root) == before,
            "Concurrent source edit; original pack retained",
        )
        _atomic_exchange(candidate, root, exchange)
    finally:
        shutil.rmtree(work)


def _atomic_exchange(candidate: Path, root: Path, exchange: str) -> None:
    if exchange == "mv":
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            at_fdcwd = -100
            rename_exchange = 2
            result = int(
                renameat2(
                    at_fdcwd,
                    os.fsencode(candidate),
                    at_fdcwd,
                    os.fsencode(root),
                    rename_exchange,
                )
            )
            if result == 0:
                return
            err = ctypes.get_errno()
            raise ValueError(
                f"Atomic publication failed; original pack retained: {os.strerror(err)}"
            )
    published = subprocess.run(
        [
            exchange,
            "--exchange",
            "--no-copy",
            "--no-target-directory",
            "--",
            str(candidate),
            str(root),
        ],
        capture_output=True,
        text=True,
    )
    require(
        published.returncode == 0,
        f"Atomic publication failed; original pack retained: {published.stderr}",
    )


def run_packwiz(stage: Path, config: Path, arguments: list[str]) -> None:
    if arguments[:2] == ["modrinth", "install"] and len(arguments) == 3:
        arguments = ["modrinth", "install", "--project-id", arguments[2]]
    # An empty per-transaction config excludes user-wide compatibility overrides
    # and credentials. Inherited terminal behavior remains available to packwiz.
    subprocess.run(
        ["packwiz", "--config", str(config), "--yes", *arguments], cwd=stage, check=True
    )


def mutate(
    root: Path, command: list[str], kind: str, settings: ValidationSettings
) -> None:
    transact(root, command, run_packwiz, fetch_versions, kind, settings=settings)
    print("Validated and published pack source, Modrinth snapshot and client.mrpack")
