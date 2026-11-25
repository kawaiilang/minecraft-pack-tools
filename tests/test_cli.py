"""Offline fixtures for Minecraft pack CLI routing, locks and argument guards."""

import fcntl
import os
import subprocess
import sys
import unittest
from pathlib import Path

from minecraft_pack_fixture import PackTestCase

from minecraft_pack import json_guard
from minecraft_pack.transaction import packwiz_arguments


class PackCliTests(PackTestCase):
    def _cli_env(self) -> dict[str, str]:
        env = dict(os.environ)
        src = str(Path(__file__).resolve().parents[1] / "src")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src}:{existing}" if existing else src
        return env

    def test_pack_dir_routing_and_cli_arguments(self) -> None:
        pack_with_spaces = self.work / "pack with spaces"
        pack_with_spaces.parent.mkdir(parents=True, exist_ok=True)
        self.root.rename(pack_with_spaces)
        env = self._cli_env()

        for operation in ("list", "validate"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "minecraft_pack.cli",
                    "--pack-dir",
                    str(pack_with_spaces),
                    operation,
                    "--json",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json_guard.record(json_guard.loads(result.stdout))
            if operation == "list":
                self.assertEqual(value["minecraft"], "fixture-game")
            else:
                self.assertEqual(value, {"valid": True, "pack": "mod"})

        for arguments in [
            ["--kind", "other", "list"],
            ["install", "../bad"],
            ["side", "missing", "server"],
        ]:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "minecraft_pack.cli",
                    "--pack-dir",
                    str(pack_with_spaces),
                    *arguments,
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)

        with (self.work / ".minecraft-pack.lock").open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "minecraft_pack.cli",
                    "--pack-dir",
                    str(pack_with_spaces),
                    "list",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_identifiers_and_command_arguments(self) -> None:
        self.assertEqual(
            packwiz_arguments("install", "sodium", "bAZQdGpg"),
            [
                "modrinth",
                "install",
                "--project-id",
                "sodium",
                "--version-id",
                "bAZQdGpg",
            ],
        )
        for arguments in [
            ("migrate", None, None),
            ("install", "--pack-file", None),
            ("install", None, None),
            ("install", "sodium", "0.9.2"),
            ("export", "elsewhere", None),
        ]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                packwiz_arguments(*arguments)

    def test_lock_and_working_directory_guards(self) -> None:
        env = self._cli_env()
        with (self.work / ".minecraft-pack.lock").open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "minecraft_pack.cli",
                    "--pack-dir",
                    str(self.root),
                    "list",
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(result.returncode, 0)

        inside = subprocess.run(
            [
                sys.executable,
                "-m",
                "minecraft_pack.cli",
                "--pack-dir",
                str(self.root),
                "export",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertNotEqual(inside.returncode, 0)
        self.assertIn("outside the pack directory", inside.stderr)

        listed = subprocess.run(
            [
                sys.executable,
                "-m",
                "minecraft_pack.cli",
                "--pack-dir",
                str(self.root),
                "list",
                "--json",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(
            json_guard.record(json_guard.loads(listed.stdout))["minecraft"],
            "fixture-game",
        )

    def test_validate_cli(self) -> None:
        env = self._cli_env()
        ok = subprocess.run(
            [
                sys.executable,
                "-m",
                "minecraft_pack.validate_cli",
                str(self.root),
                "mod",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)


if __name__ == "__main__":
    unittest.main()
