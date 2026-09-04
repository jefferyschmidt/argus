"""U-C4 (PRD §7's build order + §5's framing: "one arbiter replacing
seven independent 'should I speak?' judgments"). This is that arbiter's
delivery edge: the ONE place speak_fn is called from now on. A proactive
worker submits a Candidate; SalienceDispatcher asks SalienceEngine what
to do with it and, only if the answer is "speak", actually says it.
Workers no longer hold speak_fn/interaction_lock at all -- window focus,
a new email, a research finding are all just candidates now, not their
own reason to talk."""

import logging
import time

from argus.salience.engine import SalienceEngine
from argus.salience.scoring import Candidate, Decision
from argus.voice.captions import publish_spoken
from argus.world.model import WorldModel

log = logging.getLogger(__name__)


class SalienceDispatcher:
    def __init__(self, engine: SalienceEngine, world_model: WorldModel, speak_fn, interaction_lock, escalation=None):
        self.engine = engine
        self.world_model = world_model
        self._speak_fn = speak_fn
        self._interaction_lock = interaction_lock
        # PRD §19 unit 37: the producer side of escalation scheduling --
        # EscalationScheduler.schedule() was never called anywhere in
        # production, so its own drain timer only ever drained an empty
        # queue. Wired here, not in the tick, so EVERY candidate that
        # carries escalation steps gets them scheduled on actual delivery,
        # not just ones the tick happens to submit. None (a caller that
        # doesn't have one yet) just means escalation never schedules --
        # same "optional collaborator, fails soft" shape as everywhere
        # else in this codebase.
        self.escalation = escalation
        # PRD §15 unit 32: the thread id and timestamp of the most recent
        # item this dispatcher actually SPOKE about -- a hold (blocked by
        # the interaction lock) or an ambient decision never sets these,
        # because the user never heard either. This is what lets a
        # spoken "got it" resolve to a specific thread without an LLM in
        # the loop: only the single most recently spoken-about thread is
        # ever eligible, never a bulk close, never an older one.
        self.last_spoken_thread_id: int | None = None
        self.last_spoken_ts: float | None = None

    def submit(self, candidate: Candidate, observation=None, now: float | None = None) -> Decision:
        """Scores `candidate` against the current world snapshot and, if
        the decision is "speak", delivers it. Never raises -- a failure
        here must not take down the worker that called it (§0: every
        background thread catches and logs everything)."""
        try:
            snapshot = self.world_model.snapshot()
            decision = self.engine.decide(candidate, snapshot, observation=observation, now=now)
        except Exception:
            log.exception("Salience decide() failed for candidate kind=%s -- treating as hold", candidate.kind)
            return Decision(action="hold", reason="salience decide() raised")

        if decision.action == "speak":
            if self._deliver(candidate.text):
                # PRD §19 unit 37: action=="speak" is the DECISION, not
                # proof it was actually heard -- _deliver() can still
                # no-op below when Argus is mid-conversation. This is
                # the one place that distinction is knowable, so it's
                # recorded on the Decision itself for callers (the
                # reminder consumer, most importantly) that must not act
                # as if delivered until it actually happened.
                decision.delivered = True
                if candidate.thread_id is not None:
                    self.last_spoken_thread_id = candidate.thread_id
                    self.last_spoken_ts = now if now is not None else time.time()
                if decision.escalation and self.escalation is not None:
                    self.escalation.schedule(
                        text=candidate.text, steps=decision.escalation,
                        thread_id=candidate.thread_id, now=now,
                    )
            else:
                # Argus was mid-conversation. The interruption budget slot
                # is already spent (SalienceEngine.decide() consumed it
                # before we got here), but nothing is ever silently
                # dropped (§5.4) -- this queues it the same as an ordinary
                # hold rather than losing it outright. Not delivered, so
                # last_spoken_*/decision.delivered/escalation scheduling
                # all stay untouched -- the user never heard it.
                self.engine.held.add(
                    kind=candidate.kind, subject=candidate.subject, text=candidate.text,
                    score=1.0, thread_id=candidate.thread_id,
                )
        return decision

    def _deliver(self, text: str) -> bool:
        """Returns whether it actually got announced -- False means
        Argus was mid-conversation and the caller should treat this as
        not-yet-delivered rather than done."""
        if not self._interaction_lock.acquire(blocking=False):
            return False
        try:
            publish_spoken(text)
            self._speak_fn(text)
        finally:
            self._interaction_lock.release()
        return True
