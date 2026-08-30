import threading

from argus.orchestrator import Orchestrator


class ProactiveEngine:
    """Owns Argus's proactive/ambient workers (ROADMAP.md Phase 2) --
    decoupled from whichever voice loop happens to be running, instead of
    being threads owned directly by VoiceLoop. Confirmed live: before this
    existed, RealtimeVoiceLoop had none of these at all -- no proactive
    reminders, no email watching, no context-awareness check-ins, no
    stuck detection, no research digest, no scheduled routines, no memory
    consolidation -- because there was nowhere else for them to live.

    This only relocates *construction and thread-starting*. Each worker
    still decides when to speak and retries via the exact same
    (speak_fn, interaction_lock) contract it always has -- see
    email_watcher.py's _pending_delivery pattern for how a worker handles
    "Argus was busy, try again next poll." Nothing about how a worker
    decides to speak changes here."""

    def __init__(self, orchestrator: Orchestrator, speak_fn, interaction_lock: threading.Lock):
        self.orchestrator = orchestrator

        from argus.context_awareness import ContextAwarenessWorker
        self.context_awareness = ContextAwarenessWorker(orchestrator, speak_fn, interaction_lock)

        from argus.email_watcher import EmailWatcher
        self.email_watcher = EmailWatcher(orchestrator, speak_fn, interaction_lock)

        from argus.routine_worker import RoutineWorker
        self.routine_worker = RoutineWorker(orchestrator, speak_fn, interaction_lock)

        from argus.knowledge_watcher import KnowledgeWatcher
        self.knowledge_watcher = KnowledgeWatcher(speak_fn, interaction_lock)

        from argus.research_digest import ResearchDigestWorker
        self.research_digest = ResearchDigestWorker(orchestrator.router, speak_fn, interaction_lock)

        from argus.stuck_detection import StuckDetectionWorker
        self.stuck_detection = StuckDetectionWorker(orchestrator.router, speak_fn, interaction_lock)

        from argus.memory.consolidation_worker import ConsolidationWorker
        self.consolidation_worker = ConsolidationWorker(orchestrator.router, orchestrator.memory)

    def start(self) -> None:
        """Starts every worker's poll loop on its own daemon thread. Split
        from __init__ so a caller can construct the engine (and reach
        individual workers, e.g. for suppress_current()/check_now()-style
        on-demand triggers) before committing to actually running them."""
        for worker in (
            self.context_awareness,
            self.email_watcher,
            self.routine_worker,
            self.knowledge_watcher,
            self.research_digest,
            self.stuck_detection,
            self.consolidation_worker,
        ):
            threading.Thread(target=worker.run, daemon=True).start()
