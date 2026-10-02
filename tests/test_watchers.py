"""
Checkers for the generic watch engine (2026-09-14). Each checker's contract:
return a comparable state string, or None for "could not determine right
now" (a transient failure) - never raise for an ordinary "nothing found"
case, since src.scheduler.check_watches treats None as "try again next
cycle", not as a crash.
"""
from unittest.mock import patch

from src.integrations.watchers import CHECKERS, NOTIFY_MESSAGES, WATCH_TYPE_LABELS, check_email_reply, check_web_page


def test_check_email_reply_returns_message_count_as_state():
    messages = [{"from": "me", "date": "Mon", "subject": "x", "body": "hi"},
                {"from": "dani", "date": "Tue", "subject": "x", "body": "reply"}]
    with patch("src.integrations.gmail.get_thread_messages", return_value=messages):
        assert check_email_reply(user_id=1, target="t1") == "2"


def test_check_email_reply_returns_none_for_empty_thread():
    with patch("src.integrations.gmail.get_thread_messages", return_value=[]):
        assert check_email_reply(user_id=1, target="t1") is None


def test_check_web_page_returns_extracted_text_on_success():
    with patch(
        "src.integrations.content_extractor.fetch_and_extract",
        return_value={"title": "t", "text": "the page content", "content_type": "article", "fetch_status": "success"},
    ):
        assert check_web_page(user_id=1, target="https://x.com") == "the page content"


def test_check_web_page_returns_none_when_fetch_failed():
    with patch(
        "src.integrations.content_extractor.fetch_and_extract",
        return_value={"title": None, "text": None, "content_type": None, "fetch_status": "failed"},
    ):
        assert check_web_page(user_id=1, target="https://x.com") is None


def test_check_web_page_returns_none_when_paywalled():
    """A paywalled page's tiny extracted snippet is not a reliable baseline
    to diff against later - safer to skip than to "watch" noise."""
    with patch(
        "src.integrations.content_extractor.fetch_and_extract",
        return_value={"title": "t", "text": "a few words", "content_type": "article", "fetch_status": "paywalled"},
    ):
        assert check_web_page(user_id=1, target="https://x.com") is None


def test_checkers_and_notify_messages_and_labels_cover_the_same_types():
    assert set(CHECKERS.keys()) == set(NOTIFY_MESSAGES.keys()) == set(WATCH_TYPE_LABELS.keys())


def test_notify_messages_include_the_label():
    assert "my thread" in NOTIFY_MESSAGES["email_reply"]("my thread")
    assert "https://x.com" in NOTIFY_MESSAGES["web_page"]("https://x.com")
