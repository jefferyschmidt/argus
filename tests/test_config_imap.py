"""PRD.md §19/§20 unit 44a: the 2026-08-31 incident's root cause -- a
Yahoo app password stored with the spaces Yahoo displays it with (19
chars incl. 3 spaces; the real password is 16, no spaces), so every
login was AUTHENTICATIONFAILED. Whitespace stripping must apply to
exactly gmail_imap_app_password and yahoo_imap_app_password -- nothing
else, since a general password can legitimately contain spaces."""

from argus.config import Settings


def test_gmail_app_password_whitespace_is_stripped():
    settings = Settings(gmail_imap_app_password="abcd efgh ijkl mnop")
    assert settings.gmail_imap_app_password == "abcdefghijklmnop"


def test_yahoo_app_password_whitespace_is_stripped():
    settings = Settings(yahoo_imap_app_password="wxyz wxyz wxyz wxyz")
    assert settings.yahoo_imap_app_password == "wxyzwxyzwxyzwxyz"


def test_app_password_without_whitespace_is_unchanged():
    settings = Settings(gmail_imap_app_password="nospaceshere1234")
    assert settings.gmail_imap_app_password == "nospaceshere1234"


def test_unrelated_password_field_keeps_its_spaces():
    """A general password can legitimately contain spaces -- only the two
    IMAP app-password fields get stripped."""
    settings = Settings(anthropic_api_key="has a space")
    assert settings.anthropic_api_key == "has a space"
