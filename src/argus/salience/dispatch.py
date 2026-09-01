"""U-C4 (PRD §7's build order + §5's framing: "one arbiter replacing
seven independent 'should I speak?' judgments"). This is that arbiter's
delivery edge: the ONE place speak_fn is called from now on. A proactive
worker submits a Candidate; SalienceDispatcher asks SalienceEngine what
to do with it and, only if the answer is "speak", actually says it.
Workers no longer hold speak_fn/interaction_lock at all -- window focus,
a new email, a research finding are all just candidates now, not their
own reason to talk."""

import logging

from argus.salience.engine import SalienceEngine
from argus.salience.scoring import Candidate, Decision
from argus.ui import events as ui_events
from argus.world.model import WorldModel

log = logging.getLogger(__name__)


class SalienceDispatcher:
    def __init__(self, engine: SalienceEngine, world_model: WorldModel, speak_fn, interaction_lock):
        self.engine = engine
        self.world_model = world_model
        self._speak_fn = speak_fn
        self._interaction_lock = interaction_lock

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

        if decision.action == "speak" and not self._deliver(candidate.text):
            # Argus was mid-conversation. The interruption budget slot is
            # already spent (SalienceEngine.decide() consumed it before
            # we got here), but nothing is ever silently dropped (§5.4) --
            # this queues it the same as an ordinary hold rather than
            # losing it outright.
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
            ui_events.publish({"type": "transcript", "role": "argus", "text": text})
            ui_events.publish({"type": "caption", "text": text})
            self._speak_fn(text)
        finally:
            self._interaction_lock.release()
        return True
