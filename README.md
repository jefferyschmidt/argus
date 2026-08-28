# Argus

A personal, local-first AI assistant. Runs on this machine, routes between a
local Ollama model and Anthropic's API depending on task difficulty, and
keeps a layered memory (core / semantic / episodic) so it doesn't forget
things that matter.

Code lives in a private repo so it can be tracked and backed up even though
it never runs anywhere but locally.

## Status

Phase 1: core orchestration loop + memory, text-only chat. See the roadmap
below for what's next.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY, confirm OLLAMA_LOCAL_MODEL is pulled
```

Requires [Ollama](https://ollama.com) installed and running locally, with a
small model pulled for the local tier (CPU-only hardware, so keep it small):

```bash
ollama pull llama3.2:3b
```

## Usage

```bash
argus chat            # interactive chat -- also opens the visual console
argus voice            # wake-word voice mode -- also opens the visual console
argus agent "<goal>"   # autonomous mode: figure out what needs doing and do it
argus memory review   # confirm/reject agent-proposed core memories
```

`chat` and `voice` both auto-launch **Argus Console** (needs `pip install -e ".[ui]"`)
at http://127.0.0.1:8765 -- a live view of voice state, transcript, tool
calls (with real generated images), memory, and routing/spend.

## Architecture

- `argus.llm.router.ModelRouter` -- classifies each request and routes to
  Ollama (local, CPU, trivial stuff) or Anthropic (fast/advanced tiers,
  real reasoning). Enforces a daily spend cap.
- `argus.memory.manager.MemoryManager` -- three memory layers:
  - **core**: high-salience facts, always injected verbatim, agent-proposed
    but user-confirmed before they become "always true"
  - **semantic**: Chroma vector store, "have we discussed this before"
  - **episodic**: raw SQLite log, recency-ordered conversation history
- `argus.orchestrator.Orchestrator` -- ties routing + memory into one
  `handle(text) -> reply` call.

## Roadmap

1. ~~Core loop (text-only)~~
2. ~~Tools + permission tiers~~ (allow/confirm/deny; web search, sandboxed
   filesystem + real Documents/Downloads/Desktop, shell)
3. ~~Voice: wake word -> STT -> orchestrator -> TTS~~, plus conversational
   follow-up window, barge-in, and sentence-by-sentence streamed replies
   (speak sentence 1 while later sentences are still generating)
4. ~~Desktop/app control~~ (screenshot as a real image the model can see,
   list windows, click, type, press keys, open apps)
5. ~~Cartesia TTS~~ (natural cloud voice, matching the AI-receptionist
   project) with automatic Piper fallback when offline/unconfigured
6. ~~Autonomous agent loop~~ -- `argus agent "<goal>"`: extended tool-use
   loop (up to 25 iterations, 10min wall-clock cap by default) with a full
   JSONL audit trail of every tool call at data/agent_audit.jsonl. Same
   permission tiers as normal conversation -- ALLOW tools run unsupervised,
   CONFIRM tools still require a human's yes. Self-modification (below)
   depends on this being solid first, since both are about Argus taking
   action with less direct supervision.
7. Email/calendar, smart home (Home Assistant) -- need OAuth/app setup first
8. ~~Camera/vision~~ (single-shot, on request): capture_camera tool
   (opencv-python-headless), same bytes-result pipeline as the screenshot
   tool. CONFIRM-tier -- more sensitive than a screenshot since it captures
   the physical room/person. *Periodic* ambient capture is a further step
   not yet built (this is on-request only, like "what does this look like"
   or "hold that up to the camera"). Face *recognition* (identifying
   specific known people, not just "a face is present") is a distinct,
   harder capability -- needs a local enrollment/embedding system since
   that's biometric matching, and carries real privacy considerations to
   think through explicitly before building. ASL translation is the
   hardest of the three -- continuous gesture recognition, not single-
   frame classification, and good open models for it are scarce; treat as
   a stretch goal, not a near-term deliverable.
9. Proactive daily briefing -- greets you (morning / when you sit down) with
   weather, calendar, relevant overnight news, anything flagged while away,
   instead of only ever responding when spoken to.
10. Scheduled routines/automations -- "every morning at 7, check X and tell
    me": recurring tasks it just does on a schedule, tied into the agent
    loop rather than requiring you to ask each time.
11. ~~Reminders & a real task list~~ -- persisted (sqlite), surfaced
    proactively (a background poll speaks a due reminder even outside an
    active conversation), not just answered once and forgotten.
12. ~~Remote access from your phone~~ -- a Telegram bridge (`argus voice`
    only): set TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_CHAT_ID and message
    your bot from anywhere. No inbound port opened -- it only long-polls
    Telegram's servers -- and messages route through the same text-input
    path the console uses, so full tool/memory/streaming-reply access.
13. Plugin/skill system -- a clean pattern for adding new tools/capabilities
    without touching core code, so the tool ecosystem can keep growing
    without every addition being a bespoke change.
14. Undo/rollback for risky actions -- especially file writes and desktop
    control: back up before overwriting, so a bad CONFIRM-tier action is
    recoverable, not just "confirmed and done."
15. ~~Observability dashboard~~ -- **Argus Console**: a live local web UI
    (`argus voice`/`argus chat` auto-launch it) showing real-time voice
    state, transcript, tool calls (with actual generated images -- e.g. a
    live screenshot thumbnail), memory stats, routing tier, and spend.
    Runs in-process over a WebSocket since it reads an in-memory event bus,
    so it only shows data when launched alongside chat/voice, not standalone.
16. ~~Memory privacy/audit controls~~ -- `argus memory export <path>`
    dumps everything (core, episodic, semantic) to JSON; `argus memory
    forget` purges episodic + semantic with a typed confirmation (core
    memory untouched -- that already has its own review flow). CLI-only,
    deliberately not an LLM-callable tool -- a mishearing shouldn't be
    able to wipe real history.
17. Backup/restore -- everything lives only on this machine (SQLite +
    Chroma); a simple encrypted backup step before this becomes something
    relied on daily.
18. Graceful offline-degraded mode -- Ollama already falls back for chat
    when Anthropic's unreachable, but tools like web search and desktop
    control just fail silently right now. Clear "I'm offline, here's what
    I can still do" behavior instead.
19. Voice journaling -- a lightweight "take a note" mode that captures
    freeform spoken thoughts to a running, later-searchable log. Natural
    fit given the voice-first design and existing memory system.
20. Multi-language support -- on-the-fly translation, useful for travel or
    reading/responding to non-English content.
21. Meeting assistant -- join/transcribe/summarize calls and extract action
    items. Genuinely valuable but a bigger lift than the others here (needs
    audio capture from other apps, or a bot-join integration).
22. Self-modification -- Argus edits its own source in response to
    conversation ("you can't do X yet" -> plan it, build it, right then).
    Deferred until the autonomous agent loop (item 6) is solid, since both
    are about less-supervised action and the second should build on lessons
    from the first. Design decided when this comes up again:
    - Open its file tools to its own `src/argus` (currently excluded from
      the sandbox on purpose).
    - Every change is a real git commit -- nothing is ever unrecoverable,
      and the diff is inspectable after the fact.
    - It must run the test suite itself; a change only counts as done if
      tests pass, not just "looks right."
    - Self-edits require explicit user confirmation, at minimum -- this is
      a materially higher-risk category than other CONFIRM-tier actions
      (e.g. it editing its own permission tiers), so lean toward confirm
      rather than broad access here even once other tools loosen up.
    - No hot-reload: Python doesn't pick up code changes in a running
      process, so the flow is propose -> tests pass -> tell the user to
      restart (or later, have it restart itself deliberately) -- never a
      silent rewrite-and-keep-running-on-old-code situation.

### Lower priority / nice-to-have

- Speaker recognition (know *who's* talking -- useful once more than one
  person interacts with Argus).
- Document/receipt scanning via camera.
- Voice-gated confirmation for the riskiest actions (an extra layer beyond
  a typed/spoken "yes").

### Dream / stretch goals

- Personal knowledge graph -- not just flat memory, but a structured map of
  people/projects/relationships built over time, so it can answer
  relational questions ("who else is on the Coshocton line besides Jason?").
- Second-brain ingestion -- drop a PDF/note/article into a watched folder
  and it auto-indexes + summarizes into memory, building real long-term
  expertise on your world instead of only remembering conversations.
- Proactive research digests -- tell it what you care about (a competitor,
  a technology, a hobby) and it periodically surfaces a digest unprompted.
- "Teach me once" macros -- walk it through a multi-step task manually one
  time and it generalizes that into a replayable skill, instead of you
  re-describing the same task every time.
- Ambient stuck-detection -- with desktop visibility already in place, it
  could notice you've been stuck on the same error for a while and offer
  help unprompted, rather than only ever reacting to a direct ask.
- "Second opinion" mode for big decisions -- reason from a few angles
  (skeptic, domain expert, risk-focused) internally before giving one
  synthesized recommendation on something consequential.
- Cross-device handoff -- once remote/mobile access exists, a conversation
  started on your phone continues seamlessly when you sit at this machine.

### Later / not yet scoped

- Smarter conversational listening: after the wake word, distinguish speech
  actually directed at Argus from ambient conversation/background noise,
  rather than relying on a fixed follow-up timeout. Likely needs the LLM
  itself judging addressee intent from the transcript, not just VAD timing.
- Custom "Argus" wake-word model (currently using openWakeWord's bundled
  hey_jarvis_v0.1 as a placeholder) -- deferred; real chunk of work
  (synthetic training data, negative-audio dataset, slow CPU training).
- Investigate why time-to-first-sentence in streaming mode is slower than
  expected (~13s in one live test) -- likely system-prompt size or API
  latency, not a code regression, but worth profiling separately.
