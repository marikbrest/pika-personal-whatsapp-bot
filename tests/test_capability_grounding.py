"""
Structural fix (2026-09-27) for the image-generation false-negative found
live the same day: intent_parser.py's old classifier used to have zero
awareness of any tool added through the newer function-calling pipeline,
so its capability-gap-offer instruction (2026-09-26) could confidently and
wrongly claim a real capability doesn't exist. Now every fallback call
threads in a live, registry-grounded capability list (see
webhook_handler._build_capability_list_for_old_classifier) instead of
relying on a hand-maintained static note that has to be remembered on
every new tool.
"""
from unittest.mock import MagicMock, patch

from src.intent_parser import _build_header, _build_text_prompt
from src.webhook_handler import _build_capability_list_for_old_classifier


def _user(is_admin=False):
    return {"id": 1, "is_admin": is_admin}


def test_build_capability_list_includes_a_known_non_admin_tool():
    text = _build_capability_list_for_old_classifier(_user())
    assert "generate_image" in text
    assert "edit_image" in text


def test_build_capability_list_excludes_admin_only_tools_for_a_regular_user():
    text = _build_capability_list_for_old_classifier(_user(is_admin=False))
    assert "get_infra_status" not in text


def test_build_capability_list_includes_admin_only_tools_for_an_admin():
    text = _build_capability_list_for_old_classifier(_user(is_admin=True))
    assert "get_infra_status" in text


def test_header_includes_the_capability_list_when_given():
    header = _build_header("Asia/Jerusalem", available_capabilities="- generate_image: draws pictures")
    assert "generate_image" in header
    assert "אל תטען שאין לך" in header


def test_header_omits_the_capability_block_when_not_given():
    header = _build_header("Asia/Jerusalem")
    assert "אל תטען שאין לך" not in header


def test_text_prompt_threads_the_capability_list_through():
    prompt = _build_text_prompt("שלום", "Asia/Jerusalem", available_capabilities="- manage_my_data: shows your data")
    assert "manage_my_data" in prompt
