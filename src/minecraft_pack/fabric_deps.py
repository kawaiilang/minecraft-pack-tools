"""Read bounded Fabric JAR metadata and resolve the selected pack on each side."""

import hashlib
import io
import json as decoder
import re
import sys
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from functools import cmp_to_key
from urllib.request import Request, urlopen

from . import json_guard as json
from .data import Metadata, require
from .fabric_version import compare_versions, matches_any

MAX_JAR_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_JAR_DEPTH = 8
MAX_NESTED_JARS = 512


def _environment(value: object) -> str:
    side = json.text(value)
    require(side in ("client", "server", "*"), f"Invalid Fabric environment: {side}")
    return side


@dataclass(frozen=True)
class FabricMod:
    id: str
    version: str
    environment: str
    provides: list[str]
    depends: dict[str, list[str]]
    breaks: dict[str, list[str]]
    conflicts: dict[str, list[str]]
    jars: list["FabricMod"]

    @classmethod
    def parse(cls, value: object, depth: int = 0) -> "FabricMod":
        """Read the complete, normalized lock format (every field is required)."""
        require(depth <= MAX_JAR_DEPTH, "Fabric lock exceeds nesting limit")
        data = json.record(value)

        def constraints(key: str) -> dict[str, list[str]]:
            return {k: json.strings(v) for k, v in json.record(data.get(key)).items()}

        return cls(
            json.text(data.get("id")),
            json.text(data.get("version")),
            _environment(data.get("environment")),
            json.strings(data.get("provides")),
            constraints("depends"),
            constraints("breaks"),
            constraints("conflicts"),
            [cls.parse(item, depth + 1) for item in json.array(data.get("jars"))],
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _jar_constraints(value: object) -> dict[str, list[str]]:
    return {
        key: [raw] if isinstance(raw, str) else json.strings(raw)
        for key, raw in json.record(value).items()
    }


@dataclass
class _ArchiveBudget:
    remaining: int
    jars: int = 0

    def read(self, archive: zipfile.ZipFile, path: str, limit: int) -> bytes:
        info = archive.getinfo(path)
        require(
            info.file_size <= min(limit, self.remaining),
            f"{path}: expanded size limit exceeded",
        )
        self.remaining -= info.file_size
        require(
            info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
            f"{path}: unsupported JAR compression method",
        )
        with archive.open(info) as stream:
            data = stream.read(info.file_size + 1)
        require(len(data) == info.file_size, f"{path}: invalid ZIP member size")
        return data


def _parse_jar(data: bytes, budget: _ArchiveBudget, depth: int) -> FabricMod | None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = set(archive.namelist())
        if "fabric.mod.json" not in members:
            return None
        raw = budget.read(archive, "fabric.mod.json", MAX_MANIFEST_BYTES)
        # Loader accepts literal control characters in descriptive strings.
        manifest: object = decoder.loads(raw.decode("utf-8"), strict=False)
        fmj = json.record(manifest)
        schema = json.integer(fmj.get("schemaVersion", 0))
        require(schema == 1, f"Unsupported fabric.mod.json schemaVersion {schema}")
        nested: list[FabricMod] = []
        for item in json.array(fmj.get("jars", [])):
            path = json.text(json.record(item).get("file"))
            if path not in members:
                continue
            require(depth < MAX_JAR_DEPTH, f"{path}: JAR nesting limit exceeded")
            budget.jars += 1
            require(budget.jars <= MAX_NESTED_JARS, "Too many nested JARs")
            child = _parse_jar(
                budget.read(archive, path, MAX_JAR_BYTES), budget, depth + 1
            )
            if child is not None:
                nested.append(child)
        return FabricMod(
            json.text(fmj.get("id")),
            json.text(fmj.get("version")),
            _environment(fmj.get("environment", "*")),
            sorted(json.strings(fmj.get("provides", []))),
            _jar_constraints(fmj.get("depends", {})),
            _jar_constraints(fmj.get("breaks", {})),
            _jar_constraints(fmj.get("conflicts", {})),
            nested,
        )


def parse_fabric_jar(data: bytes) -> FabricMod:
    require(len(data) <= MAX_JAR_BYTES, "Mod JAR exceeds download size limit")
    mod = _parse_jar(data, _ArchiveBudget(MAX_EXPANDED_BYTES), 0)
    if mod is None:
        raise ValueError("Mod JAR is missing fabric.mod.json")
    return mod


def _download(url: str, limit: int) -> bytes:
    request = Request(url, headers={"User-Agent": "minecraft-pack-tools/1"})
    with urlopen(request, timeout=60) as response:
        data: bytes = response.read(limit + 1)
    require(len(data) <= limit, f"{url}: download size limit exceeded")
    return data


def fetch_fabric_metadata(
    artifacts: list[tuple[str, str, int]], cached: dict[str, FabricMod]
) -> dict[str, FabricMod]:
    result: dict[str, FabricMod] = {}
    for digest, url, size in sorted(set(artifacts)):
        require(
            0 < size <= MAX_JAR_BYTES, f"{url}: invalid Modrinth artifact size {size}"
        )
        if digest in cached:
            result[digest] = cached[digest]
            continue
        data = _download(url, size)
        require(len(data) == size, f"{url}: artifact size mismatch")
        require(hashlib.sha512(data).hexdigest() == digest, f"{url}: JAR hash mismatch")
        result[digest] = parse_fabric_jar(data)
    return result


def fetch_loader(version: str) -> FabricMod:
    require(
        re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.+_-]*", version) is not None,
        "Invalid Fabric Loader version",
    )
    url = f"https://maven.fabricmc.net/net/fabricmc/fabric-loader/{version}/fabric-loader-{version}.jar"
    checksum = _download(url + ".sha256", 256).decode("ascii").strip()
    payload = _download(url, MAX_JAR_BYTES)
    require(
        hashlib.sha256(payload).hexdigest() == checksum,
        "Fabric Loader JAR hash mismatch",
    )
    loader = parse_fabric_jar(payload)
    require(
        loader.id == "fabricloader" and loader.version == version,
        "Fabric Loader version mismatch",
    )
    return loader


