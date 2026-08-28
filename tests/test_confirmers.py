from unittest.mock import MagicMock, patch

from argus.tools.registry import console_confirmer


def test_console_confirmer_accepts_full_word_yes():
    with patch("builtins.input", return_value="yes"):
        assert console_confirmer("some_tool", {}) is True


def test_console_confirmer_accepts_bare_y():
    with patch("builtins.input", return_value="y"):
        assert console_confirmer("some_tool", {}) is True


def test_console_confirmer_rejects_no():
    with patch("builtins.input", return_value="no"):
        assert console_confirmer("some_tool", {}) is False


def test_console_confirmer_rejects_empty():
    with patch("builtins.input", return_value=""):
        assert console_confirmer("some_tool", {}) is False


def test_voice_confirmer_falls_back_to_ui_and_returns_its_answer():
    """When voice is unclear, the fallback is the console UI (a request/
    response round trip through ui_commands), NOT a blocking terminal
    input() -- confirmed live that dropping to the command line there
    broke "everything through the chat/voice interface." """
    from argus.voice.confirm import make_voice_confirmer

    speaker = MagicMock()
    transcriber = MagicMock()
    confirmer = make_voice_confirmer(speaker, transcriber)

    with patch("argus.voice.audio_io.record_followup", return_value=None), \
         patch("argus.ui.commands.request_confirmation", return_value=42) as mock_request, \
         patch("argus.ui.commands.wait_for_confirmation", return_value=True) as mock_wait:
        result = confirmer("open_app", {"app": "https://mail.yahoo.com"})

    assert result is True
    mock_request.assert_called_once_with("open_app", {"app": "https://mail.yahoo.com"})
    mock_wait.assert_called_once()
    assert mock_wait.call_args[0][0] == 42


def test_voice_confirmer_ui_timeout_defaults_to_declining():
    from argus.voice.confirm import make_voice_confirmer

    speaker = MagicMock()
    transcriber = MagicMock()
    confirmer = make_voice_confirmer(speaker, transcriber)

    with patch("argus.voice.audio_io.record_followup", return_value=None), \
         patch("argus.ui.commands.request_confirmation", return_value=7), \
         patch("argus.ui.commands.wait_for_confirmation", return_value=None):
        result = confirmer("run_shell", {"command": "dir"})

    assert result is False
    # Should speak a heads-up rather than silently declining.
    assert speaker.speak.call_count >= 1
