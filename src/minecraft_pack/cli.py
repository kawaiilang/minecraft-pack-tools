"""Generic CLI entry point operating directly on any packwiz Fabric pack directory."""

import argparse
import sys

from .manager import main as manager_main
from .settings import add_pack_dir_option, add_validation_options


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="minecraft-pack",
        description="Manage a packwiz Fabric pack with flock locking, staging, and validation.",
        add_help=False,
    )
    add_pack_dir_option(parser)
    parser.add_argument(
        "--kind",
        default="mod",
        help="Pack kind label (default: mod)",
    )
    add_validation_options(parser)
    parser.add_argument("-h", "--help", action="store_true")
    known, rest = parser.parse_known_args(argv)
    if known.help:
        parser.print_help()
        print(
            "\nOperations: list, validate, install <project-id-or-slug> [version-id], update [mod-name], remove <mod-name>, pin <mod-name>, unpin <mod-name>, side <mod-name> <client|both|server>, export"
        )
        return 0
    return manager_main(
        known.pack_dir,
        str(known.kind),
        rest,
        java_version=known.java_version,
        policy=known.policy,
    )


if __name__ == "__main__":
    sys.exit(main())
