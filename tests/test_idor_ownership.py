"""
PRD 'every table is user_id-scoped from day one; mutations verify ownership
(IDOR defence), not just correctly-scoped fetches'. This file exists because
that is a claim in a docstring/PRD, not something enforced by the type
system - if a future edit drops the ' AND user_id = ?' clause from any of
these queries, this suite is what would actually notice.

Pattern for every case: user A creates something, user B (a real, distinct,
otherwise-legitimate account - not an attacker guessing IDs from the
outside) tries to touch it by its real, correctly-guessed id, and the
mutation must refuse. This is the more important half of the property:
"scoped to the wrong owner" is what an IDOR bug looks like, not "id doesn't
exist" (which is a different, boring case every one of these functions
already gets right too).
"""
import pytest

from src.db.models import (
    delete_saved_link,
    delete_user_fact,
    deactivate_reminder,
    get_pending_draft,
    list_active_reminders,
    list_saved_links,
    list_user_facts,
    save_email_draft,
    save_link,
    save_reminder,
    save_user_fact,
    update_draft_body,
    update_draft_status,
)


@pytest.fixture()
def owner(make_user):
    return make_user(whatsapp_number="972500000001")


@pytest.fixture()
def attacker(make_user):
    """Not a hostile outsider - a second legitimate, allowlisted family
    member (Ronit/Gil/Or), which is the realistic threat model here: no
    unauthenticated user can reach these functions at all (14.2), so the
    question is whether one real user can touch another's data."""
    return make_user(whatsapp_number="972500000002", display_name="Attacker")


def test_reminder_cannot_be_cancelled_by_another_user(owner, attacker):
    from datetime import datetime, timezone

    reminder_id = save_reminder(
        user_id=owner, content="קח תרופה", schedule_type="once",
        schedule_time="09:00", schedule_days=None,
        next_trigger_at=datetime.now(timezone.utc),
    )

    assert deactivate_reminder(reminder_id, user_id=attacker) is False
    assert any(r["id"] == reminder_id for r in list_active_reminders(owner))


def test_saved_link_cannot_be_deleted_by_another_user(owner, attacker):
    link_id = save_link(
        user_id=owner, original_url="https://example.com/article",
        title="t", content_snapshot="c", content_type="article",
        fetch_status="success", embedding=None,
    )

    assert delete_saved_link(attacker, link_id) is False
    assert any(l["id"] == link_id for l in list_saved_links(owner))


def test_email_draft_cannot_be_sent_or_cancelled_by_another_user(owner, attacker):
    """The email draft is the most sensitive object here - approving 'send'
    on someone else's draft would exfiltrate an email under the wrong
    identity."""
    draft_id = save_email_draft(owner, "someone@example.com", "subj", "body")

    assert update_draft_status(draft_id, "sent", user_id=attacker) is False
    assert update_draft_body(draft_id, "new subj", "new body", user_id=attacker) is False

    still_pending = get_pending_draft(owner)
    assert still_pending is not None
    assert still_pending["subject"] == "subj"  # untouched by the attacker's edit attempt


def test_memory_fact_cannot_be_deleted_by_another_user(owner, attacker):
    save_user_fact(owner, "diet", "צמחוני")

    assert delete_user_fact(attacker, "diet") is False
    assert any(f["fact_key"] == "diet" for f in list_user_facts(owner))


def test_reminder_scoped_lookup_returns_empty_not_someone_elses_data(owner, attacker):
    """The read side of the same property: list_active_reminders(attacker)
    must not include owner's reminders just because they exist in the same
    table."""
    from datetime import datetime, timezone

    save_reminder(
        user_id=owner, content="secret", schedule_type="once",
        schedule_time="09:00", schedule_days=None,
        next_trigger_at=datetime.now(timezone.utc),
    )
    assert list_active_reminders(attacker) == []
