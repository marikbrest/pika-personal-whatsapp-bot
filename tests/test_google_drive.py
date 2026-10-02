"""
Google Drive integration (2026-09-14) - search_files, create_text_file,
format_files_for_reply. Same mocking pattern as
test_gmail_header_injection.py: patch _get_drive_service directly so these
stay fast unit tests against query-building/formatting logic, never a real
network call to Google.
"""
from unittest.mock import MagicMock, patch

from src.integrations.google_drive import create_text_file, format_files_for_reply, search_files


def _service_returning(files: list[dict]) -> MagicMock:
    service = MagicMock()
    service.files.return_value.list.return_value.execute.return_value = {"files": files}
    return service


def test_search_files_builds_query_matching_name_or_content():
    service = _service_returning([])
    with patch("src.integrations.google_drive._get_drive_service", return_value=service):
        search_files(user_id=1, query="tax invoice")

    kwargs = service.files.return_value.list.call_args.kwargs
    assert "name contains 'tax invoice'" in kwargs["q"]
    assert "fullText contains 'tax invoice'" in kwargs["q"]
    assert "trashed = false" in kwargs["q"]


def test_search_files_escapes_single_quotes_in_the_query():
    """A literal ' in the query must not break out of Drive's query string
    syntax (the Drive equivalent of a SQL-injection-shaped bug)."""
    service = _service_returning([])
    with patch("src.integrations.google_drive._get_drive_service", return_value=service):
        search_files(user_id=1, query="O'Brien's notes")

    kwargs = service.files.return_value.list.call_args.kwargs
    assert "O\\'Brien\\'s notes" in kwargs["q"]
    assert "O'Brien's notes" not in kwargs["q"]


def test_search_files_returns_the_files_list():
    files = [{"id": "1", "name": "Invoice.pdf", "webViewLink": "https://drive.google.com/x"}]
    service = _service_returning(files)
    with patch("src.integrations.google_drive._get_drive_service", return_value=service):
        result = search_files(user_id=1, query="invoice")
    assert result == files


def test_create_text_file_sends_the_given_name_and_content():
    service = MagicMock()
    service.files.return_value.create.return_value.execute.return_value = {
        "id": "file1", "webViewLink": "https://drive.google.com/file1",
    }
    with patch("src.integrations.google_drive._get_drive_service", return_value=service):
        result = create_text_file(user_id=1, filename="note.txt", content="קניתי מתנה לדני")

    kwargs = service.files.return_value.create.call_args.kwargs
    assert kwargs["body"] == {"name": "note.txt", "mimeType": "text/plain"}
    assert result == {"id": "file1", "webViewLink": "https://drive.google.com/file1"}


def test_format_files_for_reply_lists_name_and_link():
    files = [
        {"name": "Invoice.pdf", "webViewLink": "https://drive.google.com/1"},
        {"name": "Notes.txt", "webViewLink": "https://drive.google.com/2"},
    ]
    formatted = format_files_for_reply(files)
    assert "Invoice.pdf" in formatted
    assert "https://drive.google.com/1" in formatted
    assert "Notes.txt" in formatted
    assert "https://drive.google.com/2" in formatted


def test_format_files_for_reply_with_no_matches():
    assert "לא מצאתי" in format_files_for_reply([])
