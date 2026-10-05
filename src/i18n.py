"""
Locale support for every message the bot's own code composes (confirmations,
reminders, alerts, privacy text). The model's replies are shaped through
the `language_name()` instruction in the prompts instead.

    t("reminder.single", prefix=" from Dana", content="call the plumber")

Catalogs are plain dicts in src/locales/<locale>.py. Hebrew (`he`) is the
reference catalog: every key must exist there, and a key missing from another
catalog falls back to Hebrew rather than failing in front of a user.
tests/test_i18n.py enforces key and placeholder parity between catalogs.

The locale is the LOCALE env var (default `he`), read at call time so tests
can switch it. Adding a language = a new src/locales/<code>.py plus an entry
in SUPPORTED_LOCALES.
"""
from importlib import import_module

from src import config

SUPPORTED_LOCALES = ("he", "en")
DEFAULT_LOCALE = "he"

_LANGUAGE_NAMES = {"he": "עברית", "en": "English"}
_ENGLISH_NAMES = {"he": "Hebrew", "en": "English"}

_catalogs: dict[str, dict[str, str]] = {}


def current_locale() -> str:
    locale = (config.LOCALE or DEFAULT_LOCALE).lower()
    return locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE


def catalog(locale: str) -> dict[str, str]:
    if locale not in _catalogs:
        _catalogs[locale] = import_module(f"src.locales.{locale}").MESSAGES
    return _catalogs[locale]


def t(key: str, **params) -> str:
    """Looks `key` up in the current locale (falling back to Hebrew) and formats it with `params`.

    Without params the text is returned untouched, so catalog entries that
    hold strftime patterns or literal braces need no escaping.
    """
    text = catalog(current_locale()).get(key)
    if text is None:
        text = catalog(DEFAULT_LOCALE)[key]  # a key missing from `he` is a bug: raise
    return text.format(**params) if params else text


def answer_language_line() -> str:
    """A one-line "write the user-facing text in <language>" instruction to append to model prompts."""
    return t("lang.answer_in")


def has(key: str) -> bool:
    """Whether `key` exists in the Hebrew reference catalog (i.e. t(key) will not raise)."""
    return key in catalog(DEFAULT_LOCALE)


def language_name() -> str:
    """The current language's name in its own language, for "answer in ..." prompt instructions."""
    return _LANGUAGE_NAMES[current_locale()]


def language_name_english() -> str:
    """The current language's English name, for English-language system instructions ("Answer in ...")."""
    return _ENGLISH_NAMES[current_locale()]

