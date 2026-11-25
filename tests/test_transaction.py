"""Offline fixtures for pack provenance, validation, transactions and rollback."""

import json
import os
import sys
import unittest
import zipfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from minecraft_pack_fixture import (
    TEST_SETTINGS,
    PackTestCase,
    fabric_mod,
    fixture,
    modded_pack_dir,
    read_toml,
    refresh,
    runner,
    write_toml,
)

from minecraft_pack import json_guard as repo_json
from minecraft_pack.data import Metadata, list_pack
from minecraft_pack.fabric_deps import FabricMod
from minecraft_pack.settings import ValidationSettings
from minecraft_pack.transaction import transact, tree_snapshot
from minecraft_pack.validate import validate_pack
from minecraft_pack.versions import Snapshot, Version, check_versions


class PackTransactionTests(PackTestCase):
    def test_optional_external_pack_validates_offline(self) -> None:
        if not os.environ.get("MINECRAFT_PACK_MODDED"):
            self.skipTest("MINECRAFT_PACK_MODDED is not set")
        validate_pack(modded_pack_dir(), "mod", settings=TEST_SETTINGS)

    def test_synthetic_sides_empty_pack_and_independent_versions(self) -> None:
        validate_pack(self.root, "mod", settings=TEST_SETTINGS)
        with self.assertRaises(ValueError):
            validate_pack(self.root, "../invalid", settings=TEST_SETTINGS)
        other = self.work / "independent"
        fixture(other, release="v2")
        before = tree_snapshot(other)
        transact(
            self.root,
            [],
            runner,
            self.fetch,
            "mod",
            fabric_fetcher=self.fetch_fabric,
            settings=TEST_SETTINGS,
        )
        self.assertEqual(tree_snapshot(other), before)
        self.assertNotEqual(list_pack(self.root), list_pack(other))
        empty = self.work / "empty"
        fixture(empty, ())
        validate_pack(empty, "mod", settings=TEST_SETTINGS)
        self.assertEqual(list_pack(empty)["entries"], [])

    def test_java_constraint_rejects_update_before_exchange(self) -> None:
        path = self.root / "modrinth-lock.json"
        snapshot = Snapshot.parse(repo_json.load(path))
        digest, mod = next(iter(snapshot.fabric.items()))
        path.write_text(
            replace(
                snapshot,
                fabric={
                    **snapshot.fabric,
                    digest: replace(mod, depends={"java": [">=25"]}),
                },
            ).json()
        )
        before = tree_snapshot(self.root)
        with self.assertRaisesRegex(ValueError, "requires 'java'"):
            transact(
                self.root,
                ["update", "--all"],
                runner,
                self.fetch,
                fabric_fetcher=self.fetch_fabric,
                settings=ValidationSettings("21"),
            )
        self.assertEqual(tree_snapshot(self.root), before)
        transact(
            self.root,
            ["update", "--all"],
            runner,
            self.fetch,
            fabric_fetcher=self.fetch_fabric,
            settings=ValidationSettings("25"),
        )

    def test_version_triggered_policy_offline_and_before_publication(self) -> None:
        policy = self.work / "policy.json"
        policy.write_text(
            json.dumps(
                {
                    "conditional_pins": [
                        {
                            "when_any_version_ids": ["v1client"],
                            "require_pinned_project_id": "server",
                            "reason": "compatibility hold",
                        }
                    ]
                }
            )
        )
        settings = ValidationSettings.from_options("25", policy)
        validate_pack(self.root, settings=TEST_SETTINGS)
        with self.assertRaisesRegex(
            ValueError, "pinned project server: compatibility hold"
        ):
            validate_pack(self.root, settings=settings)
        other = self.work / "later-release"
        fixture(other, release="v2")
        validate_pack(other, settings=settings)

        entry = Path("mods/server.pw.toml")

        def set_pin(root: Path, pinned: bool) -> None:
            write_toml(root / entry, {**read_toml(root / entry), "pin": pinned})

        set_pin(self.root, True)
        refresh(self.root)
        validate_pack(self.root, settings=settings)
        before = tree_snapshot(self.root)
        commands: list[list[str]] = []

        def unpin(stage: Path, config: Path, arguments: list[str]) -> None:
            commands.append(arguments)
            if arguments == ["unpin", "server"]:
                set_pin(stage, False)
            else:
                runner(stage, config, arguments)

        with self.assertRaisesRegex(ValueError, "pinned project server"):
            transact(
                self.root,
                ["unpin", "server"],
                unpin,
                self.fetch,
                fabric_fetcher=self.fetch_fabric,
                settings=settings,
            )
        self.assertEqual(commands, [["unpin", "server"], ["refresh"]])
        self.assertEqual(tree_snapshot(self.root), before)

    def test_policy_rejects_malformed_rules(self) -> None:
        policy = self.work / "policy.json"
        rule = {
            "when_any_version_ids": ["v1client"],
            "require_pinned_project_id": "server",
        }
        for raw in (
            "{",
            "[]",
            "{}",
            '{"conditional_pins": {}, "extra": 0}',
            *(
                json.dumps({"conditional_pins": [bad]})
                for bad in (
                    {},
                    {**rule, "extra": 0},
                    {**rule, "reason": None},
                    {**rule, "when_any_version_ids": "v1client"},
                    {**rule, "when_any_version_ids": []},
                    {**rule, "when_any_version_ids": [""]},
                    {**rule, "require_pinned_project_id": " "},
                )
            ),
        ):
            with (
                self.subTest(raw=raw),
                self.assertRaisesRegex(ValueError, "Invalid policy"),
            ):
                policy.write_text(raw)
                ValidationSettings.from_options("25", policy)

    def test_invalid_supported_versions_and_provenance(self) -> None:
        original = repo_json.record(repo_json.load(self.root / "modrinth-lock.json"))
        cases: list[tuple[str, object]] = [
            ("game_versions", "fixture-game"),
            ("game_versions", ["fixture-game", 123]),
            ("game_versions", ["fixture-game.1"]),
            ("loaders", "fabric"),
            ("loaders", ["neoforge"]),
            ("project_id", "other"),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                raw = deepcopy(original)
                versions = [
                    repo_json.record(item) for item in repo_json.array(raw["versions"])
                ]
                versions[0][field] = value
                raw["versions"] = versions
                with self.assertRaises(ValueError):
                    check_versions(self.root, Snapshot.parse(raw))
        snapshot = Snapshot.parse(original)
        with self.assertRaises(ValueError):
            check_versions(
                self.root,
                replace(snapshot, versions=snapshot.versions + [snapshot.versions[0]]),
            )
        with self.assertRaises(ValueError):
            check_versions(self.root, replace(snapshot, versions=snapshot.versions[1:]))

    def test_duplicate_roots_and_constraints_stop_export(self) -> None:
        path = self.root / "modrinth-lock.json"
        snapshot = Snapshot.parse(repo_json.load(path))
        artifacts = check_versions(self.root, snapshot)
        client = artifacts["mods/client.pw.toml"].hashes["sha512"]
        server = artifacts["mods/server.pw.toml"].hashes["sha512"]
        for changed, error in [
            (
                replace(snapshot.fabric[server], id=snapshot.fabric[client].id),
                "Duplicate top-level Fabric ID.*client.pw.toml.*server.pw.toml",
            ),
            (
                replace(snapshot.fabric[server], depends={"absent": ["*"]}),
                "requires 'absent'",
            ),
        ]:
            path.write_text(
                replace(snapshot, fabric={**snapshot.fabric, server: changed}).json()
            )
            before = tree_snapshot(self.root)
            calls: list[list[str]] = []

            def record(
                stage: Path,
                config: Path,
                arguments: list[str],
                recorded: list[list[str]] = calls,
            ) -> None:
                recorded.append(arguments)
                runner(stage, config, arguments)

            with self.assertRaisesRegex(ValueError, error):
                transact(
                    self.root,
                    [],
                    record,
                    self.fetch,
                    fabric_fetcher=self.fetch_fabric,
                    settings=TEST_SETTINGS,
                )
            self.assertEqual(calls, [["refresh"]])
            self.assertEqual(tree_snapshot(self.root), before)

    def test_lock_records_repaired_on_export(self) -> None:
        path = self.root / "modrinth-lock.json"
        initial = repo_json.record(repo_json.load(path))
        by_hash = repo_json.record(initial["fabric"])
        bad = next(iter(by_hash))
        for mode in ("root", "nested", "old schema"):
            raw = initial.copy()
            damaged = repo_json.record(by_hash[bad])
            del damaged["breaks"]
            if mode == "nested":
                damaged = {**repo_json.record(by_hash[bad]), "jars": [damaged]}
            raw["fabric"] = {**by_hash, bad: damaged}
            if mode == "old schema":
                raw["schema"] = 2
            path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                validate_pack(self.root, settings=TEST_SETTINGS)

            expected_cached: set[str] = (
                set() if mode == "old schema" else set(by_hash) - {bad}
            )

            def repair(
                artifacts: list[tuple[str, str, int]],
                cached: dict[str, FabricMod],
                expected: set[str] = expected_cached,
            ) -> dict[str, FabricMod]:
                self.assertEqual(set(cached), expected)
                return self.fetch_fabric(artifacts, cached)

            transact(
                self.root,
                [],
                runner,
                self.fetch,
                fabric_fetcher=repair,
                loader_fetcher=lambda version: fabric_mod("fabricloader", version),
                settings=TEST_SETTINGS,
            )
            validate_pack(self.root, settings=TEST_SETTINGS)

    def test_source_and_archive_integrity(self) -> None:
        path = self.root / "mods/client.pw.toml"
        path.write_text(path.read_text().replace('name = "client"', 'name = "changed"'))
        with self.assertRaises(ValueError):
            validate_pack(self.root, "mod", settings=TEST_SETTINGS)
        refresh(self.root)
        validate_pack(self.root, "mod", settings=TEST_SETTINGS)
        (self.root / "runtime.json").write_text("{}")
        with self.assertRaises(ValueError):
            validate_pack(self.root, "mod", settings=TEST_SETTINGS)
        (self.root / "runtime.json").unlink()
        (self.root / "unsafe").symlink_to(path)
        with self.assertRaises(ValueError):
            validate_pack(self.root, "mod", settings=TEST_SETTINGS)

    def test_side_update_requires_matching_export(self) -> None:
        before = tree_snapshot(self.root)

        def noop(_stage: Path, _config: Path, _arguments: list[str]) -> None:
            pass

        with self.assertRaises(ValueError):
            transact(
                self.root,
                ["side", "client", "server"],
                noop,
                self.fetch,
                "mod",
                fabric_fetcher=self.fetch_fabric,
                settings=TEST_SETTINGS,
            )
        self.assertEqual(tree_snapshot(self.root), before)
        transact(
            self.root,
            ["side", "client", "server"],
            runner,
            self.fetch,
            "mod",
            fabric_fetcher=self.fetch_fabric,
            settings=TEST_SETTINGS,
        )
        self.assertEqual(read_toml(self.root / "mods/client.pw.toml")["side"], "server")
        validate_pack(self.root, "mod", settings=TEST_SETTINGS)

    def test_reinstall_changes_version_and_preserves_existing_side_and_pin(
        self,
    ) -> None:
        path = self.root / "mods/client.pw.toml"
        original = read_toml(path)
        original["pin"] = True
        write_toml(path, original)
        refresh(self.root)
        updated = self.work / "updated"
        self.versions[0] = fixture(updated, release="v2")[0]

        def reinstall(stage: Path, config: Path, arguments: list[str]) -> None:
            if arguments[:2] == ["modrinth", "install"]:
                meta = read_toml(updated / "mods/client.pw.toml")
                meta["side"] = "both"
                write_toml(stage / "mods/client.pw.toml", meta)
            else:
                runner(stage, config, arguments)

        transact(
            self.root,
            ["modrinth", "install", "client"],
            reinstall,
            self.fetch,
            "mod",
            fabric_fetcher=self.fetch_fabric,
            settings=TEST_SETTINGS,
        )
        self.assertEqual(read_toml(path)["pin"], True)
        self.assertEqual(read_toml(path)["side"], "client")
        selected = Metadata("mods/client.pw.toml", read_toml(path))
        self.assertEqual(selected.version, self.versions[0].id)
        validate_pack(self.root, "mod", settings=TEST_SETTINGS)

    def test_failures_preserve_original_and_external_edits(self) -> None:
        before = tree_snapshot(self.root)

        def incompatible(ids: list[str]) -> list[Version]:
            return [
                replace(version, game_versions=["incompatible"])
                for version in self.fetch(ids)
            ]

        def broken(stage: Path, _config: Path, _arguments: list[str]) -> None:
            (stage / "pack.toml").write_text("broken")
            raise RuntimeError("partial packwiz failure")

        with self.assertRaises(ValueError):
            transact(
                self.root,
                ["update", "--all"],
                runner,
                incompatible,
                "mod",
                settings=TEST_SETTINGS,
            )
        self.assertEqual(tree_snapshot(self.root), before)
        with self.assertRaises(RuntimeError):
            transact(
                self.root,
                ["update", "--all"],
                broken,
                self.fetch,
                "mod",
                settings=TEST_SETTINGS,
            )
        self.assertEqual(tree_snapshot(self.root), before)

        def concurrent(ids: list[str]) -> list[Version]:
            (self.root / "external.txt").write_text("keep this edit")
            return self.fetch(ids)

        with self.assertRaisesRegex(ValueError, "Concurrent"):
            transact(
                self.root,
                [],
                runner,
                concurrent,
                "mod",
                fabric_fetcher=self.fetch_fabric,
                settings=TEST_SETTINGS,
            )
        self.assertEqual((self.root / "external.txt").read_text(), "keep this edit")
        (self.root / "external.txt").unlink()
        self.assertEqual(tree_snapshot(self.root), before)
        self.assertFalse(list(self.work.glob(".minecraft-pack-work.*")))

    def test_incompatible_auto_dependency_is_checked_before_export(self) -> None:
        before = tree_snapshot(self.root)
        artifact = replace(
            self.versions[0].files[0],
            filename="dependency.jar",
            url="https://cdn.modrinth.com/data/dependentProject/versions/addedDependency/dependency.jar",
        )
        dependency = replace(
            self.versions[0],
            id="addedDependency",
            project_id="dependentProject",
            game_versions=["incompatible"],
            files=[artifact],
        )
        commands: list[list[str]] = []

        def install(stage: Path, config: Path, arguments: list[str]) -> None:
            commands.append(arguments)
            if arguments == ["modrinth", "install", "example"]:
                name = "mods/added-dependency.pw.toml"
                write_toml(
                    stage / name,
                    {
                        "name": "Dependency",
                        "filename": artifact.filename,
                        "side": "client",
                        "download": {
                            "url": artifact.url,
                            "hash-format": "sha512",
                            "hash": artifact.hashes["sha512"],
                        },
                        "update": {
                            "modrinth": {
                                "mod-id": dependency.project_id,
                                "version": dependency.id,
                            }
                        },
                    },
                )
                index = read_toml(stage / "index.toml")
                index["files"] = repo_json.array(index["files"]) + [
                    {"file": name, "metafile": True, "hash": ""}
                ]
                write_toml(stage / "index.toml", index)
            else:
                runner(stage, config, arguments)

        def fetch(ids: list[str]) -> list[Version]:
            self.assertIn(dependency.id, ids)
            return self.versions + [dependency]

        with self.assertRaisesRegex(ValueError, "added-dependency.*support Minecraft"):
            transact(
                self.root,
                ["modrinth", "install", "example"],
                install,
                fetch,
                "mod",
                settings=TEST_SETTINGS,
            )
        self.assertEqual(tree_snapshot(self.root), before)
        self.assertNotIn(["modrinth", "export", "-o", "client.mrpack"], commands)

    def test_api_outage_and_failed_exchange_leave_original(self) -> None:
        before = tree_snapshot(self.root)

        def unavailable(_ids: list[str]) -> list[Version]:
            raise OSError("offline")

        with self.assertRaises(OSError):
            transact(self.root, [], runner, unavailable, "mod", settings=TEST_SETTINGS)
        self.assertEqual(tree_snapshot(self.root), before)
        failed = self.work / "failed-exchange"
        failed.write_text("#!" + sys.executable + "\nraise SystemExit(12)\n")
        failed.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "Atomic publication failed"):
            transact(
                self.root,
                [],
                runner,
                self.fetch,
                "mod",
                exchange=str(failed),
                fabric_fetcher=self.fetch_fabric,
                settings=TEST_SETTINGS,
            )
        self.assertEqual(tree_snapshot(self.root), before)
        self.assertFalse(list(self.work.glob(".minecraft-pack-work.*")))

    def test_exact_target_can_change_but_cannot_be_widened(self) -> None:
        snapshot = Snapshot.parse(repo_json.load(self.root / "modrinth-lock.json"))
        for suffix in (".1", ".x", "-rc-1"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                check_versions(
                    self.root,
                    replace(
                        snapshot,
                        versions=[
                            replace(v, game_versions=["fixture-game" + suffix])
                            for v in snapshot.versions
                        ],
                    ),
                )
        check_versions(
            self.root,
            replace(
                snapshot,
                versions=[
                    replace(v, game_versions=["fixture-game.1", "fixture-game"])
                    for v in snapshot.versions
                ],
            ),
        )
        pack = read_toml(self.root / "pack.toml")
        pack["options"] = {
            "acceptable-game-versions": ["fixture-game", "fixture-game.1"]
        }
        write_toml(self.root / "pack.toml", pack)
        with self.assertRaises(ValueError):
            check_versions(self.root, snapshot)
        pack.pop("options")
        versions = repo_json.record(pack["versions"])
        versions["minecraft"] = "another-exact-game"
        pack["versions"] = versions
        write_toml(self.root / "pack.toml", pack)
        with self.assertRaises(ValueError):
            check_versions(self.root, snapshot)
        check_versions(
            self.root,
            replace(
                snapshot,
                minecraft="another-exact-game",
                versions=[
                    replace(v, game_versions=["another-exact-game"])
                    for v in snapshot.versions
                ],
            ),
        )

    def test_artifact_url_hash_and_missing_snapshot_are_rejected(self) -> None:
        snapshot = Snapshot.parse(repo_json.load(self.root / "modrinth-lock.json"))
        first = snapshot.versions[0]
        artifact = first.files[0]
        for changed in [
            replace(artifact, url="https://example.invalid/a.jar"),
            replace(artifact, hashes={**artifact.hashes, "sha512": "wrong"}),
        ]:
            with self.subTest(artifact=changed), self.assertRaises(ValueError):
                check_versions(
                    self.root,
                    replace(
                        snapshot,
                        versions=[
                            replace(first, files=[changed]),
                            *snapshot.versions[1:],
                        ],
                    ),
                )
        (self.root / "modrinth-lock.json").unlink()
        with self.assertRaises(FileNotFoundError):
            validate_pack(self.root, "mod", settings=TEST_SETTINGS)

    def test_invalid_archive_and_metadata_path_never_publish(self) -> None:
        before = tree_snapshot(self.root)

        def broken_archive(stage: Path, config: Path, arguments: list[str]) -> None:
            runner(stage, config, arguments)
            if arguments == ["modrinth", "export", "-o", "client.mrpack"]:
                (stage / "client.mrpack").write_bytes(b"broken zip")

        with self.assertRaises(zipfile.BadZipFile):
            transact(
                self.root,
                [],
                broken_archive,
                self.fetch,
                "mod",
                fabric_fetcher=self.fetch_fabric,
                settings=TEST_SETTINGS,
            )
        self.assertEqual(tree_snapshot(self.root), before)
        for invalid in [
            "mods/../outside.pw.toml",
            "mods/./client.pw.toml",
            "mods/\\client.pw.toml",
        ]:
            index = read_toml(self.root / "index.toml")
            entries = [
                repo_json.record(item) for item in repo_json.array(index["files"])
            ]
            entries[0]["file"] = invalid
            index["files"] = entries
            write_toml(self.root / "index.toml", index)
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                validate_pack(self.root, "mod", settings=TEST_SETTINGS)


if __name__ == "__main__":
    unittest.main()
