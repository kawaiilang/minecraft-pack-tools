"""Offline fixtures for Minecraft pack CLI routing, locks and argument guards."""

import fcntl
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from minecraft_pack_fixture import PackTestCase, fixture

from minecraft_pack import json_guard
from minecraft_pack.data import mod_specs
from minecraft_pack.transaction import packwiz_arguments


class PackCliTests(PackTestCase):
    def _cli_env(self) -> dict[str, str]:
        env = dict(os.environ)
        src = str(Path(__file__).resolve().parents[1] / "src")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src}:{existing}" if existing else src
        env["MINECRAFT_JAVA_VERSION"] = "25"
        env.pop("MINECRAFT_PACK_POLICY", None)
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
            ["--kind", "../bad", "list"],
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

    def test_empty_paths_and_policy_home_expansion(self) -> None:
        env = self._cli_env()
        env.update(MINECRAFT_PACK_DIR="", MINECRAFT_PACK_POLICY="")

        def run(module: str, *args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-m", f"minecraft_pack.{module}", *args],
                capture_output=True,
                text=True,
                env=env,
                cwd=self.work,
                timeout=10,
            )

        # Empty environment settings select ./pack with no policy.
        result = run("cli", "validate", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        for module, args in (
            ("cli", ["--pack-dir", "", "list"]),
            ("cli", ["--pack-dir=  ", "list"]),
            ("cli", ["--policy=", "validate"]),
            ("cli", ["--policy", "  ", "validate"]),
            ("validate_cli", [""]),
            ("validate_cli", [str(self.root), "--policy", ""]),
        ):
            with self.subTest(module=module, args=args):
                result = run(module, *args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("path must not be empty", result.stderr)
                self.assertEqual(result.stdout, "")

        missing = f"{self.work.name}-missing-policy.json"
        env["MINECRAFT_PACK_POLICY"] = f"~/{missing}"
        for module, args in (
            ("cli", ["validate"]),
            ("validate_cli", [str(self.root), "--policy", f"~/{missing}"]),
        ):
            with self.subTest(module=module):
                result = run(module, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Invalid policy {Path.home() / missing}:", result.stderr)

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
        sibling = self.work / "sibling"
        fixture(sibling)
        with (self.work / f".{self.root.name}.minecraft-pack.lock").open("w") as lock:
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

            independent = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "minecraft_pack.cli",
                    "--pack-dir",
                    str(sibling),
                    "list",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(independent.returncode, 0, independent.stderr)

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

    def test_validation_options_across_entry_points(self) -> None:
        snapshot_path = self.root / "modrinth-lock.json"
        snapshot = json_guard.record(json_guard.load(snapshot_path))
        fabric = json_guard.record(snapshot["fabric"])
        digest = json_guard.text(mod_specs(self.root)[0].download["hash"])
        mod = json_guard.record(fabric[digest])
        fabric[digest] = {**mod, "depends": {"java": [">=25"]}}
        snapshot_path.write_text(json.dumps({**snapshot, "fabric": fabric}))

        env = self._cli_env()
        del env["MINECRAFT_JAVA_VERSION"]

        def run(module: str, *args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-m", f"minecraft_pack.{module}", *args],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )

        cli = ("cli", "--pack-dir", str(self.root), "--kind", "demo")
        self.assertEqual(run(*cli, "list", "--json").returncode, 0)
        for operation in (
            "validate",
            "export",
            "update",
            "remove client",
            "install client",
            "side client both",
            "pin client",
            "unpin client",
        ):
            with self.subTest(operation=operation):
                result = run(*cli, *operation.split())
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--java-version", result.stderr)

        policy = self.work / "pack policy.json"
        policy.write_text(json.dumps({"conditional_pins": []}))
        for command in (
            (*cli, "validate", "--json"),
            ("validate_cli", str(self.root), "demo"),
        ):
            with self.subTest(command=command):
                missing = run(*command)
                self.assertNotEqual(missing.returncode, 0)
                self.assertIn("MINECRAFT_JAVA_VERSION", missing.stderr)
                env["MINECRAFT_JAVA_VERSION"] = "21"
                incompatible = run(*command)
                self.assertNotEqual(incompatible.returncode, 0)
                self.assertIn("requires 'java'", incompatible.stderr)
                env["MINECRAFT_PACK_POLICY"] = str(self.work / "missing.json")
                self.assertIn(
                    "missing.json", run(*command, "--java-version", "25").stderr
                )
                ok = run(*command, "--java-version", "25", "--policy", str(policy))
                self.assertEqual(ok.returncode, 0, ok.stderr)
                if command[0] == "cli":
                    self.assertEqual(
                        json_guard.loads(ok.stdout), {"valid": True, "pack": "demo"}
                    )
                del env["MINECRAFT_JAVA_VERSION"], env["MINECRAFT_PACK_POLICY"]

        env["MINECRAFT_JAVA_VERSION"] = "25"
        for label in ("", "../bad", "--flag"):
            with self.subTest(label=label):
                self.assertIn(
                    "Invalid pack kind",
                    run(
                        "cli", "--pack-dir", str(self.root), f"--kind={label}", "list"
                    ).stderr,
                )
                self.assertIn(
                    "Invalid pack kind",
                    run("validate_cli", "--", str(self.root), label).stderr,
                )


if __name__ == "__main__":
    unittest.main()
