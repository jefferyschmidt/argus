from unittest.mock import MagicMock

from argus.tools import build_default_registry


def test_second_opinion_absent_without_router():
    registry = build_default_registry(router=None)
    assert "second_opinion" not in registry._tools


def test_second_opinion_present_with_router():
    registry = build_default_registry(router=MagicMock())
    assert "second_opinion" in registry._tools
