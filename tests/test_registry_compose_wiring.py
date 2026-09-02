from unittest.mock import MagicMock

from argus.tools import build_default_registry


def test_compose_document_absent_without_a_spine():
    registry = build_default_registry(router=None, spine=None)
    assert "compose_document" not in registry._tools


def test_compose_document_present_with_a_spine():
    registry = build_default_registry(router=None, spine=MagicMock())
    assert "compose_document" in registry._tools
