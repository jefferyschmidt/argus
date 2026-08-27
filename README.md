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
argus chat            # interactive chat
argus memory review   # confirm/reject agent-proposed core memories
```

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
5. Cartesia TTS (natural cloud voice, matching the AI-receptionist project)
   with automatic Piper fallback when offline/unconfigured -- in progress
6. Autonomous agent loop (separate mode, goal file, budget caps, audit log)
7. Email/calendar, smart home (Home Assistant) -- need OAuth/app setup first
8. Camera/vision: periodic frame capture + Claude vision for general scene
   and object description (straightforward, same pattern as the screenshot
   tool). Face *recognition* (identifying specific known people, not just
   "a face is present") is a distinct, harder capability -- needs a local
   enrollment/embedding system since that's biometric matching, and carries
   real privacy considerations to think through explicitly before building.
   ASL translation is the hardest of the three -- continuous gesture
   recognition, not single-frame classification, and good open models for
   it are scarce; treat as a stretch goal, not a near-term deliverable.

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
