import logging
import time
from collections import deque

from argus.config import settings
from argus.ui import commands as ui_commands
from argus.ui import events as ui_events

log = logging.getLogger(__name__)

_GENERATION_PROMPT = """You're Argus, glancing at what the user's active window is to decide
whether a brief, genuinely welcome check-in is worth saying out loud right
now -- not running commentary, occasional real attention, the way a friend
working nearby might notice something and say one thing about it. Most of
the time there is nothing worth saying, and staying quiet is the right call.

Recent window history (oldest first): {recent}
Current situation: {situation}

If there's a natural, low-key, non-presumptuous thing to say -- a light
question or observation, ONE sentence, spoken conversational tone, no
assumptions about what they're actually doing beyond the window title
itself -- reply with ONLY that sentence. If there's nothing genuinely worth
interrupting for, reply with exactly: NONE"""


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
    asks the fast/cheap model whether there's something genuinely worth
    saying about it -- README roadmap-adjacent (proactive attention), not
    a listed item, added on direct request.

    Deliberately conservative by design, not just by prompt wording: a
    cooldown floor, a real judgment call handed to the model each time
    (with an explicit NONE escape hatch), and a per-title suppression list
    ("don't ask me about this") all bias toward silence. The goal is
    attention that reads as genuine, not surveillance."""

    def __init__(self, orchestrator, speak_fn, interaction_lock):
        self.orchestrator = orchestrator
        self._speak_fn = speak_fn
        self._interaction_lock = interaction_lock
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

        prompt_text = self._generate_prompt(title, duration_minutes, changed)
        if not prompt_text:
            return

        self._last_prompt_at = now
        self._deliver(prompt_text)

    def _generate_prompt(self, title: str, duration_minutes: float, changed: bool) -> str | None:
        from argus.llm.base import Message

        situation = (
            f'Just switched to "{title}".'
            if changed
            else f'Been on "{title}" for about {duration_minutes:.0f} minutes.'
        )
        instruction = _GENERATION_PROMPT.format(
            recent=", ".join(self._history) or "(none yet)", situation=situation
        )
        try:
            result = self.orchestrator.router.local.complete([Message(role="user", content=instruction)])
        except Exception:
            log.exception("Proactive prompt generation failed")
            return None

        text = result.text.strip()
        if not text or text.upper() == "NONE" or len(text) > 240:
            return None
        return text

    def _deliver(self, text: str) -> None:
        # Non-blocking: never stalls or barges into an in-progress
        # conversation. If Argus is mid-turn, this cycle's prompt is just
        # dropped rather than queued -- staying silent is always the safe
        # default here, and there'll be another scan in a minute.
        if not self._interaction_lock.acquire(blocking=False):
            return
        try:
            ui_events.publish({"type": "transcript", "role": "argus", "text": text})
            ui_events.publish({"type": "caption", "text": text})
            ui_events.publish({"type": "expression", "value": "curious"})
            self._speak_fn(text)
        finally:
            self._interaction_lock.release()
