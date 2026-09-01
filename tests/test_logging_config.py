import io
import logging

from argus.logging_config import _SafeStreamHandler, setup_logging


def _reset_root_handlers():
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)


def test_console_handler_defaults_to_warning(monkeypatch, tmp_path):
    monkeypatch.setattr("argus.logging_config.settings.console_log_level", "WARNING")
    monkeypatch.setattr("argus.logging_config.settings.argus_data_dir", str(tmp_path))
    _reset_root_handlers()

    setup_logging()

    console_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler) and not hasattr(h, "baseFilename")]
    assert console_handlers[0].level == logging.WARNING
    _reset_root_handlers()


def test_console_handler_respects_info_override(monkeypatch, tmp_path):
    monkeypatch.setattr("argus.logging_config.settings.console_log_level", "INFO")
    monkeypatch.setattr("argus.logging_config.settings.argus_data_dir", str(tmp_path))
    _reset_root_handlers()

    setup_logging()

    console_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler) and not hasattr(h, "baseFilename")]
    assert console_handlers[0].level == logging.INFO
    _reset_root_handlers()


def test_logging_emoji_does_not_raise(monkeypatch, tmp_path):
    """P5: a non-ASCII log argument must never raise or crash the process,
    even when the underlying stream is a cp1252 Windows console that can't
    encode it directly."""
    monkeypatch.setattr("argus.logging_config.settings.console_log_level", "INFO")
    monkeypatch.setattr("argus.logging_config.settings.argus_data_dir", str(tmp_path))
    _reset_root_handlers()

    setup_logging()
    logger = logging.getLogger("test_emoji_logger")
    logger.info("subject line with an emoji: \U0001F4E7")  # should not raise
    _reset_root_handlers()


def test_safe_stream_handler_recovers_from_unicode_encode_error():
    """A stream whose encoding genuinely can't represent the character
    (simulating the real cp1252-console failure) must not propagate the
    UnicodeEncodeError, and the escaped line should still reach the stream."""

    class _Cp1252Stream(io.TextIOBase):
        encoding = "cp1252"

        def __init__(self):
            self.written = []

        def write(self, s):
            self.written.append(s.encode(self.encoding, errors="strict"))
            return len(s)

        def flush(self):
            pass

    stream = _Cp1252Stream()
    handler = _SafeStreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="subject: \U0001F4E7", args=(), exc_info=None,
    )

    handler.emit(record)  # should not raise

    assert any(b"subject" in chunk for chunk in stream.written)


def test_console_handler_falls_back_to_warning_on_invalid_level(monkeypatch, tmp_path):
    monkeypatch.setattr("argus.logging_config.settings.console_log_level", "not_a_real_level")
    monkeypatch.setattr("argus.logging_config.settings.argus_data_dir", str(tmp_path))
    _reset_root_handlers()

    setup_logging()

    console_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler) and not hasattr(h, "baseFilename")]
    assert console_handlers[0].level == logging.WARNING
    _reset_root_handlers()
