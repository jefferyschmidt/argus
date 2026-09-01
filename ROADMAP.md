# Argus Roadmap

Living document, last updated 2026-08-30. Captures the architecture direction agreed on
after the "streamline conversational capabilities" session (persona/orchestrator rewrite,
OpenAI Realtime voice mode, tool bridge) and the follow-on planning discussion about
decoupling proactive behavior and tool-calling from the two voice loops, plus a brainstorm
of third-party MCP servers worth adopting toward a "do everything" assistant.

Build order is deliberately front-loaded with foundation work: everything in Phase 3+
becomes much cheaper once Phase 1 (ToolServer) and Phase 2 (ProactiveEngine) exist, since
new capabilities and new consumers (chat, realtime voice, proactive engine, whatever comes
next) all plug into the same two abstractions instead of each getting hand-wired.

## Phase 0 — Stabilize (prerequisite, not new work) — DONE

- Dependency repair confirmed sound: full suite green.
- Found and fixed a second, unrelated breakage: pytest had no `testpaths`, so it walked
  into the (gitignored but still on-disk) `Grunge/` directory and imported its frozen
  PyInstaller build's bundled torch tests, whose `python311.dll` conflicts with this
  project's Python 3.13 venv and poisoned the whole process. Fixed with
  `testpaths = ["tests"]` in `pyproject.toml`.
- Updated the two tests in `test_handle_streaming_tail.py` that had gone stale against
  the new `_should_use_tools()` classifier.
- Nothing below should be built on top of an unverified base.

## Phase 1 — ToolServer (shared tool layer) — SEAM LANDED, WIRING STILL OPEN

**Problem it fixes:** `Orchestrator` and `RealtimeVoiceLoop` each build their own
`build_default_registry()` today (`orchestrator.py` passes `router=self.router`,
`realtime.py` doesn't) — two independent registries, no shared task-approval or
cost-governor state between the chat path and the realtime voice path.

**Done:** `RealtimeVoiceLoop.__init__` now accepts an optional `tool_registry`, falling
back to building its own if none is given. Backward compatible, no behavior change for
existing callers.

**Still open:** nothing actually constructs one shared registry and hands it to both
consumers yet — `cli.py` doesn't wire this up, and doing that correctly also needs router
injection added to `Orchestrator`'s constructor (its `second_opinion`/`scan_document` tools
need the same cost governor the conversation uses). Deliberately deferred: today pipeline
mode and realtime mode are mutually exclusive per process, so there's no *live* bug from
the split, just a foundation gap. Better to design the construction-order change once,
carefully, now that Phase 2 has made the real sharing requirements concrete (see Phase 2's
dedicated-`Orchestrator`-per-loop choice below), than to have rushed it earlier.

- One `ToolServer` instance, owned by whichever `main()` entrypoint starts (`chat`/
  `voice`), holding the single registry + router + confirmer.
- `.list_tools()` / `.call(name, args, requested_by)` — same shape as today's
  `ToolRegistry.schemas()`/`.execute()`, now shared instead of duplicated.
- `Orchestrator` and `RealtimeVoiceLoop` take a `ToolServer` reference instead of each
  calling `build_default_registry()`.
- This shape already closely matches MCP's `list_tools`/`call_tool` — intentional, so
  wrapping it as a literal MCP server later (Phase 7) is cheap, not a rewrite.

## Phase 2 — ProactiveEngine (decoupled proactive subsystem) — CORE DONE

**Problem it fixes:** the 7 proactive workers (`email_watcher.py`, `context_awareness.py`,
`stuck_detection.py`, `research_digest.py`, `knowledge_watcher.py`, `routine_worker.py`,
`consolidation_worker.py`) were threads owned directly by `VoiceLoop`. `RealtimeVoiceLoop`
had none of them — confirmed orphaned: no proactive reminders, no email watching, no
context-awareness check-ins, no stuck detection, no research digest, no scheduled
routines, no memory consolidation in realtime mode.

