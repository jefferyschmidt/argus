import threading
from unittest.mock import MagicMock, patch

from argus.routine_worker import RoutineWorker


def _worker():
    orchestrator = MagicMock()
    speak_fn = MagicMock()
    lock = threading.Lock()
    return RoutineWorker(orchestrator, speak_fn, lock), orchestrator, speak_fn


def test_run_routine_marks_run_before_executing_and_streams_through_orchestrator():
    worker, orchestrator, speak_fn = _worker()

    def fake_handle_streaming(goal, on_sentence):
        on_sentence("It's sunny today.")
        on_sentence("You have one reminder due this afternoon.")
        return "full reply"

    orchestrator.handle_streaming.side_effect = fake_handle_streaming

    with patch("argus.memory.routines.RoutineStore") as MockStore, \
         patch("argus.memory.store.get_connection"):
        mock_store = MockStore.return_value
        worker._run_routine({"id": 1, "goal": "give me a morning briefing"})

    mock_store.mark_run.assert_called_once()
    orchestrator.handle_streaming.assert_called_once()
    assert speak_fn.call_count == 2
    assert speak_fn.call_args_list[0].args[0] == "It's sunny today."


def test_run_routine_failure_speaks_a_failure_note_instead_of_silently_dropping():
    worker, orchestrator, speak_fn = _worker()
    orchestrator.handle_streaming.side_effect = RuntimeError("web search failed")

    with patch("argus.memory.routines.RoutineStore"), \
         patch("argus.memory.store.get_connection"):
        worker._run_routine({"id": 2, "goal": "give me a morning briefing"})

    speak_fn.assert_called_once()
    assert "failed" in speak_fn.call_args[0][0].lower()


def test_check_now_skips_routines_when_lock_is_held():
    worker, orchestrator, speak_fn = _worker()
    worker._interaction_lock.acquire()

    with patch("argus.memory.routines.RoutineStore") as MockStore, \
         patch("argus.memory.store.get_connection"):
        MockStore.return_value.list_due.return_value = [{"id": 1, "goal": "briefing"}]
        worker.check_now()

    orchestrator.handle_streaming.assert_not_called()
