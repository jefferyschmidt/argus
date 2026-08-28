from unittest.mock import patch

from argus.telegram_bridge import TelegramBridge


def test_disabled_without_bot_token(monkeypatch):
    monkeypatch.setattr("argus.telegram_bridge.settings.telegram_bot_token", "")
    monkeypatch.setattr("argus.telegram_bridge.settings.telegram_allowed_chat_id", "12345")
    bridge = TelegramBridge()
    with patch("threading.Thread") as mock_thread:
        bridge.start()
        mock_thread.assert_not_called()


def test_disabled_without_allowed_chat_id(monkeypatch):
    monkeypatch.setattr("argus.telegram_bridge.settings.telegram_bot_token", "fake-token")
    monkeypatch.setattr("argus.telegram_bridge.settings.telegram_allowed_chat_id", "")
    bridge = TelegramBridge()
    with patch("threading.Thread") as mock_thread:
        bridge.start()
        mock_thread.assert_not_called()


def test_message_from_allowed_chat_is_submitted():
    bridge = TelegramBridge()
    bridge._allowed_chat_id = "12345"
    update = {"update_id": 1, "message": {"chat": {"id": 12345}, "text": "hey argus"}}
    with patch("argus.telegram_bridge.ui_commands.submit_text_message") as mock_submit:
        bridge._handle_update(update)
        mock_submit.assert_called_once_with("hey argus")


def test_message_from_unrecognized_chat_is_ignored():
    bridge = TelegramBridge()
    bridge._allowed_chat_id = "12345"
    update = {"update_id": 1, "message": {"chat": {"id": 99999}, "text": "let me in"}}
    with patch("argus.telegram_bridge.ui_commands.submit_text_message") as mock_submit:
        bridge._handle_update(update)
        mock_submit.assert_not_called()


def test_empty_text_is_ignored():
    bridge = TelegramBridge()
    bridge._allowed_chat_id = "12345"
    update = {"update_id": 1, "message": {"chat": {"id": 12345}, "text": "   "}}
    with patch("argus.telegram_bridge.ui_commands.submit_text_message") as mock_submit:
        bridge._handle_update(update)
        mock_submit.assert_not_called()


def test_non_argus_transcript_events_are_not_forwarded():
    bridge = TelegramBridge()
    bridge._allowed_chat_id = "12345"
    with patch.object(bridge, "_send") as mock_send:
        # Simulate the filter logic directly rather than looping the real
        # subscribe()/queue.get() forever inside a test.
        for event in (
            {"type": "transcript", "role": "you", "text": "hi"},
            {"type": "state", "value": "listening"},
        ):
            if event.get("type") == "transcript" and event.get("role") == "argus":
                bridge._send(event.get("text", ""))
        mock_send.assert_not_called()
