import io
import logging
import time
from collections import deque

from argus.config import settings
from argus.llm.base import Tier
from argus.proactive_none import is_none_reply
from argus.ui import events as ui_events

log = logging.getLogger(__name__)

_ASSESSMENT_PROMPT = """You're glancing at the user's screen because they've been in the same
window for a while without switching away -- possibly a sign they're stuck
on something (an error, a confusing UI state, a blocked workflow), possibly
just normal focused work (reading, writing, watching something). Look at
the screenshot.

If there's a clear, visible sign of being stuck -- an error message, a
stack trace, a frozen/blocked state, repeated failed attempts visible on
screen -- and a brief, genuinely useful thing to say would help, reply with
ONLY that one sentence (spoken tone, no assumptions beyond what's visibly
on screen). Most of the time nothing like that is visible, and staying
quiet is correct -- reply with exactly: NONE"""


def _active_window_title() -> str | None:
    try:
        import pygetwindow as gw

        win = gw.getActiveWindow()
        title = win.title.strip() if win and win.title else ""
        return title or None
    except Exception:
        return None


def _screenshot_bytes() -> bytes | None:
    try:
        import pyautogui

        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        log.exception("Screenshot capture failed during stuck-detection scan")
        return None


class StuckDetectionWorker:
    """Ambient stuck-detection (README dream/stretch item): with desktop
    visibility already in place (take_screenshot, proactive context
    awareness), notice when the user's been stuck on the same screen for a
    while and offer help unprompted, rather than only ever reacting to a
    direct ask.

    Deliberately its own worker rather than folded into
    ContextAwarenessWorker: this needs a much shorter fuse (minutes, not
    hours -- "stuck" is a short-timescale signal) and looks at actual
    screen content via a real screenshot + vision call, not just the
    window title, which is a meaningfully more expensive check (frontier
    vision, not the cheap local tier) that only makes sense to run once
    the same-window duration alone already looks suspicious. Offers at
    most once per continuous stretch in the same window -- switching away
    and back resets it, same as a fresh start."""

    def __init__(self, router, speak_fn, interaction_lock):
        self.router = router
        self._speak_fn = speak_fn
        self._interaction_lock = interaction_lock
        self._current_title: str | None = None
        self._current_since = time.monotonic()
        self._offered_for_current_window = False
        self._history: deque = deque(maxlen=3)

    def run(self) -> None:
        while True:
            time.sleep(settings.stuck_detection_scan_seconds)
            if not settings.stuck_detection_enabled:
                continue
            try:
                self._scan_once()
            except Exception:
                log.exception("Stuck-detection scan failed")

    def _scan_once(self) -> None:
        title = _active_window_title()
        if not title:
            return

        if title != self._current_title:
            self._current_title = title
            self._current_since = time.monotonic()
            self._offered_for_current_window = False
            return  # just switched -- nothing to assess yet

        if self._offered_for_current_window:
            return

        duration_minutes = (time.monotonic() - self._current_since) / 60
        if duration_minutes < settings.stuck_detection_idle_minutes:
            return

        self._offered_for_current_window = True  # at most one offer per stretch, pass or fail
        self._assess_and_maybe_offer(title)

    def _assess_and_maybe_offer(self, title: str) -> None:
        image_bytes = _screenshot_bytes()
        if not image_bytes:
            return
        try:
            result = self.router.complete_with_image(
                image_bytes, _ASSESSMENT_PROMPT, tier=Tier.FAST, media_type="image/png"
            )
        except Exception:
            log.exception("Stuck-detection vision call failed")
            return

        text = result.text.strip()
        if is_none_reply(text) or len(text) > 240:
            return
        self._deliver(text)

    def _deliver(self, text: str) -> None:
        if not self._interaction_lock.acquire(blocking=False):
            return
        try:
            ui_events.publish({"type": "transcript", "role": "argus", "text": text})
            ui_events.publish({"type": "caption", "text": text})
            ui_events.publish({"type": "expression", "value": "curious"})
            self._speak_fn(text)
        finally:
            self._interaction_lock.release()
