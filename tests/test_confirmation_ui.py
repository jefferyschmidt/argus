import threading
import time

from argus.ui import commands as ui_commands


def test_request_confirmation_returns_pending_state():
    request_id = ui_commands.request_confirmation("open_app", {"app": "https://mail.yahoo.com"})
    try:
        pending = ui_commands.get_pending_confirmation()
        assert pending["id"] == request_id
        assert pending["tool_name"] == "open_app"
    finally:
        ui_commands.resolve_confirmation(request_id, True)


def test_resolve_confirmation_clears_pending_state():
    request_id = ui_commands.request_confirmation("open_app", {})
    ui_commands.resolve_confirmation(request_id, True)
    assert ui_commands.get_pending_confirmation() is None


def test_wait_for_confirmation_returns_the_resolved_value():
    request_id = ui_commands.request_confirmation("run_shell", {"command": "dir"})

    def resolve_soon():
        time.sleep(0.05)
        ui_commands.resolve_confirmation(request_id, True)

    threading.Thread(target=resolve_soon).start()
    assert ui_commands.wait_for_confirmation(request_id, timeout=2.0) is True


def test_wait_for_confirmation_times_out_and_clears_pending():
    request_id = ui_commands.request_confirmation("run_shell", {"command": "dir"})
    result = ui_commands.wait_for_confirmation(request_id, timeout=0.1)
    assert result is None
    assert ui_commands.get_pending_confirmation() is None


def test_stale_response_for_a_different_request_is_ignored():
    old_id = ui_commands.request_confirmation("tool_a", {})
    ui_commands.resolve_confirmation(old_id, True)  # resolved before the new request even starts

    new_id = ui_commands.request_confirmation("tool_b", {})

    def resolve_new_soon():
        time.sleep(0.05)
        ui_commands.resolve_confirmation(new_id, False)

    threading.Thread(target=resolve_new_soon).start()
    # Must not pick up the old resolution meant for a different request.
    assert ui_commands.wait_for_confirmation(new_id, timeout=2.0) is False
