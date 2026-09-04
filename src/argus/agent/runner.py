import logging
import time

from argus.agent.audit import AuditLog
from argus.config import settings
from argus.llm.base import Tier
from argus.llm.router import ModelRouter
from argus.tools import ToolRegistry

log = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """You are Argus operating autonomously, without a user present to answer
follow-up questions in real time. You've been given a goal. Figure out what
needs to be done to accomplish it, then do it -- using your tools as needed.

Work step by step. Before you consider yourself done, make sure the goal is
actually satisfied, not just attempted. When finished, give a clear summary
of what you did, the outcome, and anything the user should know or double
check.

You have the same tools and permission tiers as normal conversation --
ALLOW-tier tools run without asking; CONFIRM-tier tools (writing files,
running shell commands, clicking/typing on the desktop) still require a
human to approve them. If one is declined, adapt your plan instead of
retrying the same thing.

If you get stuck, or the goal turns out to be impossible with your current
tools, say so clearly in your summary rather than pretending to have
succeeded."""


class AgentBudgetExceeded(Exception):
    pass


class AgentRunner:
    """Autonomous mode: given a standing goal instead of a chat message,
    runs an extended tool-use loop until the model considers the goal done
    or a budget cap is hit. Every tool call is written to an audit log
    (data/agent_audit.jsonl) so an unattended run can be reviewed after the
    fact -- this is the actual safety mechanism for less-supervised action,
    not a replacement for the permission tiers, which still apply."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        router: ModelRouter | None = None,
        max_iterations: int = 25,
        max_wall_seconds: float = 600,
        max_tokens_total: int | None = None,
        daily_cap_usd: float = 5.0,
    ):
        # PRD/INVARIANTS.md I6: no bare build_default_registry(...) call
        # fallback here -- that silently built a registry missing rules, the
        # AuthorizationChecker, decision_log, and spine (no task tools,
        # no rule introspection, no compose_document/query_timeline, no
        # standing authorizations). It caused the same bug at three call
        # sites (realtime u33, TaskRunner u39, the `argus agent` CLI).
        # Every caller now passes the Orchestrator's own full registry --
        # forgetting it fails at construction instead of silently
        # degrading.
        self.tools = tool_registry
        self.router = router or ModelRouter(daily_cap_usd=daily_cap_usd)
        self.max_iterations = max_iterations
        self.max_wall_seconds = max_wall_seconds
        # None means no token cap beyond max_iterations/max_wall_seconds --
        # existing callers (the `argus agent` CLI) don't set this. Phase I
        # tasks (argus/tasks/) always do, mapped from budget_tokens.
        self.max_tokens_total = max_tokens_total
        self.audit = AuditLog(settings.data_dir / "agent_audit.jsonl")

    def run(self, goal: str, on_progress=None) -> str:
        """on_progress(note: str), if given, fires after every tool call --
        Phase I's TaskWorker uses it to update tasks.progress_note as the
        run proceeds, so "how's that coming?" is answerable without
        interrupting the run (PRD §6)."""
        start = time.monotonic()
        self.audit.record(
            "goal_started", goal=goal,
            max_iterations=self.max_iterations, max_wall_seconds=self.max_wall_seconds,
            max_tokens_total=self.max_tokens_total,
        )

        def on_tool_call(name, tool_input, result, tokens_used=0):
            elapsed = time.monotonic() - start
            self.audit.record(
                "tool_call", name=name, input=tool_input,
                result=str(result)[:2000], elapsed_s=round(elapsed, 1), tokens_used=tokens_used,
            )
            if on_progress is not None:
                on_progress(f"called {name}")
            if elapsed > self.max_wall_seconds:
                raise AgentBudgetExceeded(
                    f"exceeded max_wall_seconds={self.max_wall_seconds} "
                    f"(elapsed {elapsed:.0f}s) after tool call to '{name}'"
                )
            if self.max_tokens_total is not None and tokens_used > self.max_tokens_total:
                raise AgentBudgetExceeded(
                    f"exceeded max_tokens_total={self.max_tokens_total} "
                    f"(used {tokens_used}) after tool call to '{name}'"
                )

        def check_budget(tokens_used):
            """Fires at the top of every tool-loop iteration, before the
            call that iteration would pay for. on_tool_call above only
            fires once a tool has already run, so a breach was noticed
            only after paying for the iteration that caused it -- and a
            long-running tool between two cheap ones could push a run well
            past its wall-clock before anything checked. This bounds the
            overshoot to the iteration already in flight.

            Known limit, deliberate: neither hook can interrupt a single
            hung tool call (a wedged shell command, an MCP server that
            never answers -- both observed in this repo). Killing work
            mid-call needs process isolation, not a callback; recorded
            against PRD Sec 6 rather than half-solved here."""
            elapsed = time.monotonic() - start
            if elapsed > self.max_wall_seconds:
                raise AgentBudgetExceeded(
                    f"exceeded max_wall_seconds={self.max_wall_seconds} (elapsed {elapsed:.0f}s)"
                )
            if self.max_tokens_total is not None and tokens_used > self.max_tokens_total:
                raise AgentBudgetExceeded(
                    f"exceeded max_tokens_total={self.max_tokens_total} (used {tokens_used})"
                )

        try:
            result = self.router.complete_with_tools(
                goal,
                system=AGENT_SYSTEM_PROMPT,
                tool_registry=self.tools,
                force_tier=Tier.ADVANCED,
                max_iterations=self.max_iterations,
                check_budget=check_budget,
                on_tool_call=on_tool_call,
            )
            self.audit.record("goal_finished", summary=result.text, model=result.model)
            return result.text
        except AgentBudgetExceeded as e:
            log.warning("Agent run aborted: %s", e)
            self.audit.record("goal_aborted", reason=str(e))
            return (
                f"Stopped: {e}. Partial progress may have been made -- "
                f"check the audit log at {self.audit.path} for what happened."
            )
        except Exception as e:
            log.exception("Agent run failed")
            self.audit.record("goal_error", error=f"{type(e).__name__}: {e}")
            return f"Agent run failed with an error: {e}. See the log for details."
