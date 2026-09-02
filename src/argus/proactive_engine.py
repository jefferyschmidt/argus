import logging
import threading

from argus.orchestrator import Orchestrator

log = logging.getLogger(__name__)


class ProactiveEngine:
    """Owns Argus's proactive/ambient workers (ROADMAP.md Phase 2) --
    decoupled from whichever voice loop happens to be running, instead of
    being threads owned directly by VoiceLoop. Confirmed live: before this
    existed, RealtimeVoiceLoop had none of these at all -- no proactive
    reminders, no email watching, no context-awareness check-ins, no
    stuck detection, no research digest, no scheduled routines, no memory
    consolidation -- because there was nowhere else for them to live.

    U-C4 (PRD §5/§7 build order): this now also constructs and starts the
    perception/salience stack -- SpineEngine (Phase A sensors), WorldModel
    (Phase B), SalienceEngine/SalienceDispatcher (Phase C) -- and is the
    ONE place speak_fn/interaction_lock still get handed out directly,
    rather than to every worker. Each retrofitted worker now submits a
    Candidate through the shared SalienceDispatcher instead of deciding
    for itself whether to speak -- see dispatch.py's docstring. Window
    focus, a new email, a research finding, a stuck-detection offer, a
    freshly-ingested file: all just candidates now, arbitrated in one
    place instead of seven independent judgments.

    routine_worker is the one exception, kept on the old (speak_fn,
    interaction_lock) contract directly -- see routine_worker.py's own
    docstring for why: a scheduled routine's goal streams its spoken
    answer live via handle_streaming as the model generates it, which
    doesn't fit a single-Candidate speak/hold/ambient decision without
    buffering the whole response first and defeating the point of
    streaming it. It's also not an ambient "is this worth interrupting
    for" judgment the way the other six are -- it's a user-scheduled
    action that runs and reports on its own explicit timetable."""

    def __init__(self, orchestrator: Orchestrator, speak_fn, interaction_lock: threading.Lock):
        self.orchestrator = orchestrator

        from argus.rules.matcher import RuleMatcher
        from argus.salience.budget import InterruptionBudget
        from argus.salience.dispatch import SalienceDispatcher
        from argus.salience.engine import SalienceEngine
        from argus.salience.escalation import EscalationScheduler
        from argus.salience.held import HeldQueue
        from argus.spine.engine import SpineEngine
        from argus.world.model import WorldModel
        from argus.world.rhythms import RhythmStore
        from argus.world.threads import ThreadStore

        # Reuses orchestrator.spine rather than building its own (P4) --
        # constructed unconditionally in Orchestrator.__init__ (also used
        # there by task_runner/compose_document), so a document.composed
        # or task.finished observation is immediately visible here too,
        # not just eventually-consistent across two WAL connections.
        self.spine = self.orchestrator.spine
        self.spine_engine = SpineEngine(store=self.spine)
        self.threads = ThreadStore(self.spine)
        self.rhythms = RhythmStore()
        self.world_model = WorldModel(spine=self.spine, threads=self.threads, rhythms=self.rhythms)

        # Reuses orchestrator.rule_store/.decision_log rather than
        # building its own (P4) -- both are constructed unconditionally
        # in Orchestrator.__init__ specifically so this engine, built
        # from that orchestrator afterwards, can share them: a rule
        # revoked via the introspection tools is immediately visible to
        # matching, and a decision logged here is immediately explainable
        # through explain_last_action.
        self.rule_matcher = RuleMatcher(self.orchestrator.rule_store)
        self.budget = InterruptionBudget()
        self.held = HeldQueue()
        self.salience_engine = SalienceEngine(
            self.rule_matcher, self.budget, self.held, rhythms=self.rhythms, spine=self.spine,
            decision_log=self.orchestrator.decision_log,
        )
        self.dispatcher = SalienceDispatcher(self.salience_engine, self.world_model, speak_fn, interaction_lock)
        # Escalation delivery reuses the same speak_fn -- channel-specific
        # routing (ambient/push) isn't implemented anywhere yet, so every
        # channel currently just speaks.
        self.escalation_scheduler = EscalationScheduler(threads=self.threads, deliver_fn=lambda channel, text: speak_fn(text))

        from argus.context_awareness import ContextAwarenessWorker
        self.context_awareness = ContextAwarenessWorker(self.dispatcher)

        from argus.email_watcher import EmailWatcher
        self.email_watcher = EmailWatcher(orchestrator.router, self.dispatcher, self.threads)

        from argus.routine_worker import RoutineWorker
        self.routine_worker = RoutineWorker(orchestrator, speak_fn, interaction_lock)

        from argus.knowledge_watcher import KnowledgeWatcher
        self.knowledge_watcher = KnowledgeWatcher(self.dispatcher)

        from argus.research_digest import ResearchDigestWorker
        self.research_digest = ResearchDigestWorker(orchestrator.router, self.dispatcher)

        from argus.stuck_detection import StuckDetectionWorker
        self.stuck_detection = StuckDetectionWorker(orchestrator.router, self.dispatcher)

        from argus.memory.consolidation_worker import ConsolidationWorker
        self.consolidation_worker = ConsolidationWorker(orchestrator.router, orchestrator.memory)

    def start(self) -> None:
        """Starts every worker's poll loop on its own daemon thread, plus
        the spine sensors and the escalation scheduler. Split from
        __init__ so a caller can construct the engine (and reach
        individual workers, e.g. for suppress_current()/check_now()-style
        on-demand triggers) before committing to actually running them.

        Each start is isolated. Before U-C4 this method started seven
        cheap in-process workers; it now also starts the spine sensors and
        the escalation scheduler, and both VoiceLoop and RealtimeVoiceLoop
        construct a ProactiveEngine during their own __init__. An
        unhandled failure here would therefore stop Argus from starting at
        all -- turning "proactive features are degraded" into "the
        assistant does not run", which is the opposite of this repo's
        fail-soft convention for optional subsystems."""
        starters = [
            ("spine sensors", self.spine_engine.start),
            ("escalation scheduler", self.escalation_scheduler.start),
        ]
        # Phase I autonomous tasks (PRD §6): off by default, and only
        # present at all when Orchestrator constructed one (enable_task_runner).
        # reconcile_on_startup() -- not a poll loop of its own, unlike the
        # other two -- is what "starting" this subsystem means: any task
        # left `running` from before a restart becomes `failed`, never
        # auto-resumed.
        if self.orchestrator.task_runner is not None:
            starters.append(("task runner reconciliation", self.orchestrator.task_runner.reconcile_on_startup))
        for name, starter in starters:
            try:
                starter()
            except Exception:
                log.exception("Could not start %s -- continuing without it", name)
        for worker in (
            self.context_awareness,
            self.email_watcher,
            self.routine_worker,
            self.knowledge_watcher,
            self.research_digest,
            self.stuck_detection,
            self.consolidation_worker,
        ):
            try:
                threading.Thread(target=worker.run, daemon=True).start()
            except Exception:
                log.exception("Could not start %s -- continuing without it", type(worker).__name__)
