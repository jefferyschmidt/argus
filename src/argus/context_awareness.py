import logging
import time
from collections import deque

from argus.config import settings
from argus.salience.scoring import Candidate, base_urgency_for
from argus.ui import commands as ui_commands
from argus.ui import events as ui_events

log = logging.getLogger(__name__)


def _active_window_title() -> str | None:
    try:
        import pygetwindow as gw

        win = gw.getActiveWindow()
        title = win.title.strip() if win and win.title else ""
        return title or None
    except Exception:
        return None


class ContextAwarenessWorker:
    """Periodically checks the focused window and, when the context has
    meaningfully changed or the user's been in the same one a long time,
    submits it to salience as a candidate -- README roadmap-adjacent
    (proactive attention), not a listed item, added on direct request.

    U-C4 (PRD §5/§7 build order): this used to also ask the local model
    "is this worth saying?" (with a NONE escape hatch) and speak the
    result directly -- its own independent judgment, one of the seven
    §5 was written to replace. That call is gone. Detection stays here
    (this is still the thing that notices a window change or a long
    stretch in one window); SalienceEngine now makes the interrupt/hold/
    ambient call, and base_urgency_for("focus.changed") is deliberately
    low (0.05, Appendix A.2) -- window focus is one input among many,
    not its own reason to talk, so a bare focus change speaks only when
    a rule boosts it or an LLM tie-break says so, not by default.

    A cooldown floor and a per-title suppression list ("don't ask me
    about this") still bias toward silence upstream of salience."""

    def __init__(self, dispatcher):
        self._dispatcher = dispatcher
        self._history: deque = deque(maxlen=5)
        self._current_title: str | None = None
        self._current_since = time.monotonic()
        self._last_prompt_at = 0.0
        self._suppressed_titles: set[str] = set()

    def suppress_current(self) -> None:
        """"Don't ask me about this" -- mutes proactive prompts for
        whatever window is active right now, for the rest of this
        process's lifetime. Session-scoped, not persisted -- a lightweight
        opt-out, not a full per-project settings system."""
        if self._current_title:
            self._suppressed_titles.add(self._current_title)

    def run(self) -> None:
        while True:
            time.sleep(settings.proactive_context_scan_seconds)
            if not ui_commands.is_proactive_context_enabled():
                continue
            try:
                self._scan_once()
            except Exception:
                log.exception("Context awareness scan failed")

    def _scan_once(self) -> None:
        title = _active_window_title()
        if not title:
            return

        now = time.monotonic()
        changed = title != self._current_title
        if changed:
            if self._current_title is not None:
                self._history.append(self._current_title)
            self._current_title = title
            self._current_since = now

        if title in self._suppressed_titles:
            return

        cooldown_minutes = (now - self._last_prompt_at) / 60
        if cooldown_minutes < settings.proactive_context_cooldown_minutes:
            return

        duration_minutes = (now - self._current_since) / 60
        worth_considering = changed or duration_minutes >= settings.proactive_context_idle_threshold_minutes
        if not worth_considering:
            return

        self._last_prompt_at = now
        self._submit(title, duration_minutes, changed)

    def _submit(self, title: str, duration_minutes: float, changed: bool) -> None:
        text = (
            f'Just switched to "{title}".' if changed
            else f'Been on "{title}" for about {duration_minutes:.0f} minutes.'
        )
        candidate = Candidate(
            observation_id=None, kind="focus.changed", subject=title, text=text,
            base_urgency=base_urgency_for("focus.changed"),
        )
        ui_events.publish({"type": "expression", "value": "curious"})
        self._dispatcher.submit(candidate)
