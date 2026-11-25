"""Generic CLI entry point operating directly on any packwiz Fabric pack directory."""

import argparse
import os
import sys
from pathlib import Path

from .manager import main as manager_main


def _default_pack_dir() -> Path:
    configured = os.environ.get("MINECRAFT_PACK_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / "pack"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="minecraft-pack",
        description="Manage a packwiz Fabric pack with flock locking, staging, and validation.",
        add_help=False,
    )
    parser.add_argument(
        "--pack-dir",
        type=Path,
        default=None,
        help="Path to the pack directory containing pack.toml (default: $MINECRAFT_PACK_DIR or ./pack)",
    )
    parser.add_argument(
        "--kind",
        default="mod",
        help="Pack kind label (default: mod)",
    )
    parser.add_argument("-h", "--help", action="store_true")
    known, rest = parser.parse_known_args(argv)
    if known.help:
        parser.print_help()
        print("\nOperations: list, validate, install <project> [version], update [project], remove <project>, pin <project>, unpin <project>, side <project> <client|both|server>, export")
        return 0
    pack_dir = (
        known.pack_dir if known.pack_dir is not None else _default_pack_dir()
    ).expanduser()
    return manager_main(pack_dir, str(known.kind), rest)


if __name__ == "__main__":
    sys.exit(main())
