# Argus System Map

**This file is the source of truth for how Argus is *wired* — who produces each signal and who
consumes it.** It exists because the project's most expensive, most repeated bug is a subsystem
that is built and unit-tested but never driven in production (reminders lost, `fire()` never
called, `reap()` never on a timer, induction/escalation/prune orphaned). A unit test cannot
catch that — it calls the function directly. This map can.

**Maintenance is not optional and is partly enforced by tests** (`tests/test_system_integrity.py`):
- Update this file **in the same commit** as any change to a producer/consumer, a store, or an
  observation kind.
- At every gate, run `tests/test_system_integrity.py` and reconcile any diff between this map
  and reality. The mechanical claims below are checked there; the prose is on you.
- The one rule that would have prevented every orphan bug: **nothing is "done" until this map
  shows a real production producer *and* consumer for it.** "Has tests" is not "is wired."

Last audited: 2026-09-04 (post-u40-Part-2 gate). Orphan sweep (I3), connection-discipline (I1),
and observation-kind (I2) greps re-run clean; `voice/captions.py`'s three new functions
(`publish_spoken`/`publish_caption`/`publish_transcript`) all have real production callers in
both voice loops and in `salience/dispatch.py`/`salience/escalation.py`, not just tests. The
Part-1 escalation-caption finding is closed this round (see §4) -- no findings open.

---

## 1. Observation kinds — producer → consumer

Every kind in `spine/observation.py::KINDS`. A kind with no producer, or no consumer, is a bug
or dead vocabulary — flagged.

| Kind | Produced by | Consumed by | Status |
|---|---|---|---|
| `focus.changed` | WindowFocusSensor | rhythms, interruption_cost, context_awareness | ✅ |
| `focus.idle_started/ended` | WindowFocusSensor | interruption_cost | ✅ |
| `mail.received` | MailSensor, EmailWatcher | salience (via dispatcher), thread openers | ✅ |
| `mail.deleted` | **nothing** | thread close-conditions (email_reply), rhythms (`_ACTED_KINDS`) | ⚠️ **ORPHAN — consumed, never produced.** "Close the email thread when I delete it" never fires; deletions never count toward sender_importance. Needs a producer (the delete_email tool, or a mail-flag sensor). |
| `calendar.event_upcoming` | CalendarSensor | salience, interruption_cost (`_in_a_meeting`) | ✅ |
| `calendar.event_changed` | **nothing** | **nothing** | ⚠️ **DEAD vocabulary** — remove from KINDS or wire a producer+consumer. |
| `reminder.due` | ReminderSensor (timeline only) + ProactiveEngine tick reads `ReminderStore.list_due` directly | tick reminder consumer → salience | ✅ (§19 u37) |
| `routine.due` | RoutineSensor | tick routine consumer → salience | ✅ (§19 u37) |
| `git.commit`, `git.branch_stale` | GitActivitySensor | salience | ✅ |
| `argus.integration_failed` | argus_health `report_failure` | tick → `open_system_health` thread; scoring | ✅ (§20 u44c) |
| `argus.credential_failed` | EmailWatcher + MailSensor (`report_failure` at auth-fail limit) | tick → `open_system_health` thread | ✅ (§20 u44c) |
| `argus.credential_recovered` | EmailWatcher + MailSensor (`report_recovery`) | tick → closes system_health thread | ✅ (§20 u44c) |
| `argus.spend_recorded` | argus_health (spend.json read) | rule-matching tick (no explicit urgency score → `_UNKNOWN_KIND_URGENCY=0.30`, logs warning) | ⚠️ add entry to `scoring.py::_BASE_URGENCY` to silence warn and set intended urgency |
| `task.started/progress/finished/failed` | TaskRunner/worker | tick (task-close threads), salience | ✅ |
| `document.composed` | compose tool | salience | ✅ |
| `thread.opened`, `thread.closed` | ThreadStore.open/close | timeline, `thread_closed` predicate | ✅ |
| `thread.acknowledged` | ProactiveEngine.acknowledge_thread (voice + ui) | `user_acknowledged` predicate, rhythms | ✅ |
| `tool.auto_approved` | ToolRegistry (standing-auth grant) | audit trail (`argus timeline`) | ✅ |

