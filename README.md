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

1. ~~Core loop (text-only)~~ -- this repo, in progress
2. Tools + permission tiers (allowlist / confirm / deny)
3. Voice: wake word -> STT -> orchestrator -> TTS, with barge-in
4. Autonomous agent loop (separate mode, goal file, budget caps, audit log)
5. Computer control + smart home (Home Assistant)
