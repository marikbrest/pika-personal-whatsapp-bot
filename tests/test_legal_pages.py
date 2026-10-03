"""The public privacy/terms pages must stay honest and bilingual (Google's consent screen needs them)."""
from unittest.mock import patch

from src import legal_pages
from src.legal_pages import privacy_html, terms_html


def test_privacy_page_is_bilingual_and_names_the_recipients():
    body = privacy_html()
    assert "Privacy Policy" in body and "מדיניות פרטיות" in body
    for recipient in ("Gemini", "Meta", "Ship24", "Open-Meteo", "Yahoo Finance"):
        assert recipient in body
    # the operator can read the DB - the page must not claim otherwise
    assert "technically read the stored data" in body
    assert "never shared with a third party" not in body


def test_privacy_page_has_the_google_limited_use_disclosure():
    body = privacy_html()
    assert "api-services-user-data-policy" in body
    assert "Limited Use" in body


def test_terms_page_is_bilingual_and_links_to_privacy():
    body = terms_html()
    assert "Terms of Use" in body and "תנאי שימוש" in body
    assert 'href="/privacy"' in body
    assert "Not for emergencies" in body


def test_operator_and_contact_show_a_placeholder_until_configured():
    with patch.object(legal_pages, "OPERATOR_NAME", ""), patch.object(legal_pages, "ADMIN_CONTACT_EMAIL", ""):
        body = privacy_html()
    assert "set OPERATOR_NAME" in body and "set ADMIN_CONTACT_EMAIL" in body


def test_operator_and_contact_are_html_escaped():
    with patch.object(legal_pages, "OPERATOR_NAME", "<b>x</b>"), patch.object(legal_pages, "ADMIN_CONTACT_EMAIL", "a@b.c"):
        body = terms_html()
    assert "&lt;b&gt;x&lt;/b&gt;" in body and 'mailto:a@b.c' in body


def test_main_serves_both_pages_and_about_links_to_them():
    src = open("src/main.py", encoding="utf-8").read()
    assert '@app.get("/privacy"' in src and '@app.get("/terms"' in src
    assert 'href="/privacy">Privacy policy</a> · <a href="/terms">' in src
