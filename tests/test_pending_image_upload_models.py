"""
pending_image_uploads (2026-09-26) - the DB layer behind edit_image: only the
WhatsApp media_id is ever stored (see save_pending_image_upload's own
docstring for why), one row per user, with a TTL. Same testing pattern as
test_forwarded_suggestions.py's pending_suggestions coverage.
"""
from src.db.models import (
    PENDING_IMAGE_UPLOAD_TTL_MINUTES,
    clear_pending_image_upload,
    get_connection,
    get_pending_image_upload,
    save_pending_image_upload,
)


def test_save_and_get_pending_image_upload(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    save_pending_image_upload(1, "media-id-1", "image/jpeg")

    row = get_pending_image_upload(1)
    assert row["media_id"] == "media-id-1"
    assert row["mime_type"] == "image/jpeg"


def test_get_pending_image_upload_returns_none_when_none_saved(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    assert get_pending_image_upload(1) is None


def test_saving_a_new_upload_replaces_the_previous_one(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    save_pending_image_upload(1, "media-id-old", "image/jpeg")
    save_pending_image_upload(1, "media-id-new", "image/png")

    row = get_pending_image_upload(1)
    assert row["media_id"] == "media-id-new"
    assert row["mime_type"] == "image/png"


def test_clear_pending_image_upload_removes_the_row(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    save_pending_image_upload(1, "media-id-1", "image/jpeg")
    clear_pending_image_upload(1)

    assert get_pending_image_upload(1) is None


def test_an_expired_pending_image_upload_is_deleted_and_returns_none(db_path, make_user):
    """Same TTL-expiry pattern as get_pending_suggestion: a row older than
    PENDING_IMAGE_UPLOAD_TTL_MINUTES must not still be offered for editing -
    otherwise an unrelated later message could end up "editing" a
    long-forgotten photo."""
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    save_pending_image_upload(1, "media-id-1", "image/jpeg")

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE pending_image_uploads SET created_at = datetime('now', ?) WHERE user_id = ?",
            (f"-{PENDING_IMAGE_UPLOAD_TTL_MINUTES + 1} minutes", 1),
        )
        conn.commit()
    finally:
        conn.close()

    assert get_pending_image_upload(1) is None

    conn = get_connection()
    try:
        cur = conn.execute("SELECT * FROM pending_image_uploads WHERE user_id = ?", (1,))
        assert cur.fetchone() is None
    finally:
        conn.close()


def test_pending_image_uploads_are_isolated_per_user(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    make_user(whatsapp_number="972500000002", display_name="רונית")
    save_pending_image_upload(1, "media-id-yossi", "image/jpeg")

    assert get_pending_image_upload(1) is not None
    assert get_pending_image_upload(2) is None