**Done:** `proactive_engine.py`'s `ProactiveEngine` now constructs and starts all 7 (pure
code motion out of `VoiceLoop.__init__`, zero behavior change to the workers). Ended up
simpler than originally sketched: rather than a new `engine.announce(text, source=...)`
API every worker would need to call instead of its existing `speak_fn`/`interaction_lock`,
each worker keeps that exact contract unchanged. `RealtimeVoiceLoop` gained an `announce()`
method (injects a `conversation.item.create` + `response.create` since server-side
turn-taking means there's no local audio queue to just play into) plus a small
`_AnnounceLock` adapter that makes `announce()` satisfy the `acquire()`/`release()` shape
workers already expect — so zero worker classes needed to change to support a second voice
loop. `RealtimeVoiceLoop` also gained its own dedicated `Orchestrator` instance (the
workers' own LLM/memory needs; the realtime conversation itself never touches it).
Realtime mode now has all 7 proactive workers running. Tested (`test_proactive_engine.py`
+ additions to `test_realtime_voice.py`), full suite green.

**Also done:** quiet-mode and listening-paused/mute are now respected in realtime mode.
`_should_forward_mic_audio()` gates the mic-send loop (a real privacy gap before this —
the mic kept streaming to OpenAI regardless of what the console UI showed); the
`response.output_audio.delta` handler skips local playback in quiet mode while captions/
transcripts still publish, same contract as the pipeline loop.

**Also done:** direct expression requests ("show me you're happy") now trigger the face
in realtime mode too — matched deterministically via `orchestrator.py`'s
`_detect_requested_expression` against each user transcript, since this mode's system
prompt carries no `EXPRESSION:` marker protocol for the audio model to rely on. Text input
(console box, Telegram) also now reaches realtime mode — `submit_text_message()` injects
it as a real user conversation item, with a real retry loop (not best-effort like
`announce()`, since it's text the user actually sent) and a toast if delivery genuinely
fails. Push-to-talk deliberately left unwired: realtime mode's mic is already always-on
via server-side VAD, so a PTT control doesn't have a clear purpose there distinct from
just talking — a UX/documentation question, not a functional gap.

**Still open (confirmed orphaned in realtime mode, not yet addressed):**
- Reminder checking specifically is still inline as `VoiceLoop._reminder_checker_worker`
  rather than a class `ProactiveEngine` owns (lower priority — same functional gap, kept
  out of this pass to limit scope of one already-large change).
- Real amplitude-envelope mouth-sync (the face still falls back to generic idle motion
  during realtime-mode speech, unprompted emotional beats aside) — `VoiceLoop`'s pattern
  (compute the whole envelope up front, publish it with a known `duration_ms` before
  playback starts) doesn't map directly onto realtime audio, which arrives as many small
  deltas over time with no known total length in advance. Needs its own design.
- Watchers get `ToolServer` access for free once Phase 1's remaining work (a single
  shared registry/router, not just the `RealtimeVoiceLoop` injection seam landed so far)
  is finished.

## Phase 3 — First external MCP: Playwright — DONE

- Web/browser automation via structured accessibility-tree data instead of
  screenshot-and-guess — same "Set-of-Mark" principle as `list_ui_elements`
  (UI Automation, built for desktop), applied to the browser.
- Directly fixes the failure mode from the Yahoo Mail deletion task (20+ iterations, a
  malformed click, eventual failure) for any future web-based task without its own tool.
- First real "consume a third-party MCP server" case — validates that a bridge can
  register tools from an external MCP server into the same registry as in-process
  `Tool` objects.

**Done:** `mcp_bridge.py`'s `McpServerBridge` launches an MCP server as a subprocess
(stdio transport), runs its own asyncio event loop on a dedicated background thread for
the server's whole lifetime (a session needs to stay alive across many calls — restarting
a browser process per tool call would be both slow and wasteful), and bridges Argus's
synchronous `Tool.handler` calls into it. `build_default_registry()` wires in Playwright
MCP (`npx @playwright/mcp@latest --headless`) when `ENABLE_PLAYWRIGHT_MCP=true` (off by
default — launching it costs a few real seconds and a Node/browser process, not worth
paying at every startup); a failure to start is caught and logged, same "skip and warn"
spirit as the plugin loader, so it can never take down the rest of the registry.
Verified live end-to-end against the real server (24 tools discovered, a real
`browser_navigate` call actually navigated a headless browser). Found and fixed one real
bug in the process: entering the stdio/session async context managers via `__aenter__()`
without keeping a reference let them get garbage-collected the moment the connecting
coroutine returned, closing the connection before a single request could go out.
7 tests for the bridge itself (fake session, real threading/event-loop code path) + 3 for
the registry wiring. Full suite: 573 passed.

**Not yet done:** this is the generic bridge, reusable for any stdio MCP server — Phase 4's
Zapier/Home Assistant and Phase 5's GitHub/Figma should be able to reuse
`McpServerBridge` directly (different `command`/`args`, possibly a different transport
for ones that aren't stdio-based) rather than needing their own bridge code.

## Phase 4 — Jarvis capability expansion — WIRING DONE, NEEDS REAL CREDENTIALS

- **Zapier MCP** — governed OAuth access to 9,000+ apps. Highest-leverage single addition
  on the whole wishlist: the fallback for "there's probably a Zap for that" instead of
  hand-building a tool per app.
- **Home Assistant MCP** — smart home control (lights, thermostat, locks, scenes).
  Explicitly requested; genuinely new capability, nothing existing to replace.

**Done:** `McpServerBridge` (Phase 3) generalized to also speak streamable HTTP, not just
stdio — the transport both of these need, since they're remote hosted servers, not local
subprocesses. Same bridge code, no duplication. `build_default_registry()` wires both in
via `ZAPIER_MCP_URL`/`ZAPIER_MCP_API_KEY` and `HOME_ASSISTANT_MCP_URL`/
`HOME_ASSISTANT_MCP_TOKEN` (see `.env.example`) — both off unless a URL is actually
configured, same try/except "skip and warn" pattern as Playwright so a bad/unreachable
server can't take down the rest of the registry. The generalized HTTP transport itself was
verified live end-to-end against a local test MCP server (real tool call round-tripped
correctly) before trusting it — same standard as Phase 3's Playwright verification.

**Not yet done — needs the user's own accounts:** neither Zapier's nor Home Assistant's
*actual* endpoint has been connected to or exercised, since both require real,
account-specific credentials only the user has (Zapier's dashboard-generated MCP URL; a
Home Assistant instance with the MCP Server integration enabled plus a long-lived access
token). The wiring and transport are proven; the specific services are not. Whoever adds
real credentials to `.env` should do a first live check the same way Phase 3's Playwright
integration was checked — confirm `list_tools()` returns something sane before trusting a
real tool call.

## Phase 5 — Developer + creative capability — DONE (needs real credentials to go live)

- **GitHub MCP** — issues/PRs/remote repo management. Complements, doesn't replace, the
  existing local self-editing tools (`read_own_source`/`write_own_source`/
  `commit_own_changes`).
- **Figma MCP** — real design-file structure (layers, auto-layout, variants, tokens) for
  generating code against an actual design instead of a screenshot.
- **Stability AI / image-gen MCP** — generate/edit/upscale images from a prompt.

**Done:** GitHub's and Figma's MCP servers have fixed, documented endpoints (not
account-specific dashboard-generated URLs) — `https://api.githubcopilot.com/mcp/` and
`http://127.0.0.1:3845/mcp` respectively — so wiring needed only an enable flag (+ a
personal access token, for GitHub) rather than a URL to paste in. Stability AI
(`mcp-server-stability-ai`) is a **local stdio server, like Playwright** — verified live
end-to-end (13 real image tools discovered) once a real bug in the bridge was found and
fixed: `env=None` on `StdioServerParameters` does **not** inherit this process's
environment (the SDK merges `server.env or {}` onto its own minimal default set, not
`os.environ`), so a server needing its API key via an env var silently never received it
and the connection just hung until timeout with no error at all. `McpServerBridge` now
takes an explicit `env=` dict for the stdio path, tested. `build_default_registry()` wires
all three in via `ENABLE_GITHUB_MCP`/`GITHUB_MCP_TOKEN`, `ENABLE_FIGMA_MCP`, and
`ENABLE_STABILITY_MCP`/`STABILITY_AI_API_KEY` (see `.env.example`), same try/except
"skip and warn" pattern as the rest.

**Not yet verified live:** GitHub needs a real personal access token, Figma needs the
actual desktop app running with Dev Mode enabled — neither exists in this environment.
Stability AI's *transport* is proven (real connection, real tool list); actual image
generation needs a real (non-dummy) API key to try.

## Phase 6 — Music/audio capability (lowest priority, "fun") — SPOTIFY WIRING DONE

- **Spotify MCP** — natural-language playback control, search, playlists.
- **ShazamAPI MCP / ACRCloud** — song recognition ("what song is this"); ACRCloud is the
  more capable paid option (150M+ track database, hums-to-song matching). Not started.
- **live-coding-music-mcp** — generative music via Strudel.cc. Experimental/optional, not
  started. True vocal singing synthesis is a narrower space; Cartesia (already Argus's TTS
  provider) is the closer fit if that's ever pursued, not a dedicated MCP.

**Done:** Spotify (`@tbrgeek/spotify-mcp-server`) is another local stdio server. Verified
live end-to-end with **zero credentials configured** — it connects and lists tools fine,
exposing only `spotify_health_check`/`spotify_get_auth_status`/`spotify_setup_instructions`
until authenticated. No API key setting needed on Argus's side at all; wired in via just
`ENABLE_SPOTIFY_MCP`. Once enabled, Argus can walk the user through auth itself using the
server's own setup-instructions tool rather than needing separate documentation here.

## Phase 7 — Argus as an MCP server (longer-term, not blocking)

- Wrap `ToolServer` as an actual MCP server (stdio/HTTP transport) so other MCP-aware
  clients (Claude Desktop, etc.) could call Argus's own capabilities directly.
- Only worth doing once something *other* than Argus itself needs to call these tools —
  Phase 1's `ToolServer` shape makes this a cheap follow-on, not a rewrite, whenever that
  becomes true.

## Already resolved / explicitly decided, not open items

- Filesystem sandboxing removal — flagged, user explicitly confirmed intentional
  ("Argus is becoming an authorized user of my machine"), leave as-is.
- Tool-call auto-authorization (`_should_use_tools` gating `CONFIRM`-tier bypass) —
  flagged, user explicitly confirmed intentional ("If I've told it to do something,
  that's enough permission"), leave as-is.
- `Grunge/`, `grunge.zip`, `grungecode/` — gitignored; the son's separate project, not
  part of Argus's own history.
- `delete_core_memory` being `ALLOW`-tier vs. the codebase's `CONFIRM`+`high_risk` pattern
  for other irreversible deletes — noted, not raised as urgent given the security stance
  above; revisit only if it becomes an actual problem.

---

# Part II — From assistant to "brain of my personal life"

Added 2026-09-01, after a direct architecture review against the stated goal: Claude Cowork
+ Alexa + something new — email monitoring, home automation, appointment/calendar
management, call screening via a separate AI call assistant, and proactive insight the user
hasn't noticed yet. Part I (Phases 0–7) built the *tool* half well. This part rebuilds the
*perception* half, which is structurally wrong for that goal.

## Diagnosis — why it still feels like a chatbot with clunky signals

The seven proactive workers are each a closed loop: **poll one signal → judge alone with an
LLM → speak directly**. That shape has five consequences, and all of them are architectural,
not prompt-tuning problems:

1. **Information poverty.** `ContextAwarenessWorker` is handed a single window-title string
   and asked "is there something worth saying?" It has no calendar, no open email threads,
   no project state, no history of what the user is actually working on. With nothing to
   reason *from*, the only thing a model can generate is a greeting — which is exactly why
   the observed output is "I see you've opened Claude. Need any assistance?" No prompt
   rewrite fixes this; the context genuinely isn't there.
2. **No world model.** There is memory (episodic/semantic/core/knowledge-graph) — a
   searchable *past* — but no continuously-maintained model of the *present*: what's open,
   what's owed, what's coming, what's normal. You cannot notice a deviation without a
   baseline, so "proactive stuff it picks up on that I haven't noticed" is currently
   impossible by construction.
3. **No arbitration.** Every worker speaks whenever it wins `interaction_lock`. That lock is
   a *concurrency* primitive being used as an *attention* policy — different problems. Seven
   workers each politely honoring a 20-minute local cooldown still yields an interruption
   roughly every 3 minutes, with no notion of whether this one is more important than the
   last.
4. **Speech is the only output.** A worker that notices something can only say it out loud,
   right now, or drop it forever (`ContextAwarenessWorker._deliver` literally discards on
   lock contention). That binary — interrupt or vanish — is the direct cause of the "clunky"
   feel. Most real observations deserve neither.
5. **No daemon.** Everything lives inside the `argus voice` process. Close it and all
   monitoring stops. Always-on home automation, overnight email triage, and an external call
   screener querying Argus are all hard-blocked by this. A brain for a life cannot be a mode
   of a CLI.

Also non-durable: `_triaged_uids`, `_suppressed_titles`, and context history are in-process
only and reset every restart, so Argus re-learns the same things forever.

**What is already right and should be kept:** the shared `ToolRegistry` + permission tiers,
`McpServerBridge` (integration cost is now near-zero), `ProactiveEngine` as a seam, and the
memory subsystem. Part II keeps all of it and changes what sits *above* it.

## Target architecture

```
  SENSORS          dumb, cheap, no LLM. Emit normalized Observations only.
  (window focus, mail, calendar, HA state, call events, files, git, location)
        |
        v
  EVENT SPINE      append-only, durable, queryable (SQLite). One timeline of the
                   user's life. Everything downstream reads from here.
        |
        v
  WORLD MODEL      continuously projected state of the PRESENT:
                   threads (open loops) | entities | rhythms | focus | horizon
        |
        v
  SALIENCE ENGINE  single arbiter. urgency x relevance x timing, against a global
                   interruption budget. Replaces 7 independent "should I speak?" calls.
        |
        v
  ACTION LAYER     speak | queue for next pause | prep work | ambient surface |
                   phone push | act under standing authorization
        |
        v
  DAEMON (argusd)  owns all of the above + the tool registry.
                   Clients attach: voice loop, console, chat, Telegram,
                   phone, call screener, MCP.
```

The one-sentence version: **stop building workers that talk; build a system that perceives,
and make speech one of its several possible responses.**

## Phase A — Event spine (foundation, nothing smart yet)

Everything after this depends on it, and it is independently useful on day one (a real
queryable timeline).

- `argus/events/` — `Observation` record (source, kind, ts, subject, payload, dedupe key)
  and a SQLite-backed append-only store with retention.
- Convert each existing worker's *detection* half into a sensor that emits Observations and
  makes no judgment: window focus changes, new mail, calendar deltas, reminders due,
  routines due, research hits. Keep their current announce behavior temporarily so nothing
  regresses while B and C are built.
- Move `_triaged_uids` / `_suppressed_titles` / context history into durable storage —
  fixes the amnesia-on-restart bug directly.
- New sensors worth adding once the spine exists (cheap, high signal): git activity,
  active-file/project detection, idle/away detection, battery/power, network location.

## Phase B — World model (the piece that makes proactivity smart)

A persisted, continuously-updated projection over the event spine. Four parts:

- **Threads** — open loops with a close condition: unanswered mail, a commitment made aloud
  ("I'll look at that tomorrow"), a blocked task, a bill due, a broken credential (the live
  Yahoo `AUTHENTICATIONFAILED` is a textbook example Argus should be *tracking*, not
  re-discovering hourly).
- **Entities** — people/projects/places/devices with current *status*, not just facts. The
  knowledge graph holds facts; this holds state.
- **Rhythms** — learned baselines: work hours, focus vs. browsing apps, which senders
  actually matter, typical session length. Derived from the accumulated event log. This is
  the prerequisite for noticing anything unnoticed.
- **Focus & horizon** — what the user is doing now (with confidence) and the next N hours of
  obligations.

Exposed as one cheap `world_model.snapshot()` injectable into any prompt. This is what turns
"he opened Claude" into "he's 90 minutes into the voice-bot repo, has a 3pm call, and the
client email about that exact project has been open two days."

## Phase C — Salience engine + delivery channels (where the change is felt)

- One arbiter consuming Observations + world model. Scores urgency x relevance x timing.
  Deletes the seven independent judgment calls.
- **Global interruption budget** — a finite number of unprompted interruptions per hour,
  spent on the best available item. Structural prioritization instead of prompt-wording.
- **Deferral instead of drop** — the binary that causes the clunk. Most items become "tell
  him at the next natural pause" or "put it in the morning brief," not "say now or lose it."
- **Channel routing** — urgent: speak. useful: queue for next pause. informational: ambient
  console surface / digest. away-and-actionable: Telegram push.
- Retire `ContextAwarenessWorker`'s standalone generation path; window focus becomes one
  input among many rather than its own reason to talk.

## Phase D — `argusd` daemon + client split (unblocks the ambitious half)

- Long-lived process owning spine, world model, salience, actions, tool registry.
- Local API (HTTP/socket) + the existing Part I Phase 7 MCP wrapper as one client interface.
- Voice loops, console, chat, Telegram become thin clients that attach/detach freely.
- Directly unblocks: overnight/always-on monitoring, home automation reacting when no voice
  session is open, and the **call screener handoff** — the separate AI call assistant asks
  `argusd` "who is this, is Jeff expecting them, what's his calendar" over the API. That is
  an API question, not something a CLI voice loop can ever serve.
- Windows service/scheduled-task install path; the existing `restart.py` needs revisiting.

## Phase E — Actions beyond speech

- **Prep work** — draft the reply, stage the calendar hold, pre-pull the file; present it
  ready rather than announcing it. Highest-value shift toward "Cowork," and safe because it
  stops short of sending.
- **Standing authorizations** — durable, scoped, revocable grants ("archive newsletters
  automatically"), replacing per-call confirmation for repeated benign work. Builds on the
  existing tier system rather than replacing it.
- **Briefings** — morning/evening digests assembled from deferred items instead of
  scattered interruptions.

## Phase F — Integrations that only make sense after A–E

- Home automation with real context (Home Assistant MCP is already wired; it becomes useful
  once the world model can say *why* to act, not just how).
- Call screening loop: screener → `argusd` → world model → decision, with the outcome
  written back to the spine as an Observation.
- Location/presence, health/wearable, financial monitoring — all just new sensors on the
  spine at that point, which is the payoff of building A first.

## Phase G — Standing rules & preferences (user-programmable behavior)

Direct request: be able to say *"you don't need to prompt me any more when you see I have
Claude open"* or *"when I get an important email, change the office bulb to blue until I've
acknowledged it"* and have Argus remember and act on it — without going back to code.

Decided at the same time: **the always-on daemon with standing authorizations (Phase D + E)
is confirmed as the intended direction**, not an open question. Phase G depends on it.

### Why core memory can't do this

`CoreMemoryStore` is the closest existing mechanism and it is the wrong substrate. It stores
**prose**, injected into the prompt verbatim. That is right for facts and cannot work for
rules, because a rule must be:

- **evaluated deterministically** on every matching event — running an LLM over N rules per
  event is neither affordable nor reliable;
- **individually inspectable and revocable** — "what rules do I have?", "forget that one";
- **able to hold live state** — the "until I've acknowledged it" half has a lifecycle;
- **able to carry an authorization grant** — the bulb rule acts with no human in the loop.

A sentence in a prompt does none of those. Rules need structure.

### Three classes, deliberately distinguished

Conflating these produces a bad design, because they attach at different points:

1. **Suppression** — "stop doing X." Gates existing behavior. No action, no state.
2. **Preference** — "when you do X, do it this way." Modulates timing, channel, threshold, tone.
3. **Automation** — "when TRIGGER, do ACTION until CONDITION." Genuinely new behavior composed
   at runtime from existing tools. Stateful. The bulb example.

(1) and (2) are inputs to the salience engine. (3) emits into the action layer directly. One
store, one engine, two output types.

### What's genuinely new to build

**G-a. Rule representation.** A structured record, not free text:
`id · natural_language · source_utterance · kind · trigger · conditions · action · until ·
scope · authorization · provenance · hit_count · last_fired · revoked_at`.

**G-b. The compiler — author once, evaluate many.** The critical split. A frontier model
turns the spoken sentence into a structured rule **once**, at authoring time. Evaluation is
then cheap deterministic matching. Rule matching must never run through an LLM per event.

**G-c. Authoring confirmation, with a scope question.** "You don't need to prompt me about
Claude" is genuinely ambiguous — just Claude? window check-ins as a category? being chatty in
general? The compiler proposes a scope, asks exactly one clarifying question, then reads the
rule back. This reuses `CoreMemoryStore`'s existing propose/confirm precedent with a
structured payload instead of prose. It is also where the rule's authorization grant is
captured, so one confirmation does both jobs.

**G-d. Matcher.** Deterministic, indexed by event kind, fast path first. Fuzzy predicates
("important email") delegate to the existing triage call and cache the verdict rather than
being re-judged once per rule.

**G-e. Stateful rule instances.** The subtle part, and where Phase B earns its place. "Until
I've acknowledged it" requires a live instance with a lifecycle (armed → fired → active →
resolved), persisted so it survives a restart, and a definition of "acknowledged" that is a
real Observation. The clean answer: the email becomes an **open thread** in the world model,
the instance watches that thread, and the thread closing resolves the instance. Every
instance needs a safety timeout so nothing can run forever — including across a crash, which
needs a reconciliation pass on daemon startup (otherwise the bulb stays blue indefinitely).

**G-f. Effect reversal.** "Until" implies undoing. The bulb had a prior color that must be
snapshotted before the action and restored after. `undo_log.py` already has exactly the right
pattern (snapshot-before-write) but is file-specific and says so in its own docstring; this
generalizes it to any reversible tool action.

**G-g. Conflict resolution.** Two rules will eventually disagree ("never interrupt during
focus time" vs. "always tell me about anything from Julia"). Precedence by specificity, then
recency, with suppression winning ties by default. Critically, detect and surface the conflict
**at authoring time** — "that contradicts one you set last week, replace it?" — not silently
at runtime.

**G-h. Introspection and decay.** "What rules do you have?" / "Why did you just do that?" /
"Forget the bulb thing." Plus hygiene: rules that never fire get flagged, rules that fire
constantly get surfaced for review. Without this, accumulated invisible rules make Argus's
behavior inexplicable — the specific failure mode that kills systems like this.

### Staging — there is an early win here

- **G1 — Suppressions.** Needs only Phase A. Durable, revocable "stop doing X," replacing
  `ContextAwarenessWorker._suppressed_titles` (in-process today, lost every restart). Small,
  and it fixes the immediate annoyance long before the rest of the system exists.
- **G2 — Preferences.** Needs C. Timing, channel, threshold and tone modulation of salience.
- **G3 — Automations.** Needs B (threads, for `until`) and E (standing authorizations, to act
  unattended). The bulb example lands here.

### Why this matters strategically

This is the feature that makes Argus **user-programmable by speech**. Alexa has rigid
prebuilt routines; Cowork has no persistent personal state. A system where you say one
sentence and durably change the assistant's standing behavior — with introspection,
conflict-checking and revocation — is the "something nobody has seen yet" part of the goal.
It is only cheap to build *because* A–E put an event spine, a world model, and an
authorization system underneath it. Attempted today, without those, every rule would need
hand-coded evaluation points — which is exactly the "going back to code" this is meant to end.

## Sequencing note

A and B are unglamorous and must come first: C is where the experience visibly changes, but
a salience engine with nothing to reason over is just today's problem with more indirection.
D can be built in parallel with C if desired, since it is a process-model change rather than
a reasoning change.

**Security posture:** an always-on daemon holding standing authorizations is a materially
different exposure than a user-launched CLI. Raised and explicitly decided on 2026-09-01:
the user confirmed this is the right call and the intended direction. Not an open question;
build D and E accordingly.

---

# Part III — The Enterprise computer reframe, and the status surface

Added 2026-09-01. Two requests that turned out to be one: a richer vocabulary of
voice-controlled standing rules, modeled on the Starship Enterprise computer rather than
Jarvis or Alexa; and an adaptive dashboard showing live status for everything Argus tracks.
They are the same system viewed two ways, and the reframe changes what Phases C and G must
support.

## Why the reframe is substantive, not cosmetic

- **Jarvis** is a personality — a butler with taste, anticipating because he knows you.
- **Alexa** is a command parser — utterance to intent to action, no state, no continuity.
- **The Enterprise computer** is an **instrument**: an always-on, impersonal, absolutely
  reliable query layer over a continuously instrumented world, which holds standing orders,
  reports its own confidence honestly, and always has a visible status surface beside it.

That third thing is what Argus is actually trying to be, and it maps onto the Part II
architecture almost exactly: the instrumented world is the event spine, "it always knows" is
the world model, standing orders are Phase G, and the visible status surface is Phase H
below. The characteristic Trek interactions are not "do this task" — they are *query against
continuous monitoring*, *standing order with a condition*, *analysis over accumulated data*,
and *honest refusal when data is insufficient*.

## Design principles this adds

**P1 — Honest uncertainty.** "Insufficient data" is a first-class, correct answer. The
computer never bluffs. This is not a personality note; it is a mechanism: confidence must be
carried on world-model facts and surfaced when low. Directly addresses the live failure
already observed on 2026-08-31, where Argus reported a `delete_email` as successful when it
had not happened.

**P2 — Query over history is a primary mode, not a side effect.** "When did I last hear from
the Cosm people?" / "How many hours went into GridPick this month?" / "What's still open from
last week?" The event spine makes these answerable and nothing currently exposes them. Cheap
once Phase A exists, and one of the highest value-per-effort items on the whole roadmap.

**P3 — Level-of-detail is user-controlled.** "More detail." / "Just the headline." A live
conversational modifier as well as a stored preference (G2).

**P4 — The computer monitors itself.** Trek reports on its own systems constantly. Argus
should hold open threads on its own integrations: an expired credential, a dead MCP server,
API spend. The Yahoo `AUTHENTICATIONFAILED` observed on 2026-08-31 is exactly this case — it
should have opened one tracked thread, not re-thrown hourly into the log forever.

## Mechanism deltas — what Phases C and G must now support

These came out of the rule brainstorm and are not optional additions; several rule patterns
people actually want are impossible without them.

**C-delta 1 — Escalation ladders, not just channel routing.** Phase C as written routes an
item to one channel. Real rules want escalation over time: *"if I don't acknowledge a
reminder in 10 minutes, say it again; after 30, text me."* The salience engine needs a
per-item escalation policy with timed steps, not a single routing decision.

**C-delta 2 — The deferral queue must be visible.** Phase C introduces "hold for later,"
which is invisible without a surface to hold it on. See Phase H: the dashboard is where held
items live, and that is what makes aggressive deferral safe rather than lossy.

**G-delta 1 — Named modes (composite rules).** *"Focus mode."* / *"I'm heading out."* /
*"Wind down."* One phrase activating a bundle of rules at once — the direct analogue of "red
alert" reconfiguring many subsystems with one word. Highest value-per-utterance item in the
rule system. Requires rules to be groupable and collectively armable/disarmable.

**G-delta 2 — Self-monitoring rules.** Rules whose triggers are Argus's own health, per P4.
Needs Argus's internals emitting Observations onto the same spine as everything else, which
is a small Phase A addition with large payoff.

**G-delta 3 — Scoped delegated authority as a rule.** *"You can archive newsletters without
asking. Never delete anything from a person."* A standing authorization is itself a rule with
a positive and a negative clause, inspectable and revocable like any other. Unifies Phase E's
authorization grants with the Phase G rule store rather than keeping two parallel systems.

## Rule patterns worth designing against

Not an exhaustive wishlist — these are the distinct *shapes*, each of which implies a
mechanism. The full example catalog lives in the architecture artifact.

| Pattern | Example | Requires |
|---|---|---|
| Standing notification | "Tell me if anything comes from a .gov address" | A, G |
| Suppression | "Stop commenting on what window I have open" | A, G1 |
| Conditional environment | "Bulb blue until I acknowledge the email" | B, E, G3 |
| Escalation ladder | "Remind me again in 10, text me after 30" | C-delta 1 |
| Named mode | "Focus mode" | G-delta 1 |
| Digest & rhythm | "Friday, tell me what I said I'd do and didn't" | B (threads) |
| Query over history | "When did I last email them?" | A, P2 |
| Delegated authority | "Archive newsletters, never delete from a person" | E, G-delta 3 |
| Self-monitoring | "Tell me if any integration breaks" | P4, G-delta 2 |

The Friday one is worth calling out: *"tell me what I said I'd do this week and didn't"* is
impossible today and nearly free once threads exist, because a spoken commitment is just a
thread that never closed.

## Phase H — The status surface (adaptive dashboard)

**Depends on:** B primarily, C for prominence and the deferral queue.

**The core problem with today's UI:** `argus/ui/` is a pure **event stream** — `events.py`
publishes transient events and `index.html` reacts to them. There is no state endpoint at
all. Events tell you what just *happened*; a dashboard must show what *is*. That is the exact
same past-versus-present error diagnosed at the memory layer in Part II, repeated one layer
up. The fix is symmetrical: add a state projection, don't bolt widgets onto the firehose.

**Not hand-built widgets — a rendering of the world model.** This is what makes it adaptive
rather than merely configurable:

1. **Composition** — widgets exist because something is being tracked. Add a sensor, a widget
   becomes available; no thermostat, no thermostat widget. New capabilities surface
   automatically instead of needing UI work each time.
2. **Prominence** — the salience engine already ranks. Layout follows that ranking, so a
   quiet Tuesday genuinely looks different from a day with three escalating items. This is
   the adaptive part with teeth.
3. **Mode-aware** — "focus mode" collapses it to one thing; "heading out" surfaces a
   different set entirely. Falls out of G-delta 1 for free.

**Widget set to start:**

- **Open threads** — the core widget. Count plus top items, each acknowledgeable.
- **Important email** — count and senders, feeding the same thread list.
- **Next obligations** — the calendar horizon from the world model.
- **Home & devices** — thermostat setpoint, lights, locks; current vs. rule-driven state.
- **Active rules** — which standing orders exist, and which are *currently firing*. Serves
  G-h introspection ("why did you do that?") visually.
- **System health** — integrations, credentials, API spend (P4).
- **Held items** — the Phase C deferral queue, made visible per C-delta 2.
- **Current focus** — what you're on and for how long, from the world model.

**Two properties that make it a system rather than a readout:**

- **Every widget is voice-addressable, and voice is the query layer over it.** "More on that
  email" refers to the same object the widget shows. The dashboard and the voice interface
  are two projections of one world model, never two separate stores — that is precisely the
  LCARS relationship, and getting it wrong (a dashboard with its own state) would recreate
  the split-registry class of bug already fixed once in Part I.
- **Acknowledgment is an interaction.** Clicking "got it" on an email widget closes the
  thread, which resolves the bulb rule instance watching it, which restores the light. The
  full loop closes visually, and that single path exercises B, C, G3 and H together — making
  it the natural end-to-end integration test for the whole architecture.

**Build note:** this extends the existing console rather than replacing it. The realistic
first step is a `GET /api/state` returning a world-model snapshot plus incremental updates
over the existing event bus — the transport and the page already exist; what is missing is
something with actual state to render.

---

# Part IV — Accepted from the capability audit (2026-09-01)

A hundred candidate capabilities were cross-checked against Parts I–III: 63 covered, 29
needing an unplanned integration, 8 genuine architectural gaps. Of those gaps, the following
were **accepted for the roadmap now**. One was accepted as a future note. The rest were left
open pending a decision.

## Phase I — Long-horizon autonomous work (ACCEPTED)

**The gap:** the entire Part II architecture is *reactive* — sensor fires, salience judges,
action executes. There is no way for Argus to go and *do* something over minutes or hours:
"research this and report back", "draft the weekly security report", "work out why the build
broke". This is the most characteristic Enterprise-computer behavior missing, and it is what
unlocks most of the security day-job block.

- Task registry with durable status, queryable progress ("how's that coming?"), hard budgets,
  and cancellation.
- Bounded concurrency; a task is never allowed to run unbounded in tokens or wall-clock.
- Completion emits an Observation onto the spine so the salience engine — not the task —
  decides how and when to tell the user.
- Builds on the existing `agent/runner.py`, which Parts I–III never touched.
- **Depends on:** Phase A (to emit results onto the spine). Independent of B and C otherwise,
  so it can be built in parallel with B.

## Phase G4 — Rule induction (ACCEPTED)

**The gap:** every rule in Phase G is authored by the user. Nothing lets Argus notice a
pattern and *propose* one. Phase B's rhythms produce exactly the raw material and nothing
consumes it. This is the most direct answer to the original ask — "proactive stuff it picks
up on that I haven't even noticed" — and is cheap once B exists.

- Slow cadence (daily), over the spine plus the world model.
- Candidate patterns: repeated dismissals, repeated identical manual actions, consistent
  timing of when the user actually deals with a category.
- **Proposes only — never self-activates.** Goes through the same propose/confirm path as a
  user-authored rule (G-c), so an induced rule is inspectable and revocable identically.
- Hard rate limit on proposals, or it becomes the nagging it was meant to remove.
- **Depends on:** B (rhythms) and G1–G2 (a rule store to propose into).

## Phase E-compose — Producing documents (ACCEPTED)

**The gap:** the action layer is speak / hold / prep / ambient / push. There is no "produce
an artifact". A real share of the user's work is documents — client security summaries,
weekly reports, project briefs — and speaking a report aloud is the wrong output shape.

- New `compose` action: takes a spec plus world-model and spine queries, writes a document to
  `data/documents/`, emits an Observation, and lets salience announce that it is ready.
- Markdown and HTML output. **Never auto-sends anything.**
- Pairs directly with Phase I — most compose jobs are long-horizon tasks.

## Noted for the future, not scheduled — speaker identity and disclosure

**Deferred by decision, recorded so the schema stays ready for it.** Two related problems the
architecture currently ignores:

- **Identity** — Argus assumes a single user. With an always-on daemon in a shared house,
  another person speaking raises whose calendar, whose threads, whose authority applies.
- **Disclosure** — independent of identity: saying something private aloud when someone else
  is in the room is a real failure. Phase C decides *whether* to speak but has no concept of
  *who can hear*.

**Forward-compatibility requirement, to be honored now at zero behavioral cost:** the
`threads` table carries a `sensitivity` column (`'normal' | 'private'`) from the day it is
created, and the salience `Decision` record carries an optional `audience` field. Neither is
read by any code in Phases A–I. This costs nothing now and avoids a migration later.

## Left open, no decision taken

Honest counsel (telling the user unwelcome things grounded in observed data), simulation and
what-if, financial data sources plus an explicit "reads money, never moves it" policy line,
the day-job integrations (Sentinel / Netsparker / Monday / Entra ID / CVE feeds), degraded
offline operation, and backup/export/retention for the accumulated spine and world model.
All remain open items from the audit; none are scheduled.
