import logging
import threading
import time

from argus.config import settings
from argus.orchestrator import Orchestrator
from argus.spine.observation import Observation
from argus.ui import commands as ui_commands

log = logging.getLogger(__name__)

# PRD §19 unit 37: induction and retention both run at most once a day,
# gated on their own last-run timestamps -- not a user-configurable
# setting, since nothing in the PRD asks for one; the tick's own cadence
# (settings.proactive_tick_seconds) is what's actually tunable.
_DAILY_SECONDS = 86400.0


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

        # PRD §7.4 + Appendix A.3 "Effect reversal", wired live for the
        # first time at the PRD §15 unit 30 gate (acknowledge_thread
        # below is what actually resolves an instance): constructed here
        # so it's the one shared instance (P4), matching rule_store/
        # decision_log's own constructed-once-in-Orchestrator-reused-here
        # treatment above.
        from argus.rules.instances import RuleInstanceStore
        self.rule_instances = RuleInstanceStore()

        self.salience_engine = SalienceEngine(
            self.rule_matcher, self.budget, self.held, rhythms=self.rhythms, spine=self.spine,
            decision_log=self.orchestrator.decision_log,
        )
        # Built BEFORE the dispatcher (order matters here) so it can be
        # handed in -- PRD §19 unit 37: schedule() was never called
        # anywhere in production, so the scheduler's own drain timer only
        # ever drained an empty queue. Escalation delivery reuses the
        # same speak_fn -- channel-specific routing (ambient/push) isn't
        # implemented anywhere yet, so every channel currently just speaks.
        self.escalation_scheduler = EscalationScheduler(threads=self.threads, deliver_fn=lambda channel, text: speak_fn(text))
        self.dispatcher = SalienceDispatcher(
            self.salience_engine, self.world_model, speak_fn, interaction_lock, escalation=self.escalation_scheduler,
        )

        # PRD §7.5 (G4 induction), wired live for the first time at the
        # PRD §19 unit 37 gate: constructed here so it's the one shared
        # instance (P4), reusing rule_store/held rather than building
        # either again.
        from argus.rules.induction import InductionEngine
        self.induction_engine = InductionEngine(self.orchestrator.rule_store, self.held)

        # PRD §19 unit 37: cursors for the proactive tick's own timer-
        # gated steps (see _run_proactive_tick). Rule-firing starts from
        # "now", not epoch -- a fresh process must not replay years of
        # spine history through automation rules that fire real tool
        # calls. Reap/induction/retention start from epoch instead, so a
        # long-neglected install (spine growing unbounded, a stale thread
        # that should have timed out weeks ago) catches up on the very
        # first tick after a restart rather than waiting a full cadence.
        self._last_rule_check_ts = time.time()
        self._last_reap_ts = 0.0
        self._last_induction_ts = 0.0
        self._last_prune_ts = 0.0

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

        # PRD §15's governing constraint: the dashboard and the voice
        # interface are two projections of ONE world model. Self-
        # registers exactly like Orchestrator does for set_active_router
        # (ui/commands.py) -- ui/server.py reaches this instance instead
        # of ever constructing its own WorldModel/ThreadStore/SpineStore.
        ui_commands.set_active_proactive_engine(self)

    def acknowledge_thread(self, thread_id: int, via: str) -> bool:
        """PRD Appendix A.1 / §15 unit 30: this is the ONE mechanism
        behind "got it" -- whether spoken or clicked. It records
        thread.acknowledged with the exact {"thread_id", "via"} shape
        §3.1 specifies, "voice" or "ui" being the only difference,
        because there is no separate UI-only close path. That
        observation is what the `user_acknowledged` predicate (Appendix
        A.1) reads, so closing the thread and resolving any rule
        instance watching it are just evaluating existing conditions
        against it -- not a special case for acknowledgment.

        Reaps both the thread and any watching rule instance
        synchronously in addition to the periodic tick's own reap-on-
        timer (unit 37) -- "click got it" -> "watch it happen" is the
        whole point of unit 30, and waiting out however much of the
        tick's own cadence remains would not close the loop
        immediately, which is the entire reason this method exists
        rather than just letting the next tick catch it.

        Returns False only if the thread doesn't exist; a thread that
        exists but whose close_condition isn't satisfied by
        acknowledgment alone (e.g. still waiting on a reply) still
        returns True -- the observation was recorded either way, exactly
        as a spoken "got it" would too."""
        thread = self.threads.get(thread_id)
        if thread is None:
            return False

        self.spine.record(Observation(
            source="ui" if via == "ui" else "voice", kind="thread.acknowledged", ts=time.time(),
            subject=thread.subject, payload={"thread_id": thread_id, "via": via},
        ))
        # Appendix A.1 evaluation cadence: "on a timer, and immediately
        # after any observation whose kind appears in an open thread's
        # condition" -- this observation is exactly that trigger.
        self.threads.reap(now=time.time())
        self.world_model.invalidate()
        self.rule_instances.reap(
            registry=self.orchestrator.tools, rule_store=self.orchestrator.rule_store,
            thread_store=self.threads, spine=self.spine,
        )
        return True

    # -- PRD §19 unit 37: the proactive tick ------------------------------
    #
    # One periodic loop driving every orphaned producer, started alongside
    # the other workers (see start()) and isolated the same way: each step
    # wrapped in its own try/except so one failure can't block the rest.
    # No step calls an LLM -- rule matching and reaping are deterministic
    # by design (RuleMatcher.match() only calls an LLM through a
    # caller-supplied fuzzy_judge, and this engine's own RuleMatcher never
    # gets one), which is the whole point: this loop must be safe to run
    # every settings.proactive_tick_seconds without ever touching the
    # daily budget or a rate limit.

    def _tick_reminders(self, now: float) -> None:
        """Units 37+38: reads ReminderStore.list_due() DIRECTLY, not the
        spine's reminder.due observation (which unit 38 demotes to a
        pure timeline record, deduped, mutating nothing). Marks a
        reminder notified ONLY after Decision.delivered confirms it was
        actually spoken -- a reminder that couldn't be delivered (Argus
        mid-conversation) is simply left un-notified, so
        ReminderStore.list_due() itself re-surfaces it next tick. This
        is the durable, restart-safe version of the email watcher's old
        in-memory _pending_delivery retry list (commit 3f07efc,
        retired at U-C4 once HeldQueue offered a general version of the
        same guarantee) -- reminders need the stronger, always-retry
        form HeldQueue alone doesn't give them: a held reminder must
        keep trying every tick, not just sit visible in Held forever."""
        from datetime import datetime

        from argus.memory.reminders import ReminderStore
        from argus.memory.store import get_connection
        from argus.salience.scoring import Candidate, base_urgency_for

        now_iso = datetime.fromtimestamp(now).astimezone().isoformat()
        conn = get_connection()
        try:
            store = ReminderStore(conn)
            for row in store.list_due(now_iso):
                candidate = Candidate(
                    observation_id=None, kind="reminder.due", subject=None,
                    text=f"Reminder: {row['text']}", base_urgency=base_urgency_for("reminder.due"),
                )
                decision = self.dispatcher.submit(candidate, now=now)
                if decision.delivered:
                    store.mark_notified(row["id"])
        finally:
            conn.close()

    def _tick_rule_firing(self, now: float) -> None:
        """Step 3: RuleInstanceStore.fire() was previously called only
        from tests -- no automation rule ever actually ran in
        production; this is what makes standing automations and
        standing authorizations actually execute. A notify-action match
        is turned into a Candidate and goes through the normal salience
        path, same as every other producer.

        boost/suppress-action rules are deliberately NOT re-submitted
        here: SalienceEngine.decide() already consults RuleMatcher
        itself (Appendix A.2 step 1) when the observation's own real
        producer (email watcher, etc.) submits its own candidate, so
        doing it again here would only risk a second, lower-fidelity
        announcement of the same event, not add coverage.

        since/until has a narrow, accepted race at the exact tick
        boundary -- SpineStore.query() has no exclusive-bound option
        and Observation carries no row id to dedupe by, so an
        observation landing at exactly the cursor timestamp could in
        principle be seen twice. Vanishingly unlikely at time.time()'s
        resolution against a 15s default cadence, and not worth a spine
        schema change to close completely."""
        from argus.salience.scoring import Candidate, base_urgency_for

        since = self._last_rule_check_ts
        self._last_rule_check_ts = now
        for obs in self.spine.query(since=since, limit=500):
            for rule in self.rule_matcher.match(obs):
                try:
                    action_type = rule.action.get("type")
                    if action_type == "tool_call":
                        self.rule_instances.fire(rule=rule, registry=self.orchestrator.tools, now=now)
                    elif action_type == "notify":
                        candidate = Candidate(
                            observation_id=None, kind=obs.kind, subject=obs.subject,
                            text=rule.action.get("text") or rule.natural_language,
                            base_urgency=base_urgency_for(obs.kind),
                        )
                        self.dispatcher.submit(candidate, observation=obs, now=now)
                except Exception:
                    log.exception("Rule #%s failed to fire for observation kind=%s", rule.id, obs.kind)

    def _tick_reap(self, now: float) -> None:
        """Step 4 / Appendix A.1: reap() previously only ran inside
        acknowledge_thread() -- a thread with a pure timeout,
        observation_seen, or value_threshold close condition, never
        manually acknowledged, stayed open forever. This is the timer
        Appendix A.1's own evaluation-cadence description already
        claimed existed. The reap-on-acknowledge path is unchanged."""
        if now - self._last_reap_ts < settings.thread_reap_seconds:
            return
        self._last_reap_ts = now
        self.threads.reap(now=now)
        self.world_model.invalidate()
        self.rule_instances.reap(
            registry=self.orchestrator.tools, rule_store=self.orchestrator.rule_store,
            thread_store=self.threads, spine=self.spine, now=now,
        )

    def _tick_escalation(self, now: float) -> None:
        """Step 5, drain half: the producer side (schedule() actually
        being called) is wired into SalienceDispatcher.submit() itself,
        not here -- see its docstring; that's what makes this queue
        stop being permanently empty. This just drains, mirroring what
        the scheduler's own timer thread (started separately in
        start()) already does on settings.escalation_poll_seconds --
        calling it again here is idempotent (process_due() only touches
        rows whose fire_ts has already passed) and keeps escalation
        draining on the same cadence as everything else this tick
        drives."""
        self.escalation_scheduler.process_due(now=now)

    def _tick_induction(self, now: float) -> None:
        """Step 6 / §7.5: InductionEngine.run_once() was never scheduled
        anywhere -- G4 induction never actually ran. Proposes only,
        never activates (unchanged); gated to at most once a day
        regardless of how often the tick itself runs."""
        if now - self._last_induction_ts < _DAILY_SECONDS:
            return
        self._last_induction_ts = now
        self.induction_engine.run_once(now=now)

    def _tick_retention(self, now: float) -> None:
        """Step 7: SpineStore.prune() was never called -- the spine grew
        unbounded. Gated to at most once a day."""
        if now - self._last_prune_ts < _DAILY_SECONDS:
            return
        self._last_prune_ts = now
        self.spine.prune(settings.spine_retention_days)

    def _run_proactive_tick(self, now: float | None = None) -> None:
        """One tick: every step below, each isolated so one failing step
        (raise) never stops the others that same tick."""
        now = now if now is not None else time.time()
        for step in (
            self._tick_reminders,
            self._tick_rule_firing,
            self._tick_reap,
            self._tick_escalation,
            self._tick_induction,
            self._tick_retention,
        ):
            try:
                step(now)
            except Exception:
                # getattr, not step.__name__ directly: a test double (or
                # any non-plain-function callable) standing in for a step
                # may not have __name__ at all, and this line runs INSIDE
                # the except block -- raising here would escape this
                # try/except entirely, defeating the one thing this loop
                # exists to guarantee.
                name = getattr(step, "__name__", repr(step))
                log.exception("Proactive tick step %s failed -- continuing with the rest of this tick", name)

    def _proactive_tick_loop(self) -> None:
        while True:
            try:
                self._run_proactive_tick()
            except Exception:
                log.exception("Proactive tick failed")
            time.sleep(settings.proactive_tick_seconds)

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
        # PRD §19 unit 37: the proactive tick, started with the other
        # workers -- see _proactive_tick_loop's own docstring block above.
        try:
            threading.Thread(target=self._proactive_tick_loop, daemon=True).start()
        except Exception:
            log.exception("Could not start the proactive tick -- continuing without it")
