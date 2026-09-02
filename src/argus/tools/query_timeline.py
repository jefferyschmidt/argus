"""PRD.md §13 unit 26. ROADMAP.md Part III names query-over-history as
principle P2 -- "a primary mode, not a side effect" -- but no unit ever
specified it: the spine (Phase A) holds the data and nothing exposed it
to conversation. Wraps SpineStore.query() directly; no LLM call happens
in here (tier ALLOW -- it only reads)."""

import time
from collections import Counter
from datetime import datetime

from argus.tools.base import PermissionTier, Tool

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_RECENT_HANDFUL = 5


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _build_query_timeline(spine) -> Tool:
    def handler(args: dict) -> str:
        kinds = args.get("kinds") or None
        subject = args.get("subject")
        # Relative, not absolute (matches set_reminder's "minutes from
        # now" convention in reminders.py) -- the model doesn't have to
        # know the current epoch time, just how far back to look.
        now = time.time()
        since_hours_ago = args.get("since_hours_ago")
        until_hours_ago = args.get("until_hours_ago")
        since = now - since_hours_ago * 3600 if since_hours_ago is not None else None
        until = now - until_hours_ago * 3600 if until_hours_ago is not None else None
        # Bounded server-side regardless of what the model asks for -- a
        # broad query (no kinds/subject filter, a huge window) must not
        # be able to flood the model's own context.
        limit = max(1, min(int(args.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT))

        rows = spine.query(kinds=kinds, subject=subject, since=since, until=until, limit=limit)

        lines = []
        earliest = spine.earliest_ts()
        if since is not None and earliest is not None and since < earliest:
            # ROADMAP.md P1: honest about gaps rather than letting a zero-
            # or few-result answer read as "nothing happened" when really
            # the spine just doesn't go back that far.
            lines.append(f"Note: the record only goes back to {_fmt(earliest)} -- earlier than the requested window.")

        if not rows:
            lines.append("No matching observations.")
            return "\n".join(lines)

        counts = Counter(r.kind for r in rows)
        by_kind = ", ".join(f"{kind}: {n}" for kind, n in counts.most_common())
        lines.append(f"{len(rows)} observation(s) -- {by_kind}.")
        lines.append("Most recent:")
        for r in rows[:_RECENT_HANDFUL]:
            subj = f" ({r.subject})" if r.subject else ""
            lines.append(f"- {_fmt(r.ts)} {r.kind}{subj}")
        return "\n".join(lines)

    return Tool(
        name="query_timeline",
        description=(
            "Queries Argus's event history (mail, calendar, git activity, focus changes, tasks, etc.) "
            "for things that actually happened -- e.g. \"when did I last get mail from X?\". "
            "Returns a summary (counts by kind + the most recent few), not a raw dump."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kinds": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional filter, e.g. ['mail.received']. Omit to match every kind.",
                },
                "subject": {"type": "string", "description": "Optional filter, e.g. a sender address or window title."},
                "since_hours_ago": {"type": "number", "description": "Only observations from at most this many hours ago."},
                "until_hours_ago": {"type": "number", "description": "Only observations from at least this many hours ago."},
                "limit": {"type": "integer", "description": f"Max rows to consider, default {_DEFAULT_LIMIT}, capped at {_MAX_LIMIT}."},
            },
        },
        tier=PermissionTier.ALLOW,
        handler=handler,
    )
