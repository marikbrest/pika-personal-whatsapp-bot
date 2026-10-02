"""
An install without GOOGLE_CLIENT_ID/SECRET used to answer "connect my Google"
(and calendar/Drive/Gmail requests from a not-yet-connected user) with the
generic "something went wrong" reply. It now says what is actually missing.
"""
from unittest.mock import patch

import pytest

from src.integrations import google_oauth
from src.integrations.google_oauth import GoogleNotConfiguredError
from src.tools.dispatch import GOOGLE_NOT_CONFIGURED_REPLY, execute_tool
from src.tools.registry import get_tool
import src.tools  # noqa: F401 - populates the registry


def test_build_auth_url_raises_a_dedicated_error_when_unconfigured():
    with patch.object(google_oauth, "GOOGLE_CLIENT_ID", ""), pytest.raises(GoogleNotConfiguredError):
        google_oauth.build_auth_url(1)


def test_connect_google_without_oauth_client_explains_the_missing_setup():
    tool = get_tool("connect_google")
    user = {"id": 1, "timezone": "Asia/Jerusalem"}
    with patch.object(google_oauth, "GOOGLE_CLIENT_ID", ""):
        reply = execute_tool(tool, user, {"reply_text": "Here you go"})
    assert reply == GOOGLE_NOT_CONFIGURED_REPLY
    assert "GOOGLE_CLIENT_ID" in reply
