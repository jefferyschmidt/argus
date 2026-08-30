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

## Phase 5 — Developer + creative capability — GITHUB + FIGMA WIRING DONE

- **GitHub MCP** — issues/PRs/remote repo management. Complements, doesn't replace, the
  existing local self-editing tools (`read_own_source`/`write_own_source`/
  `commit_own_changes`).
- **Figma MCP** — real design-file structure (layers, auto-layout, variants, tokens) for
  generating code against an actual design instead of a screenshot.
- **Stability AI / image-gen MCP** — generate/edit/upscale images from a prompt. Not
  started.

**Done:** unlike Zapier/Home Assistant, both GitHub's and Figma's MCP servers have fixed,
documented endpoints (not account-specific dashboard-generated URLs) —
`https://api.githubcopilot.com/mcp/` and `http://127.0.0.1:3845/mcp` respectively — so
wiring needed only an enable flag (+ a personal access token, for GitHub) rather than a
URL to paste in. `build_default_registry()` wires both in via `ENABLE_GITHUB_MCP`/
`GITHUB_MCP_TOKEN` and `ENABLE_FIGMA_MCP` (see `.env.example`), same try/except
"skip and warn" pattern as the rest. Figma's failure case is the *expected* common one
(the desktop app not running with Dev Mode's MCP server enabled), not just theoretical —
documented as such in the log message rather than reading as a real error.

**Not yet verified live:** same situation as Zapier/Home Assistant — GitHub needs a real
personal access token, Figma needs the actual desktop app running with Dev Mode enabled,
neither of which exists in this environment. The wiring reuses the exact bridge/transport
already proven live twice (Playwright over stdio, a local test server over HTTP), so
confidence is high, but neither specific service has been connected to for real.
Stability AI/image-gen not started at all.

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