@dataclass(frozen=True, eq=False)
class _Candidate:
    mod: FabricMod
    source: str
    parent: "_Candidate | None"


class _Incompatible(ValueError):
    """A candidate selection contradicts a hard constraint."""


def _resolve(side: str, roots: list[tuple[str, FabricMod]]) -> None:
    candidates: list[_Candidate] = []
    required: list[_Candidate] = []

    def collect(mod: FabricMod, source: str, parent: _Candidate | None) -> None:
        if mod.environment not in ("*", side):
            return
        candidate = _Candidate(mod, source, parent)
        candidates.append(candidate)
        if parent is None:
            required.append(candidate)
        for nested in mod.jars:
            collect(nested, source, candidate)

    for source, mod in roots:
        collect(mod, source, None)

    # Prefer the newer candidate only among solutions satisfying every hard constraint.
    candidates.sort(
        key=cmp_to_key(lambda a, b: compare_versions(a.mod.version, b.mod.version)),
        reverse=True,
    )
    by_id: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        for name in dict.fromkeys([candidate.mod.id, *candidate.mod.provides]):
            by_id[name].append(candidate)

    def choose(selected: dict[str, _Candidate], candidate: _Candidate) -> None:
        mod = candidate.mod
        if selected.get(mod.id) is candidate:
            return
        if candidate.parent is not None:
            choose(selected, candidate.parent)
        for name in (mod.id, *mod.provides):
            other = selected.get(name)
            if other is not None and other is not candidate:
                raise _Incompatible(
                    f"[{side}] duplicate '{name}' in {candidate.source} and {other.source}"
                )
            selected[name] = candidate

    def alternatives(selected: dict[str, _Candidate]) -> list[_Candidate] | None:
        # Reject impossible constraints before branching. Prefer the most restricted
        # dependency, including the common case with a single possible provider.
        best: list[_Candidate] | None = None
        for current in dict.fromkeys(selected.values()):
            mod = current.mod
            context = f"[{side}] {current.source} ({mod.id} {mod.version})"
            for name, terms in mod.breaks.items():
                other = selected.get(name)
                if other is not None and matches_any(other.mod.version, terms):
                    raise _Incompatible(
                        f"{context} breaks '{name}' {terms}, selected {other.mod.version}"
                    )
            for name, terms in mod.depends.items():
                active = selected.get(name)
                if active is not None:
                    if not matches_any(active.mod.version, terms):
                        raise _Incompatible(
                            f"{context} requires '{name}' {terms}, selected {active.mod.version}"
                        )
                    continue
                options = [
                    provider
                    for provider in by_id.get(name, [])
                    if matches_any(provider.mod.version, terms)
                ]
                if not options:
                    raise _Incompatible(
                        f"{context} requires '{name}' {terms}, no matching provider"
                    )
                if best is None or len(options) < len(best):
                    best = options
        return best

    def solve(selected: dict[str, _Candidate]) -> dict[str, _Candidate]:
        while options := alternatives(selected):
            for provider in options[:-1]:
                trial = selected.copy()
                try:
                    choose(trial, provider)
                    return solve(trial)
                except _Incompatible:
                    continue
            choose(selected, options[-1])
        return selected

    selection: dict[str, _Candidate] = {}
    for root in required:
        choose(selection, root)
    selection = solve(selection)
    # Loader attempts to include the remaining nested libraries if compatible.
    for candidate in candidates:
        if candidate.mod.id in selection:
            continue
        trial = selection.copy()
        try:
            choose(trial, candidate)
            selection = solve(trial)
        except _Incompatible:
            continue
    for current in dict.fromkeys(selection.values()):
        for name, terms in current.mod.conflicts.items():
            other = selection.get(name)
            if other is not None and matches_any(other.mod.version, terms):
                print(
                    f"Warning: [{side}] {current.mod.id} conflicts with {name} {other.mod.version} ({terms})",
                    file=sys.stderr,
                )


