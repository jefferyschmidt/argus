import logging

from argus.logging_config import setup_logging


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


def test_console_handler_falls_back_to_warning_on_invalid_level(monkeypatch, tmp_path):
    monkeypatch.setattr("argus.logging_config.settings.console_log_level", "not_a_real_level")
    monkeypatch.setattr("argus.logging_config.settings.argus_data_dir", str(tmp_path))
    _reset_root_handlers()

    setup_logging()

    console_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler) and not hasattr(h, "baseFilename")]
    assert console_handlers[0].level == logging.WARNING
    _reset_root_handlers()
