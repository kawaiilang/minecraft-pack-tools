"""Public pack CLI with a shared reader/exclusive writer flock boundary."""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from . import json_guard
from .data import list_pack, require
from .transaction import mutate, packwiz_arguments
from .validate import validate_pack


def manage(
    root: Path,
    kind: str,
    operation: str,
    project: str | None,
    version: str | None,
    machine: bool,
) -> None:
    require(kind == "mod", "Unknown pack")
    root = Path(os.path.abspath(root))
    actual_root = root.resolve()
    cwd = Path.cwd()
    require(
        cwd != actual_root and actual_root not in cwd.parents,
        "Run minecraft-pack from outside the pack directory",
    )
    reading = operation in ("list", "validate")
    if reading:
        require(
            project is None and version is None,
            f"{operation} takes no project arguments",
        )
        command: list[str] = []
    else:
        require(not machine, "--json is only supported for list and validate")
        command = packwiz_arguments(operation, project, version)
    descriptor = os.open(
        root.parent / ".minecraft-pack.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    try:
        fcntl.flock(
            descriptor, (fcntl.LOCK_SH if reading else fcntl.LOCK_EX) | fcntl.LOCK_NB
        )
        if operation == "list":
            value = list_pack(root)
            if machine:
                print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            else:
                print(
                    f"{value['name']} — Minecraft {value['minecraft']}, Fabric {value['fabric']}"
                )
                for item in json_guard.array(value["entries"]):
                    entry = json_guard.record(item)
                    print(
                        f"{entry['category']}/{entry['id']}: {entry['name']} "
                        f"[{entry['side']}] {entry['version']}"
                        + (" (pinned)" if entry["pinned"] else "")
                    )
        elif operation == "validate":
            validate_pack(root, kind)
            print(
                json.dumps({"valid": True, "pack": kind}, separators=(",", ":"))
                if machine
                else "Pack source, hashes, exact Minecraft/Fabric compatibility and export are valid."
            )
        else:
            mutate(root, command, kind)
    finally:
        os.close(descriptor)


def main(root: Path, kind: str, arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", nargs="?", default="list")
    parser.add_argument("project", nargs="?")
    parser.add_argument("version", nargs="?")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        manage(
            root,
            kind,
            str(args.operation),
            None if args.project is None else str(args.project),
            None if args.version is None else str(args.version),
            bool(args.json),
        )
    except (
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ) as error:
        print(
            f"Pack operation failed (or another operation holds the pack lock): {error}",
            file=sys.stderr,
        )
        return 1
    return 0
