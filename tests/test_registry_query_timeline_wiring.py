from unittest.mock import MagicMock

from argus.tools import build_default_registry


def test_query_timeline_absent_without_a_spine():
    registry = build_default_registry(router=None, spine=None)
    assert "query_timeline" not in registry._tools


def test_query_timeline_present_with_a_spine():
    registry = build_default_registry(router=None, spine=MagicMock())
    assert "query_timeline" in registry._tools
