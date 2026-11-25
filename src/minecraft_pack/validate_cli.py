"""Offline build-time validator entry point for a packwiz Fabric pack."""

import argparse
import sys

from .settings import ValidationSettings, add_validation_options, path_argument
from .validate import validate_pack


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="minecraft-pack-validate",
        description="Validate pack source, hashes, Fabric compatibility lock, and exported .mrpack without network access.",
    )
    parser.add_argument("pack_dir", type=path_argument, help="Path to the pack directory")
    parser.add_argument("kind", nargs="?", default="mod", help="Pack kind (default: mod)")
    add_validation_options(parser)
    args = parser.parse_args(argv)
    try:
        settings = ValidationSettings.from_options(args.java_version, args.policy)
        validate_pack(args.pack_dir, str(args.kind), settings=settings)
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