**Action items from this audit:** `mail.deleted` producer (real), `calendar.event_changed`
decide (wire or delete). Both pre-existing, neither introduced by recent work.

---

## 2. Subsystems — what drives each

The columns that matter: **Producer** = what actually invokes it in `src/` (not a test).
"Built, tested, no producer" is the orphan signature.

| Subsystem | Entry point | Producer (prod caller) | Status |
|---|---|---|---|
| Spine sensors | `SpineEngine.start()` | `ProactiveEngine.start()` | ✅ |
| Salience decide | `SalienceDispatcher.submit()` | 7 workers + tick | ✅ |
| Interruption budget | `budget.consume()` | dispatcher | ✅ (atomicity = §19 u43b, pending) |
| Held queue | `held.add()` | dispatcher | ✅ |
| Thread reap (timer) | `threads.reap()` | `ProactiveEngine._tick_reap` | ✅ (§19 u37) |
| Rule matching + firing | `RuleInstanceStore.fire()` | `ProactiveEngine._tick_rule_firing` | ✅ (§19 u37) |
| Escalation | `escalation.schedule()` / `process_due()` | dispatcher (schedule) + tick (drain) | ✅ (§19 u37) |
| Induction | `InductionEngine.run_once()` | `ProactiveEngine._tick_induction` (daily) | ✅ (§19 u37) |
| Retention prune | `SpineStore.prune()` | `ProactiveEngine._tick_retention` (daily) | ✅ (§19 u37) |
| Reminders | `ReminderStore.list_due()` | tick reminder consumer, mark-notified-on-delivered | ✅ (§19 u37/u38) |
| Tasks (autonomous) | `TaskRunner` | Orchestrator (shares full registry) | ✅ (§19 u39) |
| Standing authorizations | `AuthorizationChecker` | `ToolRegistry.execute` step 2b + tick rule-firing | ✅ |
| Proactive layer itself | `ProactiveEngine.start()` (via shared `proactive_engine.start_proactive_engine()`) | `argus voice` (both loops) ✅ / `argus chat` ✅ via `cli.py::_start_chat_proactive` -- chat has no voice output, so its speak_fn (`cli.py::_chat_announce`) prints instead; construction failure there is caught so chat still works with no proactive layer, matching I10 | ✅ (§19 u40 Part 1) |
| `argus agent` CLI | `AgentRunner.run()` | `cli.py::agent()` | ✅ `cli.py::agent()` builds an `Orchestrator` and passes `orchestrator.tools` to `AgentRunner` (matching `tasks/worker.py` and `voice/realtime.py`); `AgentRunner.tool_registry` now has no default, so a caller that omits it fails at construction instead of falling through to a bare, diminished registry. I6 gap closed and mechanically enforced (`test_no_bare_registry_construction`). |

---

## 3. Stores & connection discipline (P1)

Every SQLite store opens through `argus/db.py::open_db(path, schema)` — sets `busy_timeout`,
serializes the one-time WAL transition per file (§19 u43a/43a-ii). **No raw `sqlite3.connect`
may exist outside `db.py`** (enforced by `test_system_integrity.py`).

Stores on `argus.db`: RuleStore, RuleInstanceStore, RhythmStore, ThreadStore, TaskStore,
InterruptionBudget, DecisionLog, EscalationScheduler, HeldQueue, ReminderStore/RoutineStore
(via `memory.store.get_connection`). Store on `spine.db`: SpineStore. Each owns its own
connection + `threading.Lock`; the lock serializes threads within one connection, `open_db`
handles cross-connection WAL contention.

---

