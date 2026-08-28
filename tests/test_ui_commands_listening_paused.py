from argus.ui import commands as ui_commands


def test_listening_paused_defaults_off():
    ui_commands.set_listening_paused(False)
    assert ui_commands.is_listening_paused() is False


def test_set_listening_paused_on_and_off():
    ui_commands.set_listening_paused(True)
    assert ui_commands.is_listening_paused() is True
    ui_commands.set_listening_paused(False)
    assert ui_commands.is_listening_paused() is False


def test_toggle_listening_paused_flips_and_returns_new_state():
    ui_commands.set_listening_paused(False)
    assert ui_commands.toggle_listening_paused() is True
    assert ui_commands.is_listening_paused() is True
    assert ui_commands.toggle_listening_paused() is False
    assert ui_commands.is_listening_paused() is False
