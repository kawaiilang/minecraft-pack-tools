"""Offline fixtures for Fabric semver predicates, jar parsing and dependency checks."""

import hashlib
import io
import json
import unittest
import zipfile
from contextlib import redirect_stderr
from dataclasses import replace
from unittest.mock import patch

from minecraft_pack_fixture import PackTestCase, fabric_mod

from minecraft_pack.data import Metadata
from minecraft_pack.fabric_deps import (
    FabricMod,
    fetch_fabric_metadata,
    parse_fabric_jar,
    validate_fabric_dependencies,
)
from minecraft_pack.fabric_version import matches_any, matches_predicate


class FabricPackTests(PackTestCase):
    def test_fabric_semver_predicates_and_jar_parsing(self) -> None:
        self.assertTrue(matches_predicate("26.3", "~26.3"))
        self.assertTrue(matches_predicate("26.3.5", "~26.3"))
        self.assertFalse(matches_predicate("26.4.0", "~26.3"))
        self.assertTrue(matches_predicate("26.3", "~26.3-"))
        self.assertTrue(matches_predicate("26.3-rc.1", ">=26.3-"))
        self.assertFalse(matches_predicate("26.2.9", ">=26.3-"))
        self.assertTrue(matches_predicate("0.9.3+mc26.3", "0.9.x"))
        self.assertTrue(matches_predicate("0.9.2+mc26.3", "0.9.2+mc26.3"))
        self.assertFalse(matches_predicate("0.9.3+mc26.3", "0.9.2+mc26.3"))
        self.assertTrue(matches_predicate("0.9.2+mc26.3", "<=0.9.3-beta.1"))
        self.assertFalse(matches_predicate("0.9.3+mc26.3", "<=0.9.3-beta.1"))
        self.assertTrue(matches_predicate("0.7.1", ">=0.6.10 <1.0.0"))
        self.assertTrue(matches_any("0.7.1", ["<0.6.6", ">=0.7"]))
        self.assertFalse(matches_any("0.6.10", ["<0.6.6", ">=0.7"]))
        self.assertTrue(matches_predicate("1.11.7+mc26.3", "<=1.12.0"))

        # Expected results from Fabric Loader 0.20.1 VersionPredicate.parse(...).test(...).
        for version, predicate, matches in (
            ("26.3-beta.1", "26.3.x", True),
            ("26.3", "~26", False),
            ("26.0.1", "~26", True),
            ("26.3.0-rc.1", "=26.3.0.x", True),
            ("26.3.1-", "26.3.0.x", False),
            ("26.4-", "26.3.x.x", False),
            ("snapshot", ">=snapshot", True),
            ("1.0-00", ">1.0-2", True),
        ):
            with self.subTest(version=version, predicate=predicate):
                self.assertEqual(matches_predicate(version, predicate), matches)

        jar_path = self.work / "sample.jar"
        nested_path = self.work / "nested.jar"
        with zipfile.ZipFile(nested_path, "w") as nested:
            nested.writestr(
                "fabric.mod.json",
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "id": "nested_lib",
                        "version": "2.1.0",
                        "provides": ["alias_lib"],
                    }
                ),
            )
        with zipfile.ZipFile(jar_path, "w") as outer:
            outer.writestr(
                "fabric.mod.json",
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "id": "outer_mod",
                        "version": "1.0.0",
                        "environment": "client",
                        "depends": {"nested_lib": ">=2.0.0", "sodium": ["0.9.x"]},
                        "breaks": {"iris": "<=1.12.0"},
                        "jars": [{"file": "META-INF/jars/nested.jar"}],
                    }
                ),
            )
            outer.write(nested_path, "META-INF/jars/nested.jar")
        parsed = parse_fabric_jar(jar_path.read_bytes())
        self.assertEqual(parsed.id, "outer_mod")
        self.assertEqual(parsed.environment, "client")
        self.assertEqual(parsed.depends["sodium"], ["0.9.x"])
        self.assertEqual(parsed.breaks["iris"], ["<=1.12.0"])
        self.assertEqual(
            parsed.jars, [fabric_mod("nested_lib", "2.1.0", ["alias_lib"])]
        )

    def test_fabric_dependency_and_conflict_validation(self) -> None:
        def make_spec(name: str, side: str = "client") -> Metadata:
            return Metadata(f"mods/{name}.pw.toml", {"side": side})

        sodium_092 = FabricMod(
            id="sodium",
            version="0.9.2+mc26.3",
            environment="client",
            provides=["indium"],
            depends={"minecraft": ["26.3.x"], "fabricloader": [">=0.16.0"]},
            breaks={"iris": ["<=1.11.2"], "moreculling": ["<1.6.0-beta.2"]},
            conflicts={},
            jars=[],
        )
        sodium_093 = FabricMod(
            id="sodium",
            version="0.9.3+mc26.3",
            environment="client",
            provides=["indium"],
            depends={"minecraft": ["26.3.x"], "fabricloader": [">=0.16.0"]},
            breaks={"iris": ["<=1.12.0"], "moreculling": ["<1.9.2"]},
            conflicts={},
            jars=[],
        )
        iris_1117 = FabricMod(
            id="iris",
            version="1.11.7+mc26.3",
            environment="client",
            provides=[],
            depends={"sodium": ["0.9.x"]},
            breaks={},
            conflicts={},
            jars=[],
        )
        extra_095 = FabricMod(
            id="sodium-extra",
            version="0.9.5+mc26.3",
            environment="client",
            provides=[],
            depends={"sodium": ["0.9.2+mc26.3"]},
            breaks={},
            conflicts={},
            jars=[],
        )
        moreculling_192 = FabricMod(
            id="moreculling",
            version="1.9.2",
            environment="client",
            provides=[],
            depends={"minecraft": [">=26.3"]},
            breaks={"sodium": ["<=0.9.3-beta.1"]},
            conflicts={},
            jars=[],
        )

        # Valid combination: Sodium 0.9.2 + Iris 1.11.7 + Sodium Extra 0.9.5
        validate_fabric_dependencies(
            "26.3",
            fabric_mod("fabricloader", "0.20.1"),
            [make_spec("sodium"), make_spec("iris"), make_spec("sodium-extra")],
            {
                "mods/sodium.pw.toml": "h_sod",
                "mods/iris.pw.toml": "h_iris",
                "mods/sodium-extra.pw.toml": "h_extra",
            },
            {"h_sod": sodium_092, "h_iris": iris_1117, "h_extra": extra_095},
        )

        # Sodium 0.9.3 breaks Iris <=1.12.0
        with self.assertRaisesRegex(ValueError, "breaks 'iris'"):
            validate_fabric_dependencies(
                "26.3",
                fabric_mod("fabricloader", "0.20.1"),
                [make_spec("sodium"), make_spec("iris")],
                {"mods/sodium.pw.toml": "h_sod", "mods/iris.pw.toml": "h_iris"},
                {"h_sod": sodium_093, "h_iris": iris_1117},
            )

        # More Culling 1.9.2 breaks Sodium <=0.9.3-beta.1
        with self.assertRaisesRegex(ValueError, "breaks 'sodium'"):
            validate_fabric_dependencies(
                "26.3",
                fabric_mod("fabricloader", "0.20.1"),
                [make_spec("sodium"), make_spec("moreculling")],
                {"mods/sodium.pw.toml": "h_sod", "mods/moreculling.pw.toml": "h_mc"},
                {"h_sod": sodium_092, "h_mc": moreculling_192},
            )

        # Sodium Extra 0.9.5 requires exact Sodium 0.9.2+mc26.3
        with self.assertRaisesRegex(ValueError, "requires 'sodium'"):
            validate_fabric_dependencies(
                "26.3",
                fabric_mod("fabricloader", "0.20.1"),
                [make_spec("sodium"), make_spec("sodium-extra")],
                {
                    "mods/sodium.pw.toml": "h_sod",
                    "mods/sodium-extra.pw.toml": "h_extra",
                },
                {"h_sod": sodium_093, "h_extra": extra_095},
            )

        # Jar-in-Jar and provides satisfy dependencies; side/environment mismatch fails
        host_mod = FabricMod(
            id="host",
            version="1.0.0",
            environment="*",
            provides=[],
            depends={"nested_dep": [">=1.0.0"]},
            breaks={},
            conflicts={},
            jars=[fabric_mod("nested_dep", "1.2.0", [])],
        )
        validate_fabric_dependencies(
            "26.3",
            fabric_mod("fabricloader", "0.20.1"),
            [make_spec("host", "both")],
            {"mods/host.pw.toml": "h_host"},
            {"h_host": host_mod},
        )
        with self.assertRaisesRegex(ValueError, "environment 'client' disagrees"):
            validate_fabric_dependencies(
                "26.3",
                fabric_mod("fabricloader", "0.20.1"),
                [make_spec("iris", "server")],
                {"mods/iris.pw.toml": "h_iris"},
                {"h_iris": iris_1117},
            )

    def test_nested_selection_and_sides(self) -> None:
        loader = fabric_mod("fabricloader")
        library = fabric_mod("library", "1", ["alias"])
        parent = replace(fabric_mod("parent"), jars=[library])
        consumer = replace(fabric_mod("consumer"), depends={"alias": ["*"]})
        blocker = replace(fabric_mod("blocker"), breaks={"library": [">=2"]})
        newer = replace(fabric_mod("new_parent"), jars=[replace(library, version="2")])

        def check(*mods: FabricMod) -> None:
            specs = [
                Metadata(
                    m.id, {"side": m.environment if m.environment != "*" else "both"}
                )
                for m in mods
            ]
            validate_fabric_dependencies(
                "26.3",
                loader,
                specs,
                {m.id: m.id for m in mods},
                {m.id: m for m in mods},
            )

        check(parent, newer, consumer, blocker)
        check(parent, newer, replace(consumer, depends={"alias": ["<2"]}))
        with self.assertRaisesRegex(ValueError, "breaks 'consumer'"):
            check(
                replace(parent, jars=[replace(library, breaks={"consumer": ["*"]})]),
                consumer,
            )
        with self.assertRaisesRegex(ValueError, "requires 'absent'"):
            check(
                replace(parent, jars=[replace(library, depends={"absent": ["*"]})]),
                consumer,
            )
        client = replace(library, environment="client", jars=[fabric_mod("child")])
        check(
            replace(parent, jars=[client]),
            replace(
                blocker, environment="server", breaks={"child": ["*"], "library": ["*"]}
            ),
        )
        with self.assertRaisesRegex(ValueError, r"\[server\].*requires 'child'"):
            check(
                replace(parent, jars=[client]),
                replace(consumer, depends={"child": ["*"]}),
            )
        # A child's provider is inaccessible when a different version of its parent is forced.
        with self.assertRaises(ValueError):
            check(
                replace(parent, jars=[replace(library, jars=[fabric_mod("child")])]),
                newer,
                replace(consumer, depends={"library": [">=2"], "child": ["*"]}),
            )

    def test_loader_libraries_and_soft_conflicts(self) -> None:
        loader = replace(
            fabric_mod("fabricloader", "0.20.1"),
            jars=[fabric_mod("mixinextras", "0.5.5")],
        )
        spec = Metadata("client", {"side": "client"})

        def check(mod: FabricMod) -> None:
            validate_fabric_dependencies(
                "26.3", loader, [spec], {"client": "hash"}, {"hash": mod}
            )

        mod = fabric_mod("consumer")
        with self.assertRaisesRegex(ValueError, "requires 'mixinextras'"):
            check(replace(mod, depends={"mixinextras": [">=1.0"]}))
        with self.assertRaisesRegex(ValueError, "breaks 'mixinextras'"):
            check(replace(mod, breaks={"mixinextras": ["*"]}))
        with redirect_stderr(io.StringIO()) as log:
            check(
                replace(
                    mod,
                    depends={"mixinextras": [">=0.5"]},
                    conflicts={"mixinextras": ["*"]},
                )
            )
        self.assertIn(
            "Warning: [client] consumer conflicts with mixinextras 0.5.5",
            log.getvalue(),
        )

        newer = fabric_mod("mixinextras", "1.0")
        validate_fabric_dependencies(
            "26.3",
            loader,
            [spec, Metadata("external", {"side": "client"})],
            {"client": "consumer", "external": "standalone"},
            {
                "consumer": replace(mod, depends={"mixinextras": [">=1.0"]}),
                "standalone": newer,
            },
        )

    def test_jar_schema_and_read_limits(self) -> None:
        def jar(manifest: dict[str, object], nested: bytes | None = None) -> bytes:
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("fabric.mod.json", json.dumps(manifest))
                if nested is not None:
                    archive.writestr("lib.jar", nested)
            return stream.getvalue()

        manifest: dict[str, object] = {
            "schemaVersion": 1,
            "id": "sample",
            "version": "1",
        }
        leaf = jar(manifest)
        parent: dict[str, object] = {**manifest, "jars": [{"file": "lib.jar"}]}
        for version in (0, 2):
            invalid = jar({**manifest, "schemaVersion": version})
            for data in (invalid, jar(parent, invalid)):
                with self.assertRaisesRegex(ValueError, "schemaVersion"):
                    parse_fabric_jar(data)
        with self.assertRaisesRegex(ValueError, "schemaVersion 0"):
            parse_fabric_jar(jar({"id": "legacy", "version": "1"}))
        with (
            patch("minecraft_pack.fabric_deps.MAX_JAR_DEPTH", 0),
            self.assertRaisesRegex(ValueError, "nesting"),
        ):
            parse_fabric_jar(jar(parent, leaf))
        with (
            patch("minecraft_pack.fabric_deps.MAX_EXPANDED_BYTES", len(leaf)),
            self.assertRaisesRegex(ValueError, "size limit"),
        ):
            parse_fabric_jar(jar(parent, leaf))
        with (
            patch("minecraft_pack.fabric_deps.MAX_MANIFEST_BYTES", 10),
            self.assertRaisesRegex(ValueError, "size limit"),
        ):
            parse_fabric_jar(leaf)
        url = "https://cdn.modrinth.com/fixture"
        digest = hashlib.sha512(leaf).hexdigest()
        with patch("minecraft_pack.fabric_deps.urlopen", return_value=io.BytesIO(leaf)):
            self.assertEqual(
                fetch_fabric_metadata([(digest, url, len(leaf))], {})[digest].id,
                "sample",
            )
        with (
            patch("minecraft_pack.fabric_deps.urlopen", return_value=io.BytesIO(leaf)),
            self.assertRaisesRegex(ValueError, "size limit"),
        ):
            fetch_fabric_metadata([(digest, url, len(leaf) - 1)], {})
        with (
            patch("minecraft_pack.fabric_deps.MAX_JAR_BYTES", 10),
            patch("minecraft_pack.fabric_deps.urlopen") as opened,
        ):
            with self.assertRaisesRegex(ValueError, "artifact size"):
                fetch_fabric_metadata([(digest, url, len(leaf))], {})
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
