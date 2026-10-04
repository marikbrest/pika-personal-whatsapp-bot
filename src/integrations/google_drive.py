"""
Google Drive integration - searching existing files and creating new ones.

Two scopes, deliberately not the broad "drive" scope (see google_oauth.SCOPES):
drive.readonly for search_files (needs to see files the bot didn't create),
drive.file for create_text_file (limited to files the bot itself creates - it
can never read, modify, or delete anything else in the user's Drive, by
Google's own enforcement of that scope, not just by this code's discipline).

All functions require that the user has already connected Google
(src.integrations.google_oauth).
"""
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

from src.integrations.google_oauth import GoogleAuthExpiredError, NotConnectedError, get_credentials, is_genuine_auth_rejection

# Per-process cache of the service object - same pattern as gmail.py/google_calendar.py
_service_cache: dict[int, tuple[str, object]] = {}


def _get_drive_service(user_id: int):
    credentials = get_credentials(user_id)
    if credentials is None:
        raise NotConnectedError(f"user_id={user_id} has not connected Google yet")

    cached = _service_cache.get(user_id)
    if cached is not None and cached[0] == credentials.token:
        return cached[1]

    service = build("drive", "v3", credentials=credentials)
    _service_cache[user_id] = (credentials.token, service)
    return service


def search_files(user_id: int, query: str, max_results: int = 10) -> list[dict]:
    """
    Searches Drive for files whose name or content matches `query`, most
    recently modified first. Trashed files are excluded. Read-only - never
    modifies anything, matching the drive.readonly scope this runs under.

    Matches by both name and content (fullText) rather than name alone: a
    user asking to find "the tax document" is thinking of what's inside a
    file at least as often as what it's named.
    """
    escaped = query.replace("\\", "\\\\").replace("'", "\\'")
    drive_query = f"(name contains '{escaped}' or fullText contains '{escaped}') and trashed = false"
    try:
        service = _get_drive_service(user_id)
        result = (
            service.files()
            .list(
                q=drive_query,
                pageSize=max_results,
                fields="files(id, name, mimeType, webViewLink, modifiedTime)",
                orderBy="modifiedTime desc",
            )
            .execute()
        )
    except HttpError as e:
        if is_genuine_auth_rejection(e.resp.status, str(e)):
            raise GoogleAuthExpiredError(str(e)) from e
        raise
    return result.get("files", [])


def create_text_file(user_id: int, filename: str, content: str) -> dict:
    """
    Creates a new plain-text file in the user's My Drive root containing
    `content`. Runs under drive.file, not the broader drive scope - by
    Google's own enforcement, this call (and every call this integration
    makes) can only ever create/touch files it itself created, never read or
    modify anything already in the user's Drive.

    Returns {"id": ..., "webViewLink": ...} on success.
    """
    try:
        service = _get_drive_service(user_id)
        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/plain")
        result = (
            service.files()
            .create(body={"name": filename, "mimeType": "text/plain"}, media_body=media, fields="id, webViewLink")
            .execute()
        )
    except HttpError as e:
        if is_genuine_auth_rejection(e.resp.status, str(e)):
            raise GoogleAuthExpiredError(str(e)) from e
        raise
    return result


from src.i18n import t


def format_files_for_reply(files: list[dict]) -> str:
    """Formats a Drive search result as readable text (in the current locale). Never goes
    through Gemini - these are facts, not guesses."""
    if not files:
        return t("drive.no_files")

    lines = []
    for i, f in enumerate(files, start=1):
        name = f.get("name") or t("drive.unnamed")
        link = f.get("webViewLink") or ""
        lines.append(f"{i}. 📄 {name}\n   {link}")
    return "\n\n".join(lines)
