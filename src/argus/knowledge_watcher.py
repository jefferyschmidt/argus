import logging
import time
from pathlib import Path

from argus.config import settings
from argus.ingest import _SUPPORTED_EXTENSIONS, ingest_file
from argus.ui import events as ui_events

log = logging.getLogger(__name__)


class KnowledgeWatcher:
    """Second-brain ingestion (README dream/stretch item): watches a folder
    and, when a new PDF/txt/md file shows up, extracts + chunks + stores it
    into semantic memory automatically -- drop a file in, it's recallable
    in conversation from then on, no manual "ingest this" needed.

    Same backlog-safety concern as EmailWatcher applies here: a first run
    against a folder that already has years of files in it shouldn't try to
    ingest all of them at once. Unlike email (which has a server-side
    UIDNEXT to baseline against), a local folder has no equivalent, so
    instead every already-present file is ingested once on first startup
    (there's no backlog-flood risk the way there is with thousands of
    unseen emails -- a personal knowledge folder is realistically dozens of
    files, not thousands, and ingestion is local/free, not an LLM call per
    item). After that, only files new or modified since the last successful
    check are (re-)ingested, tracked by mtime, not persisted across
    restarts -- acceptable for a first pass, same tradeoff EmailWatcher
    makes with its in-memory announced-set."""

    def __init__(self, speak_fn=None, interaction_lock=None):
        self._speak_fn = speak_fn
        self._interaction_lock = interaction_lock
        self._seen_mtimes: dict[str, float] = {}
        self._first_check = True

    def run(self) -> None:
        while True:
            time.sleep(settings.knowledge_watch_poll_seconds)
            if not settings.knowledge_watch_enabled or not settings.knowledge_watch_folder:
                continue
            try:
                self.check_now()
            except Exception:
                log.exception("Knowledge watcher check failed")

    def check_now(self) -> None:
        folder = Path(settings.knowledge_watch_folder)
        if not folder.is_dir():
            return

        newly_ingested = []
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                continue
            mtime = path.stat().st_mtime
            key = str(path.resolve())
            if self._seen_mtimes.get(key) == mtime:
                continue
            self._seen_mtimes[key] = mtime
            try:
                chunks = ingest_file(path)
            except Exception:
                log.exception("Failed to ingest %s", path)
                continue
            if chunks:
                newly_ingested.append(path.name)

        if newly_ingested and not self._first_check:
            self._announce(newly_ingested)
        self._first_check = False

    def _announce(self, filenames: list[str]) -> None:
        if len(filenames) == 1:
            text = f"I just read through {filenames[0]} and added it to memory."
        else:
            text = f"I just read through {len(filenames)} new files and added them to memory."

        if self._interaction_lock is not None and not self._interaction_lock.acquire(blocking=False):
            return
        try:
            ui_events.publish({"type": "transcript", "role": "argus", "text": text})
            ui_events.publish({"type": "caption", "text": text})
            if self._speak_fn:
                self._speak_fn(text)
        finally:
            if self._interaction_lock is not None:
                self._interaction_lock.release()
