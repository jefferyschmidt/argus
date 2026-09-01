import logging
import time
from datetime import datetime

from argus.config import settings
from argus.proactive_none import is_none_reply
from argus.salience.scoring import Candidate, base_urgency_for
from argus.tools.registry import ToolRegistry

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

    def __init__(self, router, dispatcher):
        self.router = router
        self._dispatcher = dispatcher
        self._empty_registry = ToolRegistry()

    def run(self) -> None:
        while True:
            time.sleep(settings.research_digest_poll_seconds)
            if not settings.research_digest_enabled:
                continue
            try:
                self.check_now()
            except Exception:
                log.exception("Research digest check failed")

    def check_now(self) -> None:
        from argus.memory.research_topics import ResearchTopicStore
        from argus.memory.store import get_connection

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
        # it). Whether/when it's actually said is SalienceEngine's call now
        # (U-C4) -- and if Argus is mid-conversation when it decides to
        # speak, SalienceDispatcher/HeldQueue hold onto it rather than
        # this worker needing its own retry queue.
        candidate = Candidate(
            observation_id=None, kind="research.digest", subject=topic["topic"], text=text,
            base_urgency=base_urgency_for("research.digest"),
        )
        self._dispatcher.submit(candidate)
