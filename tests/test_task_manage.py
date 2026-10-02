"""
Formalizes the manual verification run against the live DB on 2026-09-12
when task_manage shipped (commit 3b415c7) into a real, repeatable suite -
the live-DB run is gone the moment the process restarts; this stays.
"""
import pytest

from src.db.models import delete_task, list_tasks, set_task_done
from src.webhook_handler import _handle_task_manage


@pytest.fixture()
def user(make_user):
    return {"id": make_user()}


@pytest.fixture()
def other_user(make_user):
    return {"id": make_user(whatsapp_number="972500000002", display_name="Other")}


def test_add_creates_item_and_confirms(user):
    reply = _handle_task_manage(user, {"action": "add", "content": "חלב", "list_name": "קניות"})
    assert "נוסף" in reply
    assert "חלב" in reply
    assert [t["content"] for t in list_tasks(user["id"], "קניות")] == ["חלב"]


def test_add_defaults_to_default_list_when_none_named(user):
    _handle_task_manage(user, {"action": "add", "content": "להתקשר למוסך", "list_name": None})
    tasks = list_tasks(user["id"], "default")
    assert [t["content"] for t in tasks] == ["להתקשר למוסך"]


def test_list_groups_by_list_name(user):
    _handle_task_manage(user, {"action": "add", "content": "חלב", "list_name": "קניות"})
    _handle_task_manage(user, {"action": "add", "content": "לחם", "list_name": "קניות"})
    _handle_task_manage(user, {"action": "add", "content": "מוסך", "list_name": None})

    reply = _handle_task_manage(user, {"action": "list", "list_name": None})
    assert "חלב" in reply and "לחם" in reply and "מוסך" in reply

    reply_scoped = _handle_task_manage(user, {"action": "list", "list_name": "קניות"})
    assert "חלב" in reply_scoped and "מוסך" not in reply_scoped


def test_list_on_empty_list_says_so_not_an_error(user):
    reply = _handle_task_manage(user, {"action": "list", "list_name": None})
    assert "ריק" in reply


def test_done_by_exact_content_match(user):
    _handle_task_manage(user, {"action": "add", "content": "חלב", "list_name": "קניות"})
    reply = _handle_task_manage(user, {"action": "done", "match": "חלב", "list_name": "קניות"})
    assert "בוצע" in reply
    assert list_tasks(user["id"], "קניות") == []


def test_done_by_partial_content_match(user):
    _handle_task_manage(user, {"action": "add", "content": "סוללות AA", "list_name": "קניות"})
    reply = _handle_task_manage(user, {"action": "delete", "match": "סוללות", "list_name": "קניות"})
    assert "נמחק" in reply


def test_no_matching_item_asks_instead_of_silently_doing_nothing(user):
    _handle_task_manage(user, {"action": "add", "content": "חלב", "list_name": "קניות"})
    reply = _handle_task_manage(user, {"action": "delete", "match": "אבטיח", "list_name": "קניות"})
    assert "לא מצאתי" in reply
    # nothing was touched
    assert [t["content"] for t in list_tasks(user["id"], "קניות")] == ["חלב"]


def test_ambiguous_match_asks_which_one_rather_than_guessing(user):
    """The failure mode being guarded against: silently completing the wrong
    item is worse than asking, because the user only discovers it later when
    the thing turns out not to have been done."""
    _handle_task_manage(user, {"action": "add", "content": "מיץ תפוזים", "list_name": "קניות"})
    _handle_task_manage(user, {"action": "add", "content": "מיץ ענבים", "list_name": "קניות"})
    reply = _handle_task_manage(user, {"action": "done", "match": "מיץ", "list_name": "קניות"})
    assert "לאיזה מהם" in reply
    # both items are still open - nothing was guessed
    assert len(list_tasks(user["id"], "קניות")) == 2


def test_clear_empties_named_list_only(user):
    _handle_task_manage(user, {"action": "add", "content": "חלב", "list_name": "קניות"})
    _handle_task_manage(user, {"action": "add", "content": "מוסך", "list_name": None})

    reply = _handle_task_manage(user, {"action": "clear", "list_name": "קניות"})
    assert "רוקנתי" in reply
    assert list_tasks(user["id"], "קניות") == []
    assert [t["content"] for t in list_tasks(user["id"], "default")] == ["מוסך"]


def test_clear_on_already_empty_list_says_so(user):
    reply = _handle_task_manage(user, {"action": "clear", "list_name": "קניות"})
    assert "כבר ריקה" in reply


def test_open_task_cap_refuses_new_additions_past_the_limit(user, monkeypatch):
    import src.db.models as models

    monkeypatch.setattr(models, "MAX_OPEN_TASKS_PER_USER", 2)
    _handle_task_manage(user, {"action": "add", "content": "a", "list_name": None})
    _handle_task_manage(user, {"action": "add", "content": "b", "list_name": None})
    reply = _handle_task_manage(user, {"action": "add", "content": "c", "list_name": None})
    assert "2" in reply  # tells the user the limit
    assert len(list_tasks(user["id"], "default")) == 2


def test_cross_user_cannot_complete_or_delete_someone_elses_task(user, other_user):
    """Same IDOR defence pattern as deactivate_reminder: ownership is
    enforced in the SQL WHERE clause, not just checked in Python
    beforehand."""
    _handle_task_manage(user, {"action": "add", "content": "חלב", "list_name": None})
    my_task_id = list_tasks(user["id"], "default")[0]["id"]

    assert set_task_done(other_user["id"], my_task_id) is False
    assert delete_task(other_user["id"], my_task_id) is False
    # still there, still open, for the real owner
    assert [t["content"] for t in list_tasks(user["id"], "default")] == ["חלב"]