def validate_fabric_dependencies(
    minecraft: str,
    loader: FabricMod,
    specs: list[Metadata],
    sha512_by_path: dict[str, str],
    fabric_by_hash: dict[str, FabricMod],
    java_version: str,
) -> None:
    mods: list[tuple[Metadata, FabricMod]] = []
    owners: dict[str, str] = {}
    for spec in specs:
        digest = sha512_by_path.get(spec.path)
        meta = fabric_by_hash.get(digest) if digest is not None else None
        if meta is None:
            raise ValueError(
                f"{spec.path}: missing Fabric metadata in modrinth-lock.json"
            )
        if meta.id in owners:
            raise ValueError(
                f"Duplicate top-level Fabric ID '{meta.id}' in {owners[meta.id]} and {spec.path}"
            )
        owners[meta.id] = spec.path
        require(
            meta.environment in ("*", spec.side),
            f"{spec.path}: fabric.mod.json environment '{meta.environment}' disagrees with pack side '{spec.side}'",
        )
        mods.append((spec, meta))

    builtins = [
        (name, FabricMod(name, version, "*", [], {}, {}, {}, []))
        for name, version in (("minecraft", minecraft), ("java", java_version))
    ]
    for side in ("client", "server"):
        roots = [(spec.path, mod) for spec, mod in mods if spec.side in ("both", side)]
        # Loader-bundled IDs must be present. A compatible explicit mod may
        # supply the same library at a different version.
        libraries = {
            lib.id: ["*"] for lib in loader.jars if lib.environment in ("*", side)
        }
        runtime = replace(loader, depends={**libraries, **loader.depends})
        _resolve(side, [*builtins, ("Fabric Loader", runtime), *roots])
