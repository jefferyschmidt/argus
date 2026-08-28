import logging
import os
import sys
import threading
import time

log = logging.getLogger(__name__)


def request_restart(delay: float = 0.75) -> None:
    """Replaces the current process with a fresh interpreter invocation of
    the same command -- the only way to pick up code changes on disk,
    since Python doesn't hot-reload a running process. Runs on a short
    delay on its own non-daemon thread so whatever triggered this (an HTTP
    handler, a tool call mid-reply) can finish responding first.

    Deliberately re-invokes via `python -m argus.cli <args>` rather than
    re-execing sys.argv verbatim -- if Argus was launched through the
    installed `argus` console-script (a native .exe launcher stub on
    Windows, not a plain .py file), `sys.executable sys.argv[0]` would try
    to run that stub AS a Python script and fail. Targeting the module
    directly works regardless of how the original process was launched."""

    def _do_restart() -> None:
        time.sleep(delay)

        # os.execv below bypasses normal interpreter shutdown entirely, so
        # anything still queued in MemoryManager._embed_pool would
        # otherwise be silently lost -- give it a bounded window to finish.
        from argus.ui import commands as ui_commands
        memory_manager = ui_commands.get_active_memory_manager()
        if memory_manager is not None:
            try:
                memory_manager.flush_pending_embeds(timeout=3.0)
            except Exception:
                log.exception("Failed to flush pending memory embeds before restart")

        args = sys.argv[1:] if len(sys.argv) > 1 else ["voice"]
        log.info("Restarting Argus: python -m argus.cli %s", " ".join(args))
        os.execv(sys.executable, [sys.executable, "-m", "argus.cli"] + args)

    threading.Thread(target=_do_restart, daemon=False).start()
