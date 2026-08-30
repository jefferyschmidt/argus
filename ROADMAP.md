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

## Phase 0 — Stabilize (prerequisite, not new work)

- Confirm the dependency repair (protobuf/onnxruntime/chromadb/silero-vad) is actually
  sound: run the full test suite.
- Update/replace tests invalidated by the persona/orchestrator rewrite. Three new test
  files already exist (`test_chat_system.py`, `test_conversation_lane.py`,
  `test_realtime_voice.py`) — audit coverage against what actually changed.
- Nothing below should be built on top of an unverified base.

## Phase 1 — ToolServer (shared tool layer)

**Problem it fixes:** `Orchestrator` and `RealtimeVoiceLoop` each build their own
`build_default_registry()` today (`orchestrator.py` passes `router=self.router`,
`realtime.py` doesn't) — two independent registries, no shared task-approval or
cost-governor state between the chat path and the realtime voice path.

- One `ToolServer` instance, owned by whichever `main()` entrypoint starts (`chat`/
  `voice`), holding the single registry + router + confirmer.
- `.list_tools()` / `.call(name, args, requested_by)` — same shape as today's
  `ToolRegistry.schemas()`/`.execute()`, now shared instead of duplicated.
- `Orchestrator` and `RealtimeVoiceLoop` take a `ToolServer` reference instead of each
  calling `build_default_registry()`.
- This shape already closely matches MCP's `list_tools`/`call_tool` — intentional, so
  wrapping it as a literal MCP server later (Phase 7) is cheap, not a rewrite.

## Phase 2 — ProactiveEngine (decoupled proactive subsystem)

**Problem it fixes:** the 8 proactive workers (`email_watcher.py`, `context_awareness.py`,
`stuck_detection.py`, `research_digest.py`, `knowledge_watcher.py`, `routine_worker.py`,
`consolidation_worker.py`, plus the reminder checker) are threads owned directly by
`VoiceLoop`. `RealtimeVoiceLoop` has none of them — confirmed orphaned: no proactive
reminders, no email watching, no context-awareness check-ins, no stuck detection, no
research digest, no scheduled routines, no memory consolidation in realtime mode. Also
orphaned in realtime mode specifically: the Telegram bridge and push-to-talk (both feed a
queue only `VoiceLoop._external_input_worker` drains), quiet-mode/listening-paused (mic
keeps streaming regardless), facial expressions, and real mouth-sync (visemes/envelope).

- `ProactiveEngine` runs each watcher's existing poll loop, unchanged logic, no longer
  thread-owned by `VoiceLoop`.
- Each watcher calls `engine.announce(text, source=...)` instead of reaching into
  `self._speak_fn`/`self._interaction_lock` directly.
- `AnnouncementSink` interface (`.try_speak(text) -> bool`, non-blocking, same semantics
  as today's `_pending_delivery` retry pattern) implemented by both voice loops:
  - `VoiceLoop`: synthesize + play if the interaction lock is free, else queue for retry
    (same as today).
  - `RealtimeVoiceLoop`: inject a `conversation.item.create` + `response.create` when
    nothing's actively happening (server-side turn-taking means this can't be a direct
    audio interrupt the way pipeline mode's is).
- While in here: wire quiet-mode/listening-paused checks into `realtime.py`'s mic loop,
  publish `expression` events and a real amplitude envelope with `realtime.py`'s
  `speaking` state, and give Telegram/PTT a consumer path in realtime mode (or explicitly
  disable those controls when `VOICE_MODE=realtime` so they don't silently no-op).
- Watchers get `ToolServer` access for free once Phase 1 exists (several already call
  tools directly, e.g. email).

## Phase 3 — First external MCP: Playwright

- Web/browser automation via structured accessibility-tree data instead of
  screenshot-and-guess — same "Set-of-Mark" principle as `list_ui_elements`
  (UI Automation, built for desktop), applied to the browser.
- Directly fixes the failure mode from the Yahoo Mail deletion task (20+ iterations, a
  malformed click, eventual failure) for any future web-based task without its own tool.
- First real "consume a third-party MCP server" case — validates that `ToolServer` can
  register tools from an external MCP server, not just in-process `Tool` objects.

## Phase 4 — Jarvis capability expansion

- **Zapier MCP** — governed OAuth access to 9,000+ apps. Highest-leverage single addition
  on the whole wishlist: the fallback for "there's probably a Zap for that" instead of
  hand-building a tool per app.
- **Home Assistant MCP** — smart home control (lights, thermostat, locks, scenes).
  Explicitly requested; genuinely new capability, nothing existing to replace.

## Phase 5 — Developer + creative capability

- **GitHub MCP** — issues/PRs/remote repo management. Complements, doesn't replace, the
  existing local self-editing tools (`read_own_source`/`write_own_source`/
  `commit_own_changes`).
- **Figma MCP** — real design-file structure (layers, auto-layout, variants, tokens) for
  generating code against an actual design instead of a screenshot.
- **Stability AI / image-gen MCP** — generate/edit/upscale images from a prompt.

## Phase 6 — Music/audio capability (lowest priority, "fun")

- **Spotify MCP** — natural-language playback control, search, playlists.
- **ShazamAPI MCP / ACRCloud** — song recognition ("what song is this"); ACRCloud is the
  more capable paid option (150M+ track database, hums-to-song matching).
- **live-coding-music-mcp** — generative music via Strudel.cc. Experimental/optional.
  True vocal singing synthesis is a narrower space; Cartesia (already Argus's TTS
  provider) is the closer fit if that's ever pursued, not a dedicated MCP.

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
