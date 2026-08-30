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

**Still open (confirmed orphaned in realtime mode, not yet addressed):**
- Reminder checking specifically is still inline as `VoiceLoop._reminder_checker_worker`
  rather than a class `ProactiveEngine` owns (lower priority — same functional gap, kept
  out of this pass to limit scope of one already-large change).
- Telegram bridge and push-to-talk both feed a queue only
  `VoiceLoop._external_input_worker` drains — a message/button-press in realtime mode
  still goes nowhere.
- No `expression` events or real amplitude envelope published with `realtime.py`'s
  `speaking` state — the face falls back to generic idle motion instead of tracking
  realtime-mode speech.
- Watchers get `ToolServer` access for free once Phase 1's remaining work (a single
  shared registry/router, not just the `RealtimeVoiceLoop` injection seam landed so far)
  is finished.

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
