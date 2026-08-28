from unittest.mock import MagicMock, patch

from argus.memory.consolidation_worker import ConsolidationWorker


def test_check_now_delegates_to_consolidate():
    router = MagicMock()
    memory_manager = MagicMock()
    worker = ConsolidationWorker(router, memory_manager)

    with patch("argus.memory.consolidation_worker.consolidate", return_value=["fact one"]) as mock_consolidate:
        result = worker.check_now()

    mock_consolidate.assert_called_once_with(router, memory_manager)
    assert result == ["fact one"]


def test_check_now_swallows_exceptions_so_the_poll_loop_keeps_going():
    worker = ConsolidationWorker(MagicMock(), MagicMock())

    with patch("argus.memory.consolidation_worker.consolidate", side_effect=RuntimeError("boom")):
        result = worker.check_now()

    assert result == []
