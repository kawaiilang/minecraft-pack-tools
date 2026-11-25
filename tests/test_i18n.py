"""Unit tests for GUI localization catalogs, locale detection, and ASCII-only GUI source."""

import string
import unittest
from importlib import resources

from minecraft_pack.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    detect_locale,
    load_catalog,
    load_translator,
)


def _format_fields(template: str) -> set[str]:
    formatter = string.Formatter()
    return {
        field_name
        for _, field_name, _, _ in formatter.parse(template)
        if field_name is not None
    }


class I18nTests(unittest.TestCase):
    def test_catalogs_share_keys_and_placeholders(self) -> None:
        base = load_catalog(DEFAULT_LOCALE)
        self.assertTrue(base)
        for key, value in base.items():
            self.assertTrue(value.strip(), f"{DEFAULT_LOCALE}:{key} must not be empty")
        for locale in SUPPORTED_LOCALES:
            catalog = load_catalog(locale)
            self.assertEqual(set(catalog), set(base), f"Key mismatch in {locale}.json")
            for key, value in catalog.items():
                self.assertTrue(value.strip(), f"{locale}:{key} must not be empty")
                self.assertEqual(
                    _format_fields(value),
                    _format_fields(base[key]),
                    f"Placeholder mismatch for {locale}:{key}",
                )

    def test_gui_source_is_english_only(self) -> None:
        text = (
            resources.files("minecraft_pack")
            .joinpath("gui.py")
            .read_text(encoding="utf-8")
        )
        non_ascii = [
            (line_no, line)
            for line_no, line in enumerate(text.splitlines(), 1)
            if not line.isascii()
        ]
        self.assertEqual(
            non_ascii,
            [],
            f"gui.py must stay English/ASCII-only; found non-ASCII lines: {non_ascii}",
        )

    def test_detect_locale_precedence_and_fallbacks(self) -> None:
        self.assertEqual(detect_locale(environ={}), "en")
        self.assertEqual(detect_locale(environ={"LANG": "ja_JP.UTF-8"}), "ja")
        self.assertEqual(detect_locale(environ={"LANG": "en_US.UTF-8"}), "en")
        self.assertEqual(detect_locale(environ={"LANG": "fr_FR.UTF-8"}), "en")
        self.assertEqual(
            detect_locale(environ={"LANG": "en_US.UTF-8", "LC_MESSAGES": "ja_JP.utf8"}),
            "ja",
        )
        self.assertEqual(
            detect_locale(
                environ={"LANG": "en_US.UTF-8", "LANGUAGE": "de_DE:ja_JP:en"}
            ),
            "ja",
        )
        self.assertEqual(
            detect_locale(
                environ={
                    "LC_ALL": "C",
                    "LANGUAGE": "ja_JP",
                    "LC_MESSAGES": "ja_JP.UTF-8",
                    "LANG": "ja_JP.UTF-8",
                }
            ),
            "en",
        )
        self.assertEqual(
            detect_locale(
                environ={"LC_ALL": "ja_JP.UTF-8", "LANG": "en_US.UTF-8"}
            ),
            "ja",
        )
        self.assertEqual(
            detect_locale(explicit="ja", environ={"LC_ALL": "C"}),
            "ja",
        )
        self.assertEqual(
            detect_locale(explicit="en-US", environ={"LANG": "ja_JP.UTF-8"}),
            "en",
        )
        with self.assertRaises(ValueError):
            detect_locale(explicit="fr")

    def test_translator_formatting(self) -> None:
        en = load_translator(explicit="en")
        ja = load_translator(explicit="ja")
        self.assertEqual(en.tr("window.title"), "Minecraft Pack Manager")
        self.assertEqual(ja.tr("window.title"), "Minecraft パック管理")
        self.assertEqual(en.tr("java.target", version="21"), "Target Java: 21")
        self.assertEqual(ja.tr("java.target", version="21"), "対象 Java: 21")
        self.assertEqual(en.side_label("both"), "Both")
        self.assertEqual(ja.side_label("both"), "両方")


if __name__ == "__main__":
    unittest.main()
