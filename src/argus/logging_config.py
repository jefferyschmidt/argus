import logging
from logging.handlers import RotatingFileHandler

from argus.config import settings


def setup_logging() -> None:
    """Console stays quiet (WARNING+) for interactive use; everything INFO+
    also goes to a rotating file so a session can be debugged after the fact
    instead of only by watching the live terminal."""
    log_path = settings.data_dir / "argus.log"
    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
