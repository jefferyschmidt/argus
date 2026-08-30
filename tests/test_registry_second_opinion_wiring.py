from unittest.mock import MagicMock

from argus.tools import build_default_registry


def test_second_opinion_absent_without_router():
    registry = build_default_registry(router=None)
    assert "second_opinion" not in registry._tools


def test_second_opinion_present_with_router():
    registry = build_default_registry(router=MagicMock())
    assert "second_opinion" in registry._tools


def test_confirmed_memory_review_tools_are_registered():
    registry = build_default_registry(router=None)

    assert "list_core_memories" in registry._tools
    assert "delete_core_memory" in registry._tools
