import logging
from logging.handlers import RotatingFileHandler

from argus.config import settings


def setup_logging() -> None:
    """Console stays quiet (WARNING+ by default) for normal interactive
    use; everything INFO+ always goes to a rotating file so a session can
    be debugged after the fact instead of only by watching the live
    terminal. Set CONSOLE_LOG_LEVEL=INFO for a debug session to see that
    same detail live instead of tailing the file separately."""
    log_path = settings.data_dir / "argus.log"
    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, settings.console_log_level.upper(), logging.WARNING))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