## 4. Voice modes (transport differs, behavior should not)

| Behavior | pipeline (`voice/loop.py`) | realtime (`voice/realtime.py`) | Unified? |
|---|---|---|---|
| Transport | local STT/TTS + wake word | OpenAI realtime socket | — |
| Wake word | required ("Argus") | none (continuous) | **permanent, inherent difference** -- pipeline has no server-side turn detection to lean on instead; not a unification candidate |
| Tool registry | Orchestrator full | Orchestrator full (§16 u33) | ✅ |
| Time grounding | per-turn (`_dynamic_context`) | per-connect + refresh (§16 u34) | ✅ |
| Confirmations | `voice/confirm.py` -- records audio, then transcribes it (STT is a separate, blocking step) | `_ask_voice_confirmation` -- the model streams a transcript asynchronously as it's produced (no discrete record-then-transcribe step exists) | **permanent, inherent difference** -- the capture MECHANISM is tied to each transport's own audio model and cannot be merged without making one loop pretend to have a capability it doesn't. §19 u40 Part 2 deliberately left this separate (not force-merged); both still call the same `voice/acknowledgment.py`-style shared phrase/window logic where that part genuinely is common (see Acknowledgment row) |
| Reminder/proactive delivery | shared `ProactiveEngine` (via `start_proactive_engine`) | shared `ProactiveEngine` (via `start_proactive_engine`) | ✅ (§19 u37, u40 Part 1) |
| Caption/transcript publishing | `voice/captions.py::publish_spoken/publish_caption/publish_transcript` | same | ✅ (§19 u40 Part 2) -- one shared module both loops and `SalienceDispatcher._deliver()`/`EscalationScheduler.process_due()` call; each loop's own historical event shape (whether a caption event carries an explicit `role` key) preserved exactly per call site, since the UI treats a missing role as `"argus"` either way (`ui/static/index.html`) but a literal shape difference is still an observable API contract worth not disturbing silently |
| Held-item handling | `SalienceDispatcher.submit()`'s `held.add(...)` fallback | same | ✅ already centralized (pre-dates §19 u40) -- neither loop ever had its own copy of this; nothing to extract |
| Acknowledgment | `_process_utterance` calls `voice/acknowledgment.py::maybe_acknowledge_spoken_thread`, with its own additional "skip while a confirmation is active" guard (kept, deliberately redundant -- see its own comment) | `_receive`'s transcript path calls the same function, structurally unreachable while a confirmation is active | ✅ (§19 u40 Part 2) -- the "skip while a confirmation is pending" rule now also lives inside `maybe_acknowledge_spoken_thread` itself, not just at each call site, so a future third caller can't forget it; both loops' own call-site guards are left in place as intentional defense in depth, not because they're still load-bearing on their own |
| Escalation-follow-up captioning | ✅ captioned -- `EscalationScheduler.process_due()` now calls `publish_spoken()` before `deliver_fn`, closing the gap found at the §19 u41/u40-Part-1 pipeline-harness gate (`tests/test_pipeline_e2e.py::test_escalation_caption_gap_is_closed`, verified failing pre-fix via a live mutation check) | ✅ captioned (was already, via `announce()`'s own unconditional publish) | ✅ (§19 u40 Part 2) |
| **Vision (screenshots / camera)** | ✅ Claude sees the image (`_tool_result_content` renders image bytes as a real image block) | ⚠️ **BLIND (regression)** -- `_run_pending_tools` discards image bytes, replacing them with the text "…returned an image"; the OpenAI realtime model never sees pixels. Worked in pipeline before realtime existed; realtime silently stubbed it (I12). Fix specced §21 u47 (bridge screenshots through Claude vision) | ⚠️ **realtime regression, being fixed §21 u47** |
| **Web search** | ✅ Anthropic server-side `web_search` bolted on in `complete_with_tools` | ⚠️ **none** -- realtime's registry has no search tool and the server-side one is Claude-only. Fix specced §21 u46 (registry `web_search` tool bridging to Claude) | ⚠️ **realtime gap, being fixed §21 u46** |

**Default:** `config.py::resolved_voice_mode()` -- realtime when `VOICE_MODE` is unset and
`openai_api_key` is present, pipeline otherwise; an explicitly-set `VOICE_MODE` (env/.env/kwarg,
checked via `model_fields_set`) is never overridden. `cli.py::voice()` calls it. (§19 u40 Part 1)

**Proactive layer at both entry points (§19 u40 Part 1):** `argus voice` (either loop) and
`argus chat` all start `ProactiveEngine` through the one shared `proactive_engine.start_proactive_engine()`
helper -- chat's speak_fn (`cli.py::_chat_announce`) has no voice output, so a proactive item prints
as text; a construction failure there is caught (`cli.py::_start_chat_proactive`) so chat still
works with none of it, matching I10. Voice loops' own construction is unchanged (still fails loudly
on construction error, same as before this item) -- only the construct-then-start pair itself moved
into one place.

