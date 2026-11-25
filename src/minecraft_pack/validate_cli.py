"""Offline build-time validator entry point for a packwiz Fabric pack."""

import argparse
import sys
from pathlib import Path

from .validate import validate_pack


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="minecraft-pack-validate",
        description="Validate pack source, hashes, Fabric compatibility lock, and exported .mrpack without network access.",
    )
    parser.add_argument("pack_dir", type=Path, help="Path to the pack directory")
    parser.add_argument("kind", nargs="?", default="mod", help="Pack kind (default: mod)")
    args = parser.parse_args(argv)
    try:
        validate_pack(args.pack_dir, str(args.kind))
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
