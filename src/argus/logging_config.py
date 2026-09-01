import logging
from logging.handlers import RotatingFileHandler

from argus.config import settings


class _SafeStreamHandler(logging.StreamHandler):
    """A non-ASCII log argument (e.g. an emoji in an email subject) raises
    UnicodeEncodeError on the Windows console, which is cp1252, not utf-8
    (P5, confirmed live 2026-08-31). The default StreamHandler.emit()
    catches that and calls handleError(), which prints a "--- Logging
    error ---" traceback to stderr instead -- ugly, and on a Windows
    console that traceback print can itself raise the same way. This
    re-encodes the line with escapes instead, so the log line survives
    (in a readable-if-imperfect form) rather than turning into a second
    error."""

    def emit(self, record: logging.LogRecord) -> None:
        # Deliberately not `super().emit()` -- the base Handler.emit()
        # already catches everything internally and calls handleError(),
        # which just prints the traceback to stderr instead of raising to
        # us, so a try/except around super().emit() never actually sees
        # the UnicodeEncodeError. Writing directly is what lets this
        # method catch it and substitute a safe re-encoding instead.
        try:
            msg = self.format(record)
            stream = self.stream
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                encoding = getattr(stream, "encoding", None) or "ascii"
                safe = msg.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")
                stream.write(safe + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


def setup_logging() -> None:
    """Console stays quiet (WARNING+ by default) for normal interactive
    use; everything INFO+ always goes to a rotating file so a session can
    be debugged after the fact instead of only by watching the live
    terminal. Set CONSOLE_LOG_LEVEL=INFO for a debug session to see that
    same detail live instead of tailing the file separately."""
    log_path = settings.data_dir / "argus.log"
    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    console_handler = _SafeStreamHandler()
    console_handler.setLevel(getattr(logging, settings.console_log_level.upper(), logging.WARNING))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
