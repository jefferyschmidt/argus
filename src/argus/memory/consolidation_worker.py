import logging
import time

from argus.config import settings
from argus.memory.consolidation import consolidate

log = logging.getLogger(__name__)


class ConsolidationWorker:
    """Background poll loop for argus/memory/consolidation.py -- see that
    module for what consolidation actually does. Kept as its own thin
    worker (same shape as KnowledgeWatcher/StuckDetectionWorker) rather
    than folded into MemoryManager itself, since it needs a router (an LLM
    call) and runs on its own clock, independent of any single
    conversation turn."""

    def __init__(self, router, memory_manager):
        self._router = router
        self._memory_manager = memory_manager

    def run(self) -> None:
        while True:
            time.sleep(settings.memory_consolidation_poll_seconds)
            if not settings.memory_consolidation_enabled:
                continue
            self.check_now()

    def check_now(self) -> list[str]:
        try:
            return consolidate(self._router, self._memory_manager)
        except Exception:
            log.exception("Memory consolidation check failed")
            return []