**Shared-behavior extraction (§19 u40 Part 2):** caption/transcript publishing, held-item
handling, and acknowledgment are now genuinely ONE implementation each, called by both loops --
see the table rows above for exactly what moved and why each one is safe (no observable-shape or
timing change beyond the one authorized fix). Confirmation capture and the wake word were
deliberately left separate: PRD's own instruction was not to force-merge genuinely
transport-specific behavior, and both are exactly that -- the confirmation MECHANISM differs
(pipeline blocks on a record-then-transcribe call; realtime reacts to an async transcript event
already arriving off the socket it's always reading), and the wake word has no realtime analog at
all (server-side turn detection replaces it entirely, not "the same thing done differently").
Closed as part of this: the escalation-caption gap found in Part 1 (see the table row above) --
`EscalationScheduler.process_due()` now calls the same `publish_spoken()` `SalienceDispatcher.
_deliver()` uses, instead of bypassing captioning entirely.

**End-to-end regression coverage (§19 u41, extended to pipeline at §19 u40 Part 1):**
`tests/test_realtime_e2e.py` drives the real `RealtimeVoiceLoop` (`_receive`, `_run_pending_tools`,
`_ask_voice_confirmation`, `announce`, `submit_text_message`, `_create_response_or_defer`) through a
fake websocket; `tests/test_pipeline_e2e.py` drives the real `VoiceLoop` (`_process_utterance`,
`_speak_with_barge_in`, `_watch_for_barge_in`, `_barge_in_session`, `_resume_after_interruption`,
`_speak_and_open_mic`, the `voice/confirm.py` confirmer) through a fake mic/wake-word stream
(`FakeInputStream`) plus faked STT/TTS (`FakeTranscriber`/`FakeSpeaker`) and a faked
`record_followup` -- the only fakes in either suite are these transport/hardware edges, never the
loops' own logic. Both suites cover the same 8 behaviors (barge-in cancel, false-barge-in resume,
caption-matches-delivery, spoken CONFIRM-tier approval, typed input mid-turn, a tool-call
round-trip, escalation-driven delivery, and reminder delivery with mark-notified-after-delivery),
each noting where pipeline's shape genuinely differs (no response.create/cancel protocol, no
persisted conversation-item history for a u24a-style leak to guard against, typed input queues
behind a real lock instead of cancelling an in-flight response) rather than forcing a false
equivalence. The escalation-caption divergence above was found by running pipeline's version of the
escalation test with the SAME assertions as realtime's -- it failed, which is what surfaced the gap.
The escalation and reminder tests are the acceptance-critical pair in both suites: proven to fail
against the pre-u37 wiring (escalation via toggling `dispatcher.escalation` back to its pre-u37
`None` default; reminders via `_tick_reminders` simply not existing/being called -- verified with a
live mutation check, temporarily no-op'ing it, for both suites) and pass against the current one.
