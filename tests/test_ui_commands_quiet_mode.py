from argus.ui import commands as ui_commands


def test_quiet_mode_defaults_off():
    ui_commands.set_quiet_mode(False)
    assert ui_commands.is_quiet_mode() is False


def test_set_quiet_mode_on_and_off():
    ui_commands.set_quiet_mode(True)
    assert ui_commands.is_quiet_mode() is True
    ui_commands.set_quiet_mode(False)
    assert ui_commands.is_quiet_mode() is False


def test_toggle_quiet_mode_flips_and_returns_new_state():
    ui_commands.set_quiet_mode(False)
    assert ui_commands.toggle_quiet_mode() is True
    assert ui_commands.is_quiet_mode() is True
    assert ui_commands.toggle_quiet_mode() is False
    assert ui_commands.is_quiet_mode() is False
