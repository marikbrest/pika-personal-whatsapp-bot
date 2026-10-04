"""
Pluggable checkers for the generic watch engine (src.scheduler.check_watches).

Each checker's only job is "what is the current state of this target right
now", as a comparable string - the scheduler owns all change-detection and
notification logic, exactly like check_and_notify_package_changes already
does for packages specifically. Adding a new watch_type means adding one
checker function here and one line in CHECKERS/NOTIFY_MESSAGES; nothing in
scheduler.py needs to change.

A checker returns None to mean "could not determine the state this time"
(a transient failure) - the scheduler treats that as "try again next cycle",
never as "nothing changed" and never as a reason to notify.
"""

from src.i18n import t


def check_email_reply(user_id: int, target: str) -> str | None:
    """
    target is a Gmail thread_id. State is the thread's message count: a
    reply is the only way that count can grow, so comparing it catches every
    real reply without needing to inspect message content.
    """
    from src.integrations.gmail import get_thread_messages

    messages = get_thread_messages(user_id, target)
    if not messages:
        return None
    return str(len(messages))


def check_web_page(user_id: int, target: str) -> str | None:
    """
    target is a URL. State is the extracted article text itself (already
    capped at MAX_SNAPSHOT_CHARS by fetch_and_extract) - a real content
    comparison, not an opaque hash that couldn't tell a caller what changed
    even if it wanted to.
    """
    from src.integrations.content_extractor import fetch_and_extract

    result = fetch_and_extract(target)
    if result["fetch_status"] != "success":
        return None
    return result["text"]


CHECKERS = {
    "email_reply": check_email_reply,
    "web_page": check_web_page,
}

# No approved WhatsApp template exists yet for watch notifications, so these
# go out as plain text (see src.integrations.whatsapp.send_text_message) -
# same as the abandoned-package notice in scheduler.py, which is in the same
# position (a message type with no matching template). If this needs to
# reliably reach someone outside the 24h window, a template submission is
# the next step, not a code change here.
NOTIFY_MESSAGES = {
    "email_reply": lambda label: t("watch.notify.email_reply", label=label),
    "web_page": lambda label: t("watch.notify.web_page", label=label),
}


class _LocalizedLabels:
    """A read-only mapping whose values are looked up in the catalog on every access, so LOCALE is honoured per call."""

    def __init__(self, keys: dict[str, str]):
        self._keys = keys

    def get(self, name, default=None):
        return t(self._keys[name]) if name in self._keys else default

    def __getitem__(self, name):
        return t(self._keys[name])

    def keys(self):
        return self._keys.keys()


WATCH_TYPE_LABELS = _LocalizedLabels({
    "email_reply": "watch.type.email_reply",
    "web_page": "watch.type.web_page",
})
