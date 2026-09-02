"""PRD.md §5 -- one arbiter replacing seven independent "should I speak?"
judgments. Ties scoring.score_with_value() + InterruptionBudget +
HeldQueue together into the single decide() call a caller actually
wants; nothing here recomputes the formula itself (unit 16a) or the
budget/queue mechanics (this unit's other two files) -- it just wires
them in the order Appendix A.2 specifies: score first, budget check
after (never folded into the score), hold items queued rather than
dropped."""

import time
from datetime import datetime

from argus.salience.budget import InterruptionBudget
from argus.salience.held import HeldQueue
from argus.salience.scoring import Candidate, Decision, interruption_cost, score_with_value
from argus.world.model import WorldSnapshot
from argus.world.rhythms import RhythmStore


class SalienceEngine:
    def __init__(
        self, matcher, budget: InterruptionBudget, held: HeldQueue,
        rhythms: RhythmStore | None = None, spine=None, llm_tiebreak=None, decision_log=None,
    ):
        self.matcher = matcher
        self.budget = budget
        self.held = held
        self.rhythms = rhythms
        self.spine = spine
        self.llm_tiebreak = llm_tiebreak
        # PRD §7.6: explain_last_action reads this back. Optional --
        # existing callers/tests that don't pass one just don't get a
        # persisted history, same as before this existed.
        self.decision_log = decision_log

    def decide(
        self, candidate: Candidate, snapshot: WorldSnapshot, observation=None, now: float | None = None,
    ) -> Decision:
        now = now if now is not None else time.time()
        rhythm_fit = self._rhythm_fit(candidate, now)
        cost = interruption_cost(snapshot, self.spine, self.rhythms, now)

        decision, final_score = score_with_value(
            candidate, snapshot, matcher=self.matcher, rhythm_fit=rhythm_fit, interruption_cost=cost,
            observation=observation, llm_tiebreak=self.llm_tiebreak, now=now,
        )

        if decision.action == "speak":
            # Budget is checked after scoring, never folded into the
            # score (Appendix A.2) -- this is that check.
            if self.budget.available(now):
                self.budget.consume(now)
            else:
                decision = Decision(action="hold", reason=f"{decision.reason}; interruption budget exhausted")

        if decision.action == "hold":
            # Nothing is ever silently dropped (§5.4).
            self.held.add(
                kind=candidate.kind, subject=candidate.subject, text=candidate.text,
                score=final_score, thread_id=candidate.thread_id,
            )

        if self.decision_log is not None:
            self.decision_log.record(
                kind=candidate.kind, subject=candidate.subject, action=decision.action, reason=decision.reason, ts=now,
            )

        return decision

    def _rhythm_fit(self, candidate: Candidate, now: float) -> float:
        if self.rhythms is None or self.spine is None:
            return 0.5
        hour = datetime.fromtimestamp(now).hour
        return self.rhythms.rhythm_fit(candidate.kind, hour, self.spine)
