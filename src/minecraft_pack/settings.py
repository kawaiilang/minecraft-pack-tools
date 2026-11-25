"""Validation settings shared by the manager, offline validator and GUI."""

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import json_guard as json
from .data import Metadata, require


def path_argument(value: str) -> Path:
    if not value.strip():
        raise argparse.ArgumentTypeError("path must not be empty")
    try:
        return Path(value).expanduser()
    except RuntimeError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _env_path(name: str) -> str | None:
    value = os.environ.get(name, "")
    return value if value.strip() else None


def add_pack_dir_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pack-dir",
        type=path_argument,
        default=_env_path("MINECRAFT_PACK_DIR") or Path.cwd() / "pack",
        help="Path to the pack directory containing pack.toml (default: $MINECRAFT_PACK_DIR or ./pack)",
    )


def add_validation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--java-version",
        default=os.environ.get("MINECRAFT_JAVA_VERSION"),
        help="Target Java runtime version (or set MINECRAFT_JAVA_VERSION)",
    )
    parser.add_argument(
        "--policy",
        type=path_argument,
        default=_env_path("MINECRAFT_PACK_POLICY"),
        help="Optional conditional pin policy JSON file (or set MINECRAFT_PACK_POLICY)",
    )


def validate_kind(kind: str) -> None:
    require(
        re.fullmatch(r"[a-z0-9][a-z0-9_-]*", kind) is not None,
        "Invalid pack kind label",
    )


def _modrinth_id(value: object) -> str:
    identifier = json.text(value)
    require(
        re.fullmatch(r"[A-Za-z0-9]+", identifier) is not None, "Expected a Modrinth ID"
    )
    return identifier


@dataclass(frozen=True)
class ConditionalPin:
    when_any_version_ids: frozenset[str]
    require_pinned_project_id: str
    reason: str | None

    @classmethod
    def parse(cls, value: object) -> "ConditionalPin":
        rule = json.record(value)
        required = {"when_any_version_ids", "require_pinned_project_id"}
        require(
            required <= rule.keys() <= required | {"reason"},
            "Invalid conditional pin fields",
        )
        versions = frozenset(
            _modrinth_id(item) for item in json.array(rule["when_any_version_ids"])
        )
        require(
            bool(versions), "when_any_version_ids must contain at least one version ID"
        )
        reason = json.text(rule["reason"]) if "reason" in rule else None
        return cls(versions, _modrinth_id(rule["require_pinned_project_id"]), reason)


@dataclass(frozen=True)
class ValidationSettings:
    java_version: str
    conditional_pins: tuple[ConditionalPin, ...] = ()

    @classmethod
    def from_options(
        cls, java_version: str | None, policy: Path | None
    ) -> "ValidationSettings":
        if not java_version:
            raise ValueError(
                "Set --java-version or MINECRAFT_JAVA_VERSION to the target Java version"
            )
        require(
            re.fullmatch(
                r"[1-9][0-9]*(?:\.[0-9]+)*(?:-[a-zA-Z0-9.-]+)?(?:\+[a-zA-Z0-9.-]+)?",
                java_version,
            )
            is not None,
            "Expected a target Java version such as 21 or 25.0.1",
        )
        if policy is None:
            return cls(java_version)
        try:
            raw = json.record(json.load(policy))
            require(
                raw.keys() == {"conditional_pins"},
                "Expected only the conditional_pins field",
            )
            pins = tuple(
                ConditionalPin.parse(item)
                for item in json.array(raw["conditional_pins"])
            )
        except (OSError, ValueError) as error:
            raise ValueError(f"Invalid policy {policy}: {error}") from error
        return cls(java_version, pins)

    def check_pins(self, specs: list[Metadata]) -> None:
        versions = {spec.version for spec in specs}
        pinned = {spec.project for spec in specs if spec.pinned}
        for rule in self.conditional_pins:
            if (
                versions & rule.when_any_version_ids
                and rule.require_pinned_project_id not in pinned
            ):
                reason = f": {rule.reason}" if rule.reason else ""
                raise ValueError(
                    f"Policy requires pinned project {rule.require_pinned_project_id}{reason}"
                )
