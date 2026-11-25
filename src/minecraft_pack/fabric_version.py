"""Fabric Loader version predicates, including partial versions and prereleases."""

import re
from dataclasses import dataclass
from functools import lru_cache

_CORE = re.compile(
    r"([0-9]+(?:\.[0-9]+)*)(?:-((?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)?))?(?:\+.*)?",
    re.DOTALL,
)
_WILDCARD = re.compile(r"([0-9]+(?:\.[0-9]+)*)(?:\.[xX*])+(?:\+.*)?", re.DOTALL)
_NUMERIC = re.compile(r"0|[1-9][0-9]*")


@dataclass(frozen=True)
class _Version:
    components: tuple[int, ...]
    prerelease: str | None

    def padded(self, size: int) -> tuple[int, ...]:
        return self.components + (0,) * max(0, size - len(self.components))


@lru_cache(maxsize=4096)
def _parse(value: str) -> _Version | None:
    match = _CORE.fullmatch(value)
    if match is None:
        return None
    components: list[int] = []
    for part in match[1].split("."):
        digits = part.lstrip("0") or "0"
        if len(digits) > 10 or int(digits) > 2147483647:
            return None
        components.append(int(digits))
    return _Version(tuple(components), match[2])


def _compare(left: _Version, right: _Version) -> int:
    size = max(len(left.components), len(right.components))
    a, b = left.padded(size), right.padded(size)
    if a != b:
        return 1 if a > b else -1
    if left.prerelease == right.prerelease:
        return 0
    if left.prerelease is None:
        return 1
    if right.prerelease is None:
        return -1
    first = left.prerelease.split(".") if left.prerelease else []
    second = right.prerelease.split(".") if right.prerelease else []
    for x, y in zip(first, second, strict=False):
        if x == y:
            continue
        nx, ny = bool(_NUMERIC.fullmatch(x)), bool(_NUMERIC.fullmatch(y))
        if nx != ny:
            return -1 if nx else 1
        if nx and len(x) != len(y):
            return 1 if len(x) > len(y) else -1
        return 1 if x > y else -1
    return len(first) - len(second)


def compare_versions(left: str, right: str) -> int:
    a, b = _parse(left), _parse(right)
    if a is not None and b is not None:
        return _compare(a, b)
    return (left > right) - (left < right)


def _matches_term(version: str, term: str) -> bool:
    if term in ("", "*"):
        return True
    op = next(
        (op for op in (">=", "<=", ">", "<", "=", "~", "^") if term.startswith(op)), ""
    )
    raw = term[len(op) :]
    if not raw:
        raise ValueError(f"Empty version in Fabric predicate '{term}'")
    wildcard = _WILDCARD.fullmatch(raw)
    target = _parse(wildcard[1] if wildcard else raw)
    actual = _parse(version)
    if target is None:
        if op in (">", "<"):
            raise ValueError(
                f"Exclusive Fabric predicate requires a semantic version: {term}"
            )
        return version == raw
    if wildcard:
        if op not in ("", "="):
            raise ValueError(f"Invalid operator in Fabric wildcard predicate: {term}")
        prefix = target.components
        return (
            actual is not None and actual.padded(len(prefix))[: len(prefix)] == prefix
        )
    if actual is None:
        return op not in (">", "<") and version == raw
    order = _compare(actual, target)
    if op in ("^", "~"):
        size = 1 if op == "^" else 2
        return order >= 0 and actual.padded(size)[:size] == target.padded(size)[:size]
    return {
        ">=": order >= 0,
        "<=": order <= 0,
        ">": order > 0,
        "<": order < 0,
        "=": order == 0,
        "": order == 0,
    }[op]


@lru_cache(maxsize=8192)
def matches_predicate(version: str, predicate: str) -> bool:
    return all(_matches_term(version, term.strip()) for term in predicate.split(" "))


def matches_any(version: str, predicates: list[str]) -> bool:
    return any(matches_predicate(version, predicate) for predicate in predicates)
