import logging
import time
from datetime import datetime

from argus.config import settings
from argus.proactive_none import is_none_reply
from argus.tools.registry import ToolRegistry
from argus.ui import events as ui_events

log = logging.getLogger(__name__)

_DIGEST_INSTRUCTION = """You're checking a topic the user asked you to keep an eye on: "{topic}"

{prior_context}

Search the web for genuinely new developments since then -- not a general
summary of the topic, specifically what's changed or is newsworthy right
now. Most checks should find nothing worth a proactive interruption for;
staying quiet is the right call unless there's something real.

If there's something genuinely worth telling the user unprompted, reply
with a short spoken-style digest (2-4 sentences). If there's nothing
genuinely new or noteworthy, reply with exactly: NONE"""

_NO_PRIOR_CONTEXT = "You haven't told them anything about this yet -- this is the first check."


class ResearchDigestWorker:
    """Proactive research digests (README dream/stretch item): periodically
    web-searches each tracked topic (argus/tools/research_topics.py) and
    surfaces a digest only when something's genuinely new, using the same
    NONE-escape-hatch pattern as ContextAwarenessWorker so staying quiet is
    the default outcome, not "give the user something every cycle."

    Uses router.complete_with_tools directly with an EMPTY tool registry
    (so the model only gets the web_search tool the Anthropic client
    always appends -- see anthropic_client.py's _WEB_SEARCH_TOOL) rather
    than the orchestrator's real tool registry. Deliberate: this runs
    unattended, and the real registry includes CONFIRM-tier tools
    (send_email, write_file, ...) that would pop an unprompted
    confirmation card if the model ever decided a "digest check" warranted
    one -- there's no legitimate reason a research digest needs anything
    beyond search."""

    def __init__(self, router, speak_fn, interaction_lock):
        self.router = router
        self._speak_fn = speak_fn
        self._interaction_lock = interaction_lock
        self._empty_registry = ToolRegistry()
        # (topic, text) findings already recorded as told but not actually
        # announced yet, because Argus was mid-conversation at the time.
        # Retried at the top of every poll -- see check_now.
        self._pending_delivery: list[tuple[str, str]] = []

    def run(self) -> None:
        while True:
            time.sleep(settings.research_digest_poll_seconds)
            if not settings.research_digest_enabled:
                continue
            try:
                self.check_now()
            except Exception:
                log.exception("Research digest check failed")

    def _flush_pending_delivery(self) -> None:
        if not self._pending_delivery:
            return
        self._pending_delivery = [
            (topic, text) for topic, text in self._pending_delivery if not self._deliver(topic, text)
        ]

    def check_now(self) -> None:
        from argus.memory.research_topics import ResearchTopicStore
        from argus.memory.store import get_connection

        # Findings we couldn't announce last time go first -- record_check
        # below has already stored them as the topic's last_digest, so
        # without this they'd be treated as already-told and never spoken.
        self._flush_pending_delivery()

        conn = get_connection()
        try:
            topics = ResearchTopicStore(conn).list_enabled()
        finally:
            conn.close()

        for topic in topics:
            try:
                self._check_topic(topic)
            except Exception:
                log.exception("Research digest check failed for topic #%s", topic["id"])

    def _check_topic(self, topic) -> None:
        from argus.llm.base import Tier
        from argus.memory.research_topics import ResearchTopicStore
        from argus.memory.store import get_connection

        prior_context = (
            f'Last thing you told them: "{topic["last_digest"]}"' if topic["last_digest"] else _NO_PRIOR_CONTEXT
        )
        prompt = _DIGEST_INSTRUCTION.format(topic=topic["topic"], prior_context=prior_context)

        result = self.router.complete_with_tools(
            prompt, system="", tool_registry=self._empty_registry, force_tier=Tier.ADVANCED
        )
        text = result.text.strip()
        is_none = is_none_reply(text)

        conn = get_connection()
        try:
            ResearchTopicStore(conn).record_check(topic["id"], datetime.now(), None if is_none else text)
        finally:
            conn.close()

        if is_none:
            return
        # record_check above already stored this as the topic's last_digest
        # (so the next check treats it as prior context and won't re-report
        # it) -- if we couldn't announce it right now because Argus was
        # mid-conversation, holding it for the next poll is the difference
        # between "told a bit late" and "silently never told at all".
        if not self._deliver(topic["topic"], text):
            self._pending_delivery.append((topic["topic"], text))

    def _deliver(self, topic: str, text: str) -> bool:
        """Returns whether it actually got announced -- False means Argus
        was mid-conversation and the caller should hold onto this finding
        and retry rather than treating it as delivered."""
        if not self._interaction_lock.acquire(blocking=False):
            return False
        try:
            ui_events.publish({"type": "transcript", "role": "argus", "text": text})
            ui_events.publish({"type": "caption", "text": text})
            self._speak_fn(text)
        finally:
            self._interaction_lock.release()
        return True
