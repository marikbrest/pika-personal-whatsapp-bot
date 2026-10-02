"""
src.tools.shadow - Stage B of the function-calling migration.

_run is tested directly and synchronously (not through the real background
thread) for deterministic assertions on its logic. run_shadow_classification
itself is tested only for "returns fast" - not disabling threading.Thread
globally to force synchronous execution, since threading is a process-wide
singleton module: patching it would also make db.models'
_embed_message_in_background run synchronously for any test in the same
session that happens to save a long-enough message, which would fire a real
Gemini embedding call. Kept deliberately separate.
"""
import time
from unittest.mock import patch

from src.tools.shadow import _run, run_shadow_classification


def test_run_logs_agreement_and_no_error(db_path, make_user):
    user_id = make_user()
    with patch("src.tools.gemini_adapter.classify_with_tools", return_value=("get_weather", {"day_offset": 0})):
        _run({"id": user_id, "is_admin": False}, "מה מזג האוויר", "wamid.1", "weather")

    from src.db.models import get_connection
    conn = get_connection()
    row = conn.execute("SELECT * FROM intent_shadow_log WHERE whatsapp_message_id='wamid.1'").fetchone()
    conn.close()
    assert row["old_intent"] == "weather"
    assert row["new_tool"] == "get_weather"
    assert row["new_args"] == '{"day_offset": 0}'
    assert row["raw_content"] == "מה מזג האוויר"
    assert row["error"] is None


def test_run_logs_none_result_as_an_error_not_a_disagreement(db_path, make_user):
    """classify_with_tools returning None means the new pipeline itself
    failed (API error, no function call) - distinct from a clean
    disagreement, so it must land in the error column, not silently look
    like new_tool=None meant something classificatory."""
    user_id = make_user()
    with patch("src.tools.gemini_adapter.classify_with_tools", return_value=None):
        _run({"id": user_id, "is_admin": False}, "משהו", "wamid.2", "chat")

    from src.db.models import get_connection
    conn = get_connection()
    row = conn.execute("SELECT * FROM intent_shadow_log WHERE whatsapp_message_id='wamid.2'").fetchone()
    conn.close()
    assert row["new_tool"] is None
    assert "returned None" in row["error"]


def test_run_catches_a_raising_classifier_and_still_logs(db_path, make_user):
    user_id = make_user()
    with patch("src.tools.gemini_adapter.classify_with_tools", side_effect=RuntimeError("boom")):
        _run({"id": user_id, "is_admin": False}, "משהו", "wamid.3", "chat")  # must not raise

    from src.db.models import get_connection
    conn = get_connection()
    row = conn.execute("SELECT * FROM intent_shadow_log WHERE whatsapp_message_id='wamid.3'").fetchone()
    conn.close()
    assert row["new_tool"] is None
    assert "boom" in row["error"]


def test_run_never_performs_a_real_side_effect(db_path, make_user):
    """The entire safety property of shadow mode in one test: classifying a
    message as manage_tasks/add must NEVER actually add anything - shadow
    mode only compares verdicts, it never calls execute_tool."""
    user_id = make_user()
    with patch(
        "src.tools.gemini_adapter.classify_with_tools",
        return_value=("manage_tasks", {"action": "add", "content": "should not be added"}),
    ):
        _run({"id": user_id, "is_admin": False}, "תוסיף חלב", "wamid.4", "task_manage")

    from src.db.models import list_tasks
    assert list_tasks(user_id) == []


def test_run_when_logging_itself_fails_does_not_raise(db_path, make_user):
    user_id = make_user()
    with patch("src.tools.gemini_adapter.classify_with_tools", return_value=("get_weather", {})), \
         patch("src.db.models.log_shadow_classification", side_effect=RuntimeError("db is down")):
        _run({"id": user_id, "is_admin": False}, "x", "wamid.5", "weather")  # must not raise


def test_run_shadow_classification_returns_immediately_even_if_classification_is_slow():
    """Proves the fire-and-forget contract: the caller (webhook_handler,
    on the request-handling path) must never wait on the actual
    classification, however long it takes."""
    def slow_classify(*a, **kw):
        time.sleep(0.3)
        return ("chat", {"reply_text": "x"})

    with patch("src.tools.gemini_adapter.classify_with_tools", side_effect=slow_classify), \
         patch("src.db.models.log_shadow_classification"):
        start = time.perf_counter()
        run_shadow_classification({"id": 1, "is_admin": False}, "x", "wamid.6", "chat")
        elapsed = time.perf_counter() - start

    assert elapsed < 0.1  # returned long before the 0.3s classification would have finished
