from unittest.mock import patch

from argus.spine.sensors.window_focus import WindowFocusSensor


def test_no_active_window_emits_idle_started_once():
    sensor = WindowFocusSensor()
    with patch("argus.context_awareness._active_window_title", return_value=None):
        first = sensor.poll()
        second = sensor.poll()

    assert [o.kind for o in first] == ["focus.idle_started"]
    assert second == []  # idle already reported; no repeat every poll


def test_returning_from_idle_emits_idle_ended_then_focus_changed():
    sensor = WindowFocusSensor()
    with patch("argus.context_awareness._active_window_title", return_value=None):
        sensor.poll()
    with patch("argus.context_awareness._active_window_title", return_value="Notepad"):
        observations = sensor.poll()

    kinds = [o.kind for o in observations]
    assert kinds == ["focus.idle_ended", "focus.changed"]
    assert observations[1].subject == "Notepad"


def test_same_title_does_not_re_emit_focus_changed():
    sensor = WindowFocusSensor()
    with patch("argus.context_awareness._active_window_title", return_value="VS Code"):
        first = sensor.poll()
        second = sensor.poll()

    assert len(first) == 1
    assert second == []


def test_title_change_emits_focus_changed_with_new_subject():
    sensor = WindowFocusSensor()
    with patch("argus.context_awareness._active_window_title", return_value="VS Code"):
        sensor.poll()
    with patch("argus.context_awareness._active_window_title", return_value="Chrome"):
        observations = sensor.poll()

    assert len(observations) == 1
    assert observations[0].kind == "focus.changed"
    assert observations[0].subject == "Chrome"
