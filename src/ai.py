"""
AI provider selection: which model family answers a given user.

Gemini is the default and does everything. OpenAI is an optional second provider (text, tools, JSON, image/PDF,
voice transcription, web search); embeddings (semantic search) and image generation stay on Gemini.

How it works
- A user's choice is stored in `ai_preferences`; `AI_DEFAULT_PROVIDER` applies until they choose.
- The provider for the current request is held in a ContextVar, set around message handling (`use_user`) and around
  per-user background jobs, so two users handled at the same time can never see each other's provider.
- The four LLM entry points (`call_gemini_json`, `call_gemini_json_with_media`, `search_web`, `classify_with_tools`) check
  `current_provider()` and hand over to that provider's adapter module. Domain tools, confirmations and permission rules
  run in the same dispatcher for every provider; an adapter only turns text/media into JSON or one tool call.
- No silent failover: if the chosen provider is down or unconfigured the user is told, and nothing is sent to the other one.

To add a provider, see docs/ADDING_A_PROVIDER.md.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from importlib import import_module
from inspect import signature
from typing import Any, Callable, Iterator

from src import config


@dataclass(frozen=True)
class ProviderSpec:
    name: str  # the value stored per user
    label: str  # how the user sees it
    aliases: tuple[str, ...]  # words that select it in a chat command (lowercase)
    adapter: str  # module with call_json / call_json_with_media / search_web / classify_with_tools
    is_configured: Callable[[], bool]
    model: Callable[[], str]


PROVIDERS: dict[str, ProviderSpec] = {
    "gemini": ProviderSpec(
        "gemini", "Gemini", ("gemini", "ג׳מיני", "ג'מיני", "גמיני"), "src.integrations.gemini",
        lambda: bool(config.GEMINI_API_KEY), lambda: config.GEMINI_MODEL,
    ),
    "openai": ProviderSpec(
        "openai", "OpenAI", ("openai", "open ai", "אופן איי איי"), "src.integrations.openai",
        lambda: bool(config.OPENAI_API_KEY and config.OPENAI_MODEL), lambda: config.OPENAI_MODEL,
    ),
}

_provider: ContextVar[str | None] = ContextVar("ai_provider", default=None)


def default_provider() -> str:
    return config.AI_DEFAULT_PROVIDER if config.AI_DEFAULT_PROVIDER in PROVIDERS else "gemini"


def current_provider() -> str:
    return _provider.get() or default_provider()


def get_adapter(name: str) -> Any:
    return import_module(PROVIDERS[name].adapter)


def configured(name: str) -> bool:
    return name in PROVIDERS and PROVIDERS[name].is_configured()


def provider_for(user: Any) -> str:
    from src.db.models import get_ai_provider

    chosen = get_ai_provider(dict(user)["id"])
    return chosen if chosen in PROVIDERS else default_provider()


@contextmanager
def use_provider(name: str) -> Iterator[None]:
    if name not in PROVIDERS:
        raise ValueError("Unsupported AI provider")
    token = _provider.set(name)
    try:
        yield
    finally:
        _provider.reset(token)


@contextmanager
def use_user(user: Any) -> Iterator[None]:
    with use_provider(provider_for(user)):
        yield


def for_user(function: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for standalone functions with a `user` argument that call the LLM (background jobs)."""
    sig = signature(function)

    @wraps(function)
    def scoped(*args: Any, **kwargs: Any) -> Any:
        user = sig.bind(*args, **kwargs).arguments["user"]
        with use_user(user):
            return function(*args, **kwargs)

    return scoped


def unavailable_reply() -> str:
    spec = PROVIDERS[current_provider()]
    return f"{spec.label} אינו זמין כרגע. לא ביצעתי פעולה. אפשר לנסות שוב או לבקש לעבור לספק השני."


def manage_provider(user: Any, args: dict) -> str:
    """The `manage_ai_provider` tool and the exact chat commands. Only ever touches the calling user's own setting."""
    from src.db.models import set_ai_provider

    action = args.get("action")
    provider = args.get("provider")
    if action == "set":
        if provider not in PROVIDERS:
            return "באיזה ספק להשתמש: " + " או ".join(s.label for s in PROVIDERS.values()) + "?"
        if not configured(provider):
            return "הספק הזה עדיין לא מוגדר. מי שמפעיל את הבוט צריך להגדיר את מפתח ה-API ואת שם המודל."
        set_ai_provider(user["id"], provider)
        return f"מעכשיו אשתמש ב-{PROVIDERS[provider].label} עבורך, גם בעדכונים היזומים שלך."
    if action != "status":
        return "אפשר לבקש לעבור לספק אחר, או לבדוק באיזה ספק אתה משתמש."
    spec = PROVIDERS[provider_for(user)]
    others = [s.label for s in PROVIDERS.values() if s.name != spec.name and s.is_configured()]
    extra = f"\nאפשר לעבור ל: {', '.join(others)}." if others else ""
    return f"הספק שלך: {spec.label}. המודל: {spec.model()}.\nחיפוש בזיכרון ויצירת תמונות משתמשים תמיד ב-Gemini.{extra}"


_SWITCH_PREFIXES = ("עבור ל־", "עבור ל-", "עבור ל", "תעבור ל־", "תעבור ל-", "השתמש ב־", "השתמש ב-", "חזור ל־", "חזור ל-", "switch to ", "use ")
_STATUS_PHRASES = ("באיזה ספק אני משתמש", "באיזה מודל אני משתמש", "איזה ספק", "ai status", "which ai", "which provider")


def provider_command(text: str, user: Any) -> str | None:
    """
    Exact commands ("עבור ל־OpenAI", "switch to gemini", "באיזה ספק אני משתמש") work without any model call, so they
    still work while the selected provider is down. The whole message must be the command: a sentence that merely
    contains one, or any forwarded/quoted content (the caller never passes it here), cannot change a setting.
    """
    text = text.strip().lower().rstrip(".!?")
    if text in _STATUS_PHRASES:
        return manage_provider(user, {"action": "status"})
    for spec in PROVIDERS.values():
        for alias in spec.aliases:
            if text in tuple(prefix + alias for prefix in _SWITCH_PREFIXES):
                return manage_provider(user, {"action": "set", "provider": spec.name})
    return None
