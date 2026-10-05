"""src/i18n.py (2026-10-04) - locale lookup, fallback and catalog parity."""
import re
import string

import pytest

from src import config, i18n


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_locale_covers_exactly_the_hebrew_keys():
    reference = i18n.catalog("he")
    for locale in i18n.SUPPORTED_LOCALES:
        assert set(i18n.catalog(locale)) == set(reference), f"{locale} keys differ from he"


def test_every_locale_uses_the_same_placeholders_as_hebrew():
    reference = i18n.catalog("he")
    for locale in i18n.SUPPORTED_LOCALES:
        for key, text in i18n.catalog(locale).items():
            assert _placeholders(text) == _placeholders(reference[key]), f"{locale}:{key}"


class _Anything:
    """Formats under any spec (e.g. {cost:.2f}), so placeholders need no per-key dummy values."""

    def __format__(self, spec):
        return "x"


def test_every_catalog_entry_formats_with_dummy_params():
    for locale in i18n.SUPPORTED_LOCALES:
        for key, text in i18n.catalog(locale).items():
            text.format(**{name: _Anything() for name in _placeholders(text)})


def test_t_formats_params_in_the_current_locale(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "he")
    assert i18n.t("schedule.daily", time="08:00") == "כל יום ב-08:00"
    monkeypatch.setattr(config, "LOCALE", "en")
    assert i18n.t("schedule.daily", time="08:00") == "Every day at 08:00"


def test_t_without_params_returns_the_text_untouched(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "en")
    assert i18n.t("schedule.once_when_format") == "%d/%m at %H:%M"


def test_a_key_missing_from_a_locale_falls_back_to_hebrew(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "en")
    monkeypatch.setitem(i18n.catalog("he"), "only.in.he", "רק בעברית")
    assert i18n.t("only.in.he") == "רק בעברית"


def test_a_key_missing_everywhere_raises():
    with pytest.raises(KeyError):
        i18n.t("no.such.key")


def test_unknown_locale_value_uses_hebrew(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "fr")
    assert i18n.current_locale() == "he"


def test_language_name_follows_the_locale(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "en")
    assert i18n.language_name() == "English"
    monkeypatch.setattr(config, "LOCALE", "he")
    assert i18n.language_name() == "עברית"


def test_locale_env_var_is_validated():
    import importlib
    import os

    os.environ["LOCALE"] = "xx"
    try:
        with pytest.raises(ValueError):
            importlib.reload(config)
    finally:
        os.environ["LOCALE"] = "he"
        importlib.reload(config)
