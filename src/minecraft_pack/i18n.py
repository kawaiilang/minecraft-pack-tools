"""Lightweight JSON-backed localization for the GTK4 GUI."""

import os
from collections.abc import Mapping
from importlib import resources

from . import json_guard

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES: tuple[str, ...] = ("en", "ja")


def _normalize_locale_tag(raw: str) -> str | None:
    cleaned = raw.strip()
    if not cleaned:
        return None
    base = cleaned.split(".", 1)[0].split("@", 1)[0].replace("-", "_").lower()
    if base in ("c", "posix"):
        return DEFAULT_LOCALE
    lang = base.split("_", 1)[0]
    if lang in SUPPORTED_LOCALES:
        return lang
    return None


def detect_locale(
    explicit: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve UI locale from explicit flag or POSIX locale environment variables."""
    if explicit is not None:
        normalized = _normalize_locale_tag(explicit)
        if normalized is None:
            supported = ", ".join(SUPPORTED_LOCALES)
            raise ValueError(
                f"Unsupported language {explicit!r}; expected one of: {supported}"
            )
        return normalized

    env = os.environ if environ is None else environ

    lc_all = env.get("LC_ALL", "").strip()
    if lc_all:
        normalized = _normalize_locale_tag(lc_all)
        return normalized if normalized is not None else DEFAULT_LOCALE

    language_list = env.get("LANGUAGE", "").strip()
    if language_list:
        for entry in language_list.split(":"):
            stripped = entry.strip()
            if not stripped:
                continue
            base = stripped.split(".", 1)[0].split("@", 1)[0].lower()
            if base in ("c", "posix"):
                return DEFAULT_LOCALE
            normalized = _normalize_locale_tag(stripped)
            if normalized is not None:
                return normalized

    for key in ("LC_MESSAGES", "LANG"):
        value = env.get(key, "").strip()
        if value:
            normalized = _normalize_locale_tag(value)
            return normalized if normalized is not None else DEFAULT_LOCALE

    return DEFAULT_LOCALE


def load_catalog(locale: str) -> dict[str, str]:
    """Load and validate a locale JSON catalog shipped inside the package."""
    if locale not in SUPPORTED_LOCALES:
        raise ValueError(f"Unsupported locale catalog: {locale!r}")
    resource = resources.files(__package__).joinpath("locales", f"{locale}.json")
    raw = json_guard.record(json_guard.loads(resource.read_text(encoding="utf-8")))
    return {key: json_guard.text(value) for key, value in raw.items()}


class Translator:
    """Translate UI keys with English fallback."""

    def __init__(self, locale: str) -> None:
        self.locale = locale
        self._fallback = load_catalog(DEFAULT_LOCALE)
        self._messages = (
            self._fallback if locale == DEFAULT_LOCALE else load_catalog(locale)
        )

    def tr(self, key: str, **kwargs: object) -> str:
        template = self._messages.get(key)
        if template is None:
            template = self._fallback[key]
        return template.format(**kwargs) if kwargs else template

    def side_label(self, side: str) -> str:
        return self.tr(f"side.{side}")


def load_translator(
    explicit: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Translator:
    return Translator(detect_locale(explicit=explicit, environ=environ))
