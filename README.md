# Argus

A personal, local-first AI assistant. Runs on this machine, routes between a
local Ollama model and Anthropic's API depending on task difficulty, and
keeps a layered memory (core / semantic / episodic) so it doesn't forget
things that matter.

Code lives in a private repo so it can be tracked and backed up even though
it never runs anywhere but locally.

## Status

**Added live, 2026-08-28 (loudness as an addressee signal, a "show me"
window, and a computer-vision-style camera default)**: three separate
live requests.

1. Asked directly: "does Argus understand that the quieter a sound is,
   the more likely it is to be background noise?" No -- the addressee
   gate (`_seems_addressed_to_argus`) looked only at the transcribed
   TEXT, never how loud/close the utterance actually was. Added
   `_loudness_hint` (peak RMS relative to the same silence floor already
   used for VAD gating) folded into the local model's classification
   prompt as an extra signal alongside the words -- deliberately NOT a
   hard code-level gate, so a real question asked quietly on purpose
   still passes immediately via the existing fast-path ("?" / direct
   opener).
2. A "show me" window: `show_website` (new tool) and `fetch_image` (now
   also publishes a `show_modal` event) open a large modal in the console
   (`#showModal`, image or embedded-site iframe) -- distinct from the
   small "Visual output" history strip, which stays for incidental
   captures (screenshots, camera frames). Some sites refuse to be framed
   (their own CSP); the title always includes the real URL as a fallback.
3. `capture_camera` now defaults to displaying a computer-vision-style
   edge-outline rendering (`_stylize_vision`, OpenCV Canny, drawn in the
   console's own accent color) instead of the literal photo -- the raw
   photo only displays when `raw=true` is explicitly requested. The
   MODEL still always analyzes the real captured frame either way (e.g.
   to answer "what am I holding") -- this only changes what's shown on
   screen, via a dedicated display event the tool publishes itself, with
   `Orchestrator._on_tool_call`'s generic auto-display skipped
   specifically for this tool so the raw frame doesn't ALSO silently
   appear.

Not yet visually verified live (backend logic and event-publishing are
fully unit-tested; the modal's actual on-screen appearance/interaction
needs a real run to confirm).

**Added, 2026-08-28 (a real session event log, and grouped desktop-control
confirmation)**: asked live -- "is there a good way to let you review
Argus's sessions... would we have to write transcripts as well as
debugging/action info?" Answer: no new instrumentation needed -- every
meaningful thing (conversation turns, state transitions, tool confirm
requests/resolutions, memory events) already flows through the single
`ui_events.publish()` choke point. It now also appends every event, with
a timestamp, to `data/events/events-YYYY-MM-DD.jsonl` regardless of
whether a UI is connected, so a session is fully reviewable afterward
without needing to have been watching live. Also added an
`addressee_gate` event published whenever a follow-up-window utterance
gets silently dropped as "not meant for Argus," with the actual text and
verdict -- directly targets the recurring "struggling to figure out when
I'm talking to him" complaint, which was previously only guessable from
console scrollback that's long gone by the time it's reported.

Separately -- reported live: "if I say 'open my calculator and add 4+4,'
that's explicit permission... I shouldn't also have to say yes to 'can I
open the calculator' and 'can I click.'" The `repeatable` mechanism added
earlier today only deduplicated repeat calls of the exact SAME tool --
open_app still asked once, then the first click asked again separately.
Added a `group` field on `Tool`: tools sharing a group (`click`,
`type_text`, `press_key`, `scroll`, `open_app` all share
`"desktop_control"`) now share ONE approval for the rest of the task --
the first confirm ask for any of them covers the rest. `capture_camera`
deliberately stays its own, ungrouped, always-ask tool (physical room,
not screen -- a meaningfully different sensitivity).

**Fixed, 2026-08-28 (log review -- local tier rate-limits escalated
straight to the paid frontier, no retry)**: went looking through
`data/argus.log` for anything worth fixing beyond live reports. Found
`ModelRouter.complete()`'s LOCAL path treating a `groq.RateLimitError`
the same as any other local-tier failure -- escalating straight to the
paid Anthropic frontier tier on the very first hit. Groq's free tier is
a tight shared budget (8000 TPM) across every LOCAL-tier caller now
(idle emotes, memory consolidation, ordinary small talk), and its own
error message routinely says "try again in ~360ms" -- a real, short-lived
contention spike, not genuine unavailability. Escalating immediately
defeats the whole point of the free local tier and directly works against
today's memory-system "stay cheap" goal, since consolidation adds a new
regular consumer of that same budget. Now retries once after a 1.5s pause
before falling through to the existing escalation safety net. (Also
checked: recurring idle-emote JSON parse failures and occasional Groq STT
failures -- both already have working graceful fallbacks by design, left
alone; a one-off `pyscreeze`/Pillow import error and a `PathEscapesAllowedRoots`
on the project directory via the general `list_dir` tool -- neither has
recurred/mattered since, not touched.)

**Fixed live, 2026-08-28 ("Stop listening" still wasn't a real mute)**:
reported live -- "it needs to basically be a mute input button for
Argus." The earlier persistent-pause redesign (see below) made pause
state stick, but only took effect BETWEEN listen attempts -- if the mic
was already mid-capture when paused, it kept actively capturing (and
transcribing) for however long that attempt took (up to 20s for the
wake-word engine, up to the full follow-up window otherwise), which
doesn't read as "muted" at all. Added `ListeningPaused`
(`voice/audio_io.py`) plus a `should_stop` callback threaded through
`record_followup`, `LocalWakeWordListener`, and `WakeWordListener` --
checked every audio frame, not just once per call/utterance, and raised
immediately (not just an early return) so the `with sd.InputStream(...)`
block actually closes the stream on the way out. `voice/loop.py` passes
`ui_commands.is_listening_paused` as `should_stop` and catches
`ListeningPaused` in both the wake-word listen and the follow-up loop,
looping back to `_wait_while_listening_paused()`, which blocks with no
stream open at all until resumed.

**Added, 2026-08-28 (voice-accessible core-memory review)**: with the
memory consolidation worker now proposing facts unattended every ~10
minutes, pending memories could pile up invisibly if nobody happened to
be looking at the console -- confirming/rejecting one was previously only
possible by clicking Confirm/Reject in the browser or running `argus
memory review` in a terminal. Added `list_pending_core_memories`,
`confirm_core_memory`, `reject_core_memory` tools (direct DB access via a
fresh connection, same pattern as `ui/server.py`'s
`_resolve_core_memory`, publishing the identical `core_memory_resolved`/
`memory` events so a connected console stays in sync) so "what's pending
to remember" and "yes, remember that one" work hands-free.

**Fixed live, 2026-08-28 (a transient audio-device error crashed the
whole process)**: reported live -- a `sounddevice.PortAudioError:
Unanticipated host error [PaErrorCode -9999]: 'There is no driver
installed on your system.'` raised out of `stream.read()` mid-listen and
killed the entire Argus process, full traceback to the terminal. This
class of error is inherently transient (a Bluetooth mic dropping out,
another app briefly grabbing exclusive access, a sleep/resume cycle) --
the fix isn't to prevent it, it's to not let one bad read kill the whole
session. Both places in `voice/loop.py` that block on live mic reads
(the wake-word listen and the follow-up-window listen) now catch
`sd.PortAudioError` specifically, log it, wait 3s, and loop back to try
again with a fresh `InputStream` -- the same "worth a beat, not a crash"
philosophy as the existing barge-in-watcher and restart-flush fixes.

**Added, 2026-08-28 (skip embedding filler turns -- second piece of the
memory system's "stay cheap and relevant" goal)**: `remember_turn` used to
embed every single turn indiscriminately, confirmed by an earlier live
diagnostic (300 filler documents like "okay"/"thanks"/"sounds good"
against 1 real fact) to measurably dilute semantic recall at realistic
scale. Added `_is_worth_embedding` (too short, or an exact filler match
after normalizing) -- filler turns still go into episodic storage (short-
term recency is unaffected), they're just never embedded/searched.
Complements memory consolidation: fewer, more relevant embeddings now,
denser durable facts distilled over time.

**Added, 2026-08-28 (memory consolidation -- first piece of the
"best-ever memory system")**: implements two of the four explicit design
goals agreed on 2026-08-28 ("feels like accumulated knowledge, not a
lookup" and "stay cheap as it grows" -- the other two, prompt-cached
injection and a searchable cold archive, were already true of the
existing memory system on inspection: `orchestrator.py` already passes
`cacheable_system=SYSTEM_PROMPT`, and `episodic`/`semantic` stores
already keep every raw turn, untouched, forever). New
`argus/memory/consolidation.py`: a background worker
(`ConsolidationWorker`, polls every `memory_consolidation_poll_seconds`,
10 minutes by default) periodically distills new episodic turns into
durable core-memory candidates on the cheap `Tier.LOCAL` model, never the
frontier tier -- same design as `idle_emote.py`'s on-the-fly generation.
Every distilled fact goes through the exact same propose/review flow
(`CoreMemoryStore.propose`, `core_memory_pending` UI event) as any other
agent-proposed core memory -- nothing is ever auto-confirmed. A cursor
(`consolidation_state` table) tracks the last episode considered so nothing
is re-summarized, and doesn't advance on a failed model call, so a
transient error just retries next poll rather than silently skipping that
chunk of conversation forever.

**Fixed live, 2026-08-28 (Argus didn't know its own capabilities)**:
reported live -- asked what to say to get its attention, Argus suggested
"hey jarvis" (the real wake word is "Argus"; `hey_jarvis_v0.1` is only an
inert placeholder filename for the openWakeWord engine, which isn't even
the active one by default). Separately, asked what features it wished it
had, it claimed it can't act unprompted and can't create/modify files
outside its own source -- both already exist (six background workers
already speak up unprompted: context awareness, stuck detection, email
watching, research digest, reminders, scheduled routines; `write_file`
already covers Documents/Downloads/Desktop, not just self-editing) but
were invisible to Argus's own self-knowledge -- the background workers
aren't tool calls so had zero presence in the system prompt, and
`write_file`'s real scope wasn't called out clearly enough among 30+
tool schemas. Renamed the misleading setting (`wake_word_model` ->
`openwakeword_model_name`, with a comment on the live confusion) and
added explicit "## Already proactive" and file-tool-scope sections to
SYSTEM_PROMPT so this kind of self-assessment answers correctly.

**Fixed live, 2026-08-28 (replies to Argus's own unprompted speech got
silently dropped)**: reported live -- Argus asked "sounds like there's
something else on your mind about tomorrow, what's up?" (a proactive
context nudge), the user answered directly, and the console showed only
a fragment ("expensive.") stuck on "checking whether that was really
meant for me," never actually responding -- described as Argus
"disregarding" what was said. Root cause: only the normal reply path
(`_process_utterance`) refreshed the hot-mic hands-free window; anything
Argus said on its OWN initiative -- proactive check-ins, email alerts,
reminders, scheduled routines -- spoke via `_speak_with_barge_in`
directly and never opened that window, so answering it still required
saying the wake word first. Any reply given without it just fragmented
against the strict wake-word check and got silently discarded. Fixed
with `VoiceLoop._speak_and_open_mic` (wraps `_speak_with_barge_in` +
`_refresh_hot_mic`, now used by every background worker instead of
`_speak_with_barge_in` directly) plus a new `hot_mic_check` callback on
`LocalWakeWordListener.listen_for_wake_and_command`: when the hot-mic
window is open, a captured utterance is treated as addressed to Argus
without the wake-word requirement at all -- the same hands-free
follow-up a normal reply already gets.

**Added live, 2026-08-28 (direct email unsubscribe + a scroll tool for
desktop control)**: reported live -- Argus was "still struggling to get
it to unsubscribe" from a marketing email via desktop-automation clicking
(imprecise coordinate-guessing at a tiny link, discussed live via the
argus.log tool-call trail -- there's no separate conversation-transcript
file, only infrastructure call logging). Root cause of the underlying
struggle: desktop control had no `scroll` tool at all -- anything below
the initial screenshot's viewport (a link near the bottom of a long email,
an item further down a list) was simply unreachable by clicking alone.
Added `scroll` (repeatable, like click/type_text/press_key). Separately,
and more directly: most real marketing email carries a machine-readable
`List-Unsubscribe` header (RFC 2369/8058) -- a one-click HTTPS link or a
mailto:, no screen-clicking needed at all. Added `unsubscribe_from_email`
(finds the message by sender/subject match, uses the header directly,
falls back to telling the user it needs a manual click only when the
email genuinely has no machine-readable option) and a system-prompt rule
that an internal tool always beats desktop/browser control when one
exists for the task.

**Fixed live, 2026-08-28 (stuck on "listening," never processing a reply)**:
reported live -- "he's stuck on listening and isn't processing what I
said," with a screenshot showing the local wake-word engine's "checking"
state hung indefinitely after the user said "Argus, I said yes, I want
you to handle that." Root cause: local Whisper (already documented in
this file as prone to mishearing "Argus" on short clips) apparently
transcribed something close but not in `_WAKE_PATTERN`'s exact word list,
so the wake-word match silently failed and the loop just re-checked the
next utterance forever with no way out -- looks exactly like "stuck,"
even though speech was genuinely being heard and transcribed the whole
time. Added a fuzzy fallback (`_find_wake_word`, `difflib.SequenceMatcher`
against "argus" at a 0.72 ratio) that only kicks in when the exact regex
misses, so a near-miss transcription still gets through.

**Fixed live, 2026-08-28 (memory embeds silently dropped on restart)**:
`MemoryManager._embed_pool` queues each turn's semantic embedding on a
background thread and had no shutdown/flush path; `restart.py`'s
`os.execv`-based restart replaces the process image directly, bypassing
normal interpreter shutdown/atexit entirely, so anything still queued at
that exact moment was silently lost -- the most recent turn(s) never
becoming recallable. Added `MemoryManager.flush_pending_embeds(timeout)`
(bounded wait via `concurrent.futures.wait`, mirrors the barge-in
watcher's bounded-join pattern) plus a `set_active_memory_manager`/
`get_active_memory_manager` registry (mirrors the existing active-router
registry), called from `request_restart` right before `os.execv`.

**Tightened live, 2026-08-28 (mouth movement read as the whole swarm
moving)**: reported live -- "the swarm around his mouth shape is moving
when he talks... it should either just slightly move or not move at all."
`particleTarget`'s mouth-bulge displacement affected a wide band (most of
the lower half of the face) at full `mouthOpenPx` amplitude, competing
visually with `drawLiquidLipSeam`'s actual mouth-shape overlay. Narrowed
the affected band to a tight zone right at the mouth line and cut the
displacement to 25% of its previous amplitude -- just a faint nudge, not
a second copy of the articulation.

**Fixed live, 2026-08-28 (voice confirmations gave no "listening" cue)**:
reported live -- "he asked 'may I click?' for approval for something, but
didn't go to 'listening' to hear my response." `make_voice_confirmer`'s
`_try_voice` spoke the yes/no prompt and started recording, but never
published a state update -- the console stayed on whatever it last showed
while genuinely waiting on the mic. Fixed by publishing `{"type": "state",
"value": "listening", "mode": "confirming"}` (reusing the existing
`confirming` label) right before recording, and a `thinking` state after,
so the console visibly shows Argus is listening for the spoken answer.

**Fixed live, 2026-08-28 (a confirmation for every single click)**:
reported live -- "it's asking for confirmation for every click. That's
too much. It needs to be able to act a little more independently once it
has instructions and confirmation." Multi-step desktop automation (click,
type_text, press_key) asked to confirm on every single tool call, making
real tasks (several clicks in a row) unusable hands-free. Added a
`repeatable` flag on `Tool`: approving one call of a repeatable CONFIRM
tool auto-approves the rest of that same tool for the rest of the current
task (`ToolRegistry._task_approved`), reset at the start of every new
user-initiated turn (`Orchestrator.handle`'s `reset_task_autonomy()`
call) so it never carries across unrelated requests. `click`, `type_text`,
and `press_key` are now marked `repeatable=True`; higher-stakes one-shot
actions (`open_app`, `capture_camera`, filesystem writes, email, etc.)
are untouched and still confirm every time.

**Tightened live, 2026-08-28 (still too wordy despite the earlier
trim)**: reported live -- "he's probably saying 500 words when 50 would
do." The existing "BE CONCISE" rule said "a few sentences by default,"
which wasn't restrictive enough in practice, and multi-step tool tasks
(e.g. desktop automation) were narrating every individual step aloud,
which adds up fast even when each narration is individually short.
Tightened the target to "a sentence or two, 40 words or fewer" and added
an explicit instruction to work through multi-step tool sequences without
a running commentary, giving one short summary at the end instead.

**Fixed live, 2026-08-28 (local wake-word: no feedback during the slow
part)**: reported live -- "it heard me (you can see my speech to text),
but it's not doing anything." The console's live hearing caption showed
"Hey, Argus, give me a briefing." correctly, but the state stayed on
"waiting for the wake word" the whole time. Not a bug in detection itself
-- a real, measured gap in feedback: the local engine only knows whether
it heard the wake word AFTER transcribing the whole utterance locally
(CPU-bound), and the console gave zero visual indication that was even
happening. Measured directly on this hardware: local transcription of a
short clip took 8.24s cold (first call in a process, the model loads
lazily) and 3.17s warm -- easily long enough to look stuck with no
feedback at all. Fixed with a new `on_checking` callback
(`LocalWakeWordListener.listen_for_wake_and_command`) that fires the
moment real speech is captured and about to be checked, publishing a
state update ("Checking whether that was really meant for me," reusing
the existing `confirming` label) so the console visibly changes the
instant it starts working on what you said, not just when it finishes.

**Trimmed live, 2026-08-28 (system prompt conciseness)**: reported live --
Argus's spoken replies were running too long. The user first asked Argus
to trim its own SYSTEM_PROMPT via its self-improve tools; that attempt
introduced a real encoding bug (a mojibake character replacing an em
dash) and was undone before being committed. Did the trim directly
instead: SYSTEM_PROMPT cut from ~13,600 characters (~3400 tokens) to
~6,200 (~1550 tokens) -- roughly halved -- reorganizing the tool-guidance
section from one long run-on paragraph into short bulleted entries (the
system prompt itself isn't spoken, so markdown there is fine even though
Argus's own replies must never use it) and cutting explanatory padding
throughout, while keeping every actual instruction: every tool's real
guidance, the Amazon purchase boundary, the CORE_MEMORY/EXPRESSION marker
formats exactly, multilingual support, STT-mishearing handling, and the
"be concise" rule itself, which was moved earlier and given its own
short, direct paragraph rather than staying buried in a longer one.
Live-verified against the real Anthropic API through the actual
production call path (complete_with_tools, not a shortcut): "What's the
capital of France?" got back "Paris." -- one word, no padding -- and
casual replies stayed warm and on-brand, not clipped.

**Fixed live, 2026-08-28 (Whisper hallucination feedback loop)**: reported
live -- after a normal exchange ended, the transcript showed "You: Thank
you." / "Argus: You're welcome!" repeating for over a dozen exchanges,
verbatim, with the user saying nothing. Root cause: Whisper is
well-documented to hallucinate short boilerplate phrases ("thank you",
"thanks for watching," "bye") when fed near-silent or ambient audio --
Argus's own TTS bleeding faintly back into the mic right after he finished
talking (or just room noise) was enough to trigger it during the
follow-up window's RMS-threshold gate, which is more permissive than real
speech detection. The addressee gate's own documented "when uncertain,
assume addressed" bias then let the hallucinated phrase through, Argus
replied, and that reply's own echo restarted the cycle. Fixed by running
the ALREADY-CAPTURED audio through Silero VAD (the same detector already
used for barge-in) before ever handing it to Whisper at all -- real
silence/ambient noise is rejected before transcription, not filtered
after. Separately: the user tried "Stop listening" to break the live
loop and it didn't help, because that button only ever turned off the
hot-mic barge-in window specifically -- nothing in the normal wake-word or
follow-up loop ever checked it. Redesigned into a real persistent pause
(`ui_commands.set/is/toggle_listening_paused`) that the whole run() loop
holds on, in both places (wake-word wait and the follow-up window), until
explicitly turned back on -- not a one-shot flag that only ever covered
one specific case. The console button is now a real toggle (mirrors the
existing quiet-mode button pattern) instead of a fire-once action.

**Fixed live, 2026-08-28 (still stuck after the frame-size fix)**: the
wake-word frame-size fix above was real and necessary, but "stuck in
speaking mode even when he's done speaking" persisted -- a genuinely
separate bug. Root cause: `_watch_for_barge_in` ran synchronously inside
`_speak_with_barge_in`, and its `sd.InputStream.read()` calls have no
timeout. A mic-side hiccup there (a device stall, or stream-open/close
churn from the local wake-word engine's own separate InputStream cycling)
could block that call indefinitely -- and since nothing downstream ever
runs to publish a new state until `_speak_with_barge_in` returns, the
console stayed on "speaking" forever, well after actual audio playback had
finished. Fixed by running the watcher on its own thread and joining
`play_thread` directly instead of behind it -- playback finishing no
longer waits on the watcher also finishing; the watcher gets a bounded
5s grace period to exit cleanly, and if it's still stuck after that, it's
abandoned (daemon thread) and the turn continues rather than hanging. Also
fixed the reported "unnatural, jittering" mouth/swarm motion: `speaking`
had its own higher jitter/swirl energy than every other state, making the
WHOLE swarm visibly more agitated while talking, independent of and
distracting from the actual mouth articulation -- now uses the same calm
energy as `listening`, so only the mouth region visibly reacts to speech.
Added a direct regression test (mocks the watcher to hang forever,
forever) asserting `_speak_with_barge_in` still returns in well under the
hang duration -- this test would have hung indefinitely before the fix.

**Fixed live, 2026-08-28 (critical)**: the local wake-word engine added
earlier this session could never actually detect the wake word --
`LocalWakeWordListener` read the mic in 30ms/480-sample frames, but
`SpeechDetector.is_speech()` (Silero VAD) sub-chunks its input into blocks
of exactly its own required 512 samples; fed anything smaller, the
sub-chunking loop's range was empty and it silently returned `False`
unconditionally, no matter what was actually said. This explained all
three symptoms reported live in one shot: the wake word never firing at
all (direct cause); Argus getting stuck showing a jittering, synthetically-
wobbling open mouth (once real speech-timing data for a reply expired, the
mouth falls back to a synthetic wobble *while the client's state stays
"speaking"* -- which only happens if the backend never sends a follow-up
state update, exactly what a backend stuck forever in a broken wait loop
would do); and a page refresh landing on a permanently stuck "idle" (the
backend had nothing new to tell a freshly-connected client, since it was
never advancing past "waiting for wake word" in the first place). Fixed by
reading in Silero's own native 512-sample chunk size directly instead of
computing a frame length from an arbitrary millisecond duration. Confirmed
live end to end: streamed a real recorded utterance through the corrected
frame size and got 42 of 143 frames correctly speech-flagged, versus zero
before the fix. Also added a `CONSOLE_LOG_LEVEL` setting (default
`WARNING`, set to `INFO` for a debug session) -- console logging was
silently WARNING-only even though a full INFO+ log already existed at
`data/argus.log`, making live debugging harder than it needed to be.

**Fixed live, 2026-08-28**: `capture_camera`'s JPEG output was being sent to
the Anthropic API hardcoded as `image/png` (`_tool_result_content` in
`argus/llm/anthropic_client.py` assumed every tool-returned image was PNG --
true for `take_screenshot`, false for `capture_camera`'s `cv2.imencode(".jpg",
...)`), and there was no error handling around a turn failing at all --
confirmed live that the resulting 400 from the API crashed the entire `argus
voice` process, not just that one turn. Fixed both: `_tool_result_content`
now sniffs the real format from magic bytes instead of assuming, and
`VoiceLoop._process_utterance` catches any unexpected exception during a
turn, reports it, and keeps the session alive instead of dying. Live-verified
end to end against the real Anthropic API with a real camera capture (the
exact reported scenario) -- correct description back, no crash.

**Console UI pass, 2026-08-28**: rebuilt Argus's face
(`argus/ui/static/index.html`) as a bio-luminescent particle-swarm
avatar -- ported from a design explored with Gemini, replacing the
wireframe/CRT-glitch head. ~900 particles dissipate to a loose cloud at
idle and coalesce into a face as engagement ramps up (idle -> listening ->
thinking -> speaking, each with its own cohesion/density/jitter/swirl
energy), tint toward the active named expression's color, and the mouth is
still driven by the same real Cartesia viseme/amplitude data as before.
Also added **idle emotes**: while genuinely idle, Argus occasionally forms
a small one-off "accessory" scene -- a hat, glasses, whatever -- generated
FRESH by an LLM call each time (`argus/idle_emote.py`, `/api/idle_emote`),
not replayed from a fixed set, with the base head silhouette always kept
client-side as a safety/quality bound on what a generation can do, and a
hand-authored fallback spec used if generation fails or the console isn't
wired to a live orchestrator. Also fixed three real UX papercuts flagged
live: the console now opens in a real new window (`webbrowser.open_new`,
not `.open`, which just let the browser decide and usually meant a buried
tab); the live caption box now shows the FULL current reply and scrolls
internally instead of discarding everything but the last two sentences;
and the console's wake-word flag no longer claims `hey_jarvis_v0.1` is
active when the local engine (which uses no trained model at all) is
actually running.

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
7. ~~Email~~ -- read (IMAP, argus/email_watcher.py -- see the proactive-
   context-style writeup further down) and send (SMTP, send_email tool in
   argus/tools/email.py, same app-password credentials work for both, no
   extra setup). ~~Google Calendar~~ -- real OAuth2 API access
   (argus/google_calendar.py, list_calendar_events/create_calendar_event
   tools), not browser automation -- one-time `argus calendar auth` after
   setting GOOGLE_CALENDAR_CLIENT_ID/SECRET from a Cloud Console OAuth
   client, then silent after that. Yahoo Calendar has no viable third-
   party API anymore, skipped. ~~Amazon order tracking~~ -- same browser-automation pattern
   (amazon.com/gp/css/order-history): checking status/delivery/tracking
   is fine unsupervised, but placing or completing any purchase is a hard
   no regardless of how it's asked -- that's a real financial transaction
   and stays a "you do it yourself" action, same boundary Claude Code
   itself holds. Smart home (Home Assistant) still needs its own setup.
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
9. ~~Proactive daily briefing~~ -- not a separate feature, just an easy
    routine (see item 10): "every morning at 7, give me a briefing" sets
    up a routine whose goal naturally pulls in weather (web search),
    today's reminders, and anything notable in email. Calendar isn't in
    the briefing yet -- still needs OAuth (see item 7's other half).
10. ~~Scheduled routines/automations~~ -- create_scheduled_routine/
    list_scheduled_routines/cancel_scheduled_routine (argus/tools/
    routines.py + argus/routine_worker.py). A routine fires once per
    calendar day at its time_of_day, running its goal through the FULL
    tool-using conversational pipeline (not the cheap local tier the
    other background workers use, since a briefing needs real tool
    calls), then speaks the result. Same non-blocking-lock delivery
    pattern as reminders/context-awareness/email -- never barges into an
    active conversation, tries again next poll if busy.
11. ~~Reminders & a real task list~~ -- persisted (sqlite), surfaced
    proactively (a background poll speaks a due reminder even outside an
    active conversation), not just answered once and forgotten.
12. ~~Remote access from your phone~~ -- a Telegram bridge (`argus voice`
    only): set TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_CHAT_ID and message
    your bot from anywhere. No inbound port opened -- it only long-polls
    Telegram's servers -- and messages route through the same text-input
    path the console uses, so full tool/memory/streaming-reply access.
13. ~~Plugin/skill system~~ -- drop a .py file defining module-level Tool
    instances into src/argus/plugins/ and it's auto-registered on next
    startup (argus/plugin_loader.py), zero core-file edits. A plugin
    that fails to import is skipped with a warning, not a crash; a
    plugin can't override a built-in tool's name. example_dice.py ships
    as a real, working example (a roll_dice tool) -- delete it if you
    don't want it, it's not load-bearing for anything else.
14. ~~Undo/rollback~~ -- scoped to file writes (argus/undo_log.py);
    desktop-action undo has no generic mechanism (there's no "undo" for
    an arbitrary click) so stays out of scope. write_file and
    write_own_source both snapshot the target's prior content before
    overwriting; undo_last_write (no confirmation needed -- undo is
    itself the corrective action) reverts the most recent write to a
    given path, or the single most recent write of any file.
    list_recent_writes shows what's undoable.
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
17. ~~Backup/restore~~ -- `argus backup <path>` / `argus restore <path>`
    (argus/backup.py). Zips the sqlite db + Chroma vector store +
    sandboxed workspace, encrypts with a passphrase (PBKDF2-SHA256 ->
    Fernet/AES-128-CBC+HMAC, tamper-evident not just confidential).
    Passphrase typed via getpass (never written anywhere, never touches
    .env or any file). Never includes .env itself -- a memory backup is
    meant to be portable/storable, live API keys shouldn't travel with it.
18. ~~Graceful offline-degraded mode~~ -- turned out worse than "fails
    silently": complete_with_tools/complete_with_tools_streaming had NO
    handling at all for Anthropic being unreachable, so a real network
    outage raised an uncaught exception straight through the orchestrator
    and crashed the whole turn (chat's console.input() loop, voice's
    _process_utterance -- neither had a catch for it). ModelRouter now
    catches anthropic.APIConnectionError at every frontier call site and
    falls back to the local model with a clear disclaimer (tools/web
    unavailable in the fallback), or a plain "I'm offline" message if
    local is unreachable too -- always a normal reply, never an exception.
    Found and fixed a second related gap while testing this: the local-
    tier fast-path had no error handling either, so a stale
    is_available()==True followed by an actual failure would also crash;
    that path now escalates to the frontier (and from there to the same
    degraded fallback) instead.
19. ~~Voice journaling~~ -- "note to self: ..." / "journal this" / "take a
    note" (inline content, or say the trigger alone and Argus listens for
    the entry next) logs a freeform spoken thought, bypassing the LLM
    entirely for a fast, deterministic capture-and-confirm. Searchable via
    `argus journal [query]` or in conversation (search_journal tool).
    Separate from episodic memory (conversation) and core memory (standing
    facts) -- this is freeform thinking-out-loud with Argus as scribe.
20. ~~Multi-language support~~ -- turned out to be a genuinely small lift:
    Claude is multilingual out of the box (system prompt just needed a
    one-line "translate on request, don't hedge" nudge), and Cartesia TTS
    already speaks other languages fine. The real gap was STT hardcoded
    to language="en" everywhere, plus the local Whisper model being the
    English-only "base.en" variant (physically can't transcribe anything
    else, regardless of the language parameter). Fixed both: STT_LANGUAGE
    empty = auto-detect (the default), local model switched to
    multilingual "base". Verified live end-to-end: real Spanish audio
    (via Cartesia TTS) correctly auto-detected and transcribed through
    Groq's hosted Whisper.
21. Meeting assistant -- DEFERRED INDEFINITELY at the user's direction
    (not a priority). Would join/transcribe/summarize calls and extract
    action items; needs audio capture from other apps or a bot-join
    integration, the biggest lift of anything on this list. Revisit only
    if priorities change.
22. ~~Self-modification~~ -- Argus edits its own source through normal
    conversation (voice, chat, or Telegram -- no separate CLI mode):
    read_own_source/list_own_source/write_own_source/run_own_tests/
    commit_own_changes/restart_argus (argus/tools/self_improve.py), scoped
    to src/argus + tests only (a separate, narrower sandbox from the
    general file tools' workspace_dir). write_own_source and
    commit_own_changes are always CONFIRM-tier. The system prompt tells it
    to read before writing, run its own tests after every write and
    report honestly (never claim success without seeing tests pass), and
    only commit once they do. restart_argus (also CONFIRM) re-execs the
    process via `python -m argus.cli <original args>` (argus/restart.py)
    since Python can't hot-reload -- also available as a console button
    (with a confirm prompt) for when you just want to restart without
    asking Argus to.
      process, so the flow is propose -> tests pass -> tell the user to
      restart (or later, have it restart itself deliberately) -- never a
      silent rewrite-and-keep-running-on-old-code situation.
23. ~~Proactive context awareness~~ -- periodically checks the active
    window (argus/context_awareness.py) and, when the context has
    meaningfully changed or the user's been in the same one a long time
    (2hr default), asks the fast/cheap tier whether there's something
    genuinely worth saying, with an explicit NONE escape hatch so most
    scans produce nothing. Delivered via the same non-blocking-lock
    pattern as reminders (never barges into an active conversation) plus
    a "curious" expression. Global on/off, and "don't ask me about this"
    suppresses it per-window for the session.

### Lower priority / nice-to-have

- Speaker recognition (know *who's* talking -- useful once more than one
  person interacts with Argus).
- ~~Document/receipt scanning via camera.~~ Done: the `scan_document` tool
  (argus/tools/scan_document.py) captures a webcam frame (same capture
  logic as capture_camera), sends it through a new one-shot vision call
  (AnthropicClient.complete_with_image / ModelRouter.complete_with_image --
  no tool loop, just an image + prompt in a single message) that extracts
  vendor/date/total/key details as a searchable paragraph, then stores it
  into semantic memory (same store second-brain ingestion uses) so
  questions like "how much did I spend at X" are answerable later.
  CONFIRM-tier, same sensitivity reasoning as capture_camera. Live-verified
  against the real Anthropic API: a synthetic receipt image (Trader Joe's,
  itemized, dated) produced a correct extraction -- right vendor, right
  total, right items, right date -- with real spend recorded through the
  shared cost governor.
- ~~Voice-gated confirmation for the riskiest actions (an extra layer beyond
  a typed/spoken "yes").~~ Done: a `high_risk` flag on `Tool` (send_email,
  write_own_source, commit_own_changes, restart_argus) makes
  `ToolRegistry.execute` ask for confirmation twice, not once, before
  running -- a single misheard "yes" from STT is a real, previously
  unmitigated risk for exactly these irreversible/visible actions.

### Dream / stretch goals

- ~~Personal knowledge graph -- not just flat memory, but a structured map
  of people/projects/relationships built over time, so it can answer
  relational questions ("who else is on the Coshocton line besides
  Jason?").~~ Done: a plain subject-predicate-object triple store
  (argus/memory/knowledge_graph.py, a new kg_facts table), not a real
  graph database -- at a personal assistant's scale (hundreds to low
  thousands of facts) that covers both traversal directions with two
  indexes, no need for real graph-engine overhead. remember_relationship/
  query_relationships tools let the model add structured facts
  opportunistically during normal conversation (the same way core memory
  proposals already work) rather than needing a separate extraction
  pipeline, and look them up by entity (case-insensitive substring match
  on either side of the triple) for genuinely relational questions that
  semantic search alone can't answer -- finding documents *about* a topic
  is different from finding a specific typed relationship between two
  named things. Live-verified against the exact motivating example from
  this roadmap entry: stored 4 real facts (who works on/manages the
  Coshocton line, who reports to whom), queried "Coshocton line" and got
  back all three people correctly (Jason, Priya, Marcus), queried "Jason"
  and got back both of his facts correctly. A forget_relationship tool
  (exact subject/predicate/object match, so a near-miss reports nothing
  matched rather than guessing which fact to delete) rounds this out for
  correcting a fact that's now wrong or outdated -- live-verified removing
  one of two facts about the same project and confirming only the
  intended one was gone afterward.
- ~~Second-brain ingestion -- drop a PDF/note/article into a watched folder
  and it auto-indexes + summarizes into memory, building real long-term
  expertise on your world instead of only remembering conversations.~~
  Done: `argus/ingest.py` extracts text (PDF via pypdf, txt/md directly),
  chunks it (1500 chars, 200 overlap -- the embedding model silently
  truncates long input, so a whole document as one chunk would only ever
  be searchable by its first ~256 tokens), and stores it into the same
  SemanticStore conversation recall already searches -- ingested documents
  are recallable with zero changes to the recall path. Two ways in:
  `KNOWLEDGE_WATCH_FOLDER` (argus/knowledge_watcher.py) auto-ingests
  anything dropped in a watched folder and announces new files (existing
  files are ingested silently on first run, no backlog-flood announcement
  spam), or the `ingest_document` tool for "read this file" on demand.
  Re-ingesting a changed file overwrites its old chunks (deterministic doc
  ids + upsert) rather than duplicating them. Live-verified: ingested a
  real text file, searched a natural-language question against it through
  the real Chroma store, got the right chunk back.
- ~~Proactive research digests -- tell it what you care about (a
  competitor, a technology, a hobby) and it periodically surfaces a digest
  unprompted.~~ Done: track_research_topic/list_research_topics/
  untrack_research_topic (argus/tools/research_topics.py) manage a
  tracked-topics list; ResearchDigestWorker (argus/research_digest.py)
  checks each one on a poll (6h default) via real web search, using the
  same NONE-escape-hatch pattern as proactive context awareness so staying
  quiet is the default outcome, not a guaranteed digest every cycle. Each
  check is told what it last said (last_digest, persisted in a new
  research_topics table) so it's judging "is this genuinely new since
  then," not repeating a static summary. Deliberately runs against an
  EMPTY tool registry (web_search only, via the Anthropic client's
  always-on web search tool) rather than the orchestrator's real
  registry -- this runs unattended, and the real registry includes
  CONFIRM-tier tools that would pop an unprompted confirmation card if the
  model ever decided a digest check warranted one. Live-verified against
  the real Anthropic API: asked it to check "Anthropic Claude model
  releases" with no prior context, got back a real, dated, genuinely
  current digest (found a specific recent release), real spend recorded.
- "Teach me once" macros -- walk it through a multi-step task manually one
  time and it generalizes that into a replayable skill, instead of you
  re-describing the same task every time.
- ~~Ambient stuck-detection -- with desktop visibility already in place, it
  could notice you've been stuck on the same error for a while and offer
  help unprompted, rather than only ever reacting to a direct ask.~~ Done:
  StuckDetectionWorker (argus/stuck_detection.py) -- its own worker rather
  than folded into proactive context awareness, since it needs a much
  shorter fuse (minutes, not the 2-hour idle threshold context awareness
  uses) and looks at actual screen content via a real screenshot + vision
  call (complete_with_image), not just the window title. Same
  NONE-escape-hatch pattern: only assesses once the same window has been
  active past a threshold (8 min default), offers at most once per
  continuous stretch in that window (switching away and back resets it),
  and stays quiet unless there's a clearly visible sign of being stuck
  (an error, a stack trace, a blocked state). Live-verified against the
  real Anthropic API with a real desktop screenshot -- caught and fixed a
  real bug in the process: the screenshot is PNG (pyautogui) but
  complete_with_image defaults to image/jpeg, which the Anthropic API
  flatly rejects on a mismatch; a live round-trip surfaced the 400 before
  it ever reached the user, fixed by passing media_type explicitly.
- ~~"Second opinion" mode for big decisions -- reason from a few angles
  (skeptic, domain expert, risk-focused) internally before giving one
  synthesized recommendation on something consequential.~~ Done: the
  `second_opinion` tool (argus/tools/second_opinion.py) runs three
  independent frontier-tier calls, each reasoning from its own angle
  without seeing the others, then a fourth call synthesizes them into one
  recommendation that names the real tradeoff rather than averaging the
  takes together. Shares the orchestrator's real ModelRouter (and its cost
  governor/daily cap) rather than spinning up a second one that would
  silently bypass the spend cap -- only registered when a router is
  actually supplied to build_default_registry. System prompt scopes it to
  genuinely consequential decisions, not routine questions, since it's
  ~4x the cost of a normal reply. Live-verified against the real Anthropic
  API end to end: a real open-source-timing question in, a coherent
  synthesized recommendation out, real spend correctly recorded on the
  shared cost governor.
- Cross-device handoff -- once remote/mobile access exists, a conversation
  started on your phone continues seamlessly when you sit at this machine.

### Later / not yet scoped

- ~~Smarter conversational listening: after the wake word, distinguish speech
  actually directed at Argus from ambient conversation/background noise,
  rather than relying on a fixed follow-up timeout.~~ The addressee-judgment
  half was already done (`_seems_addressed_to_argus`, heuristic + local-LLM
  fallback, gates every follow-up-window utterance). Confirmed against a
  real precedent (a family member's own similar assistant project) that
  this is genuinely the same pattern other real implementations converge
  on -- no further work needed here specifically.
- ~~Custom "Argus" wake-word model (currently using openWakeWord's bundled
  hey_jarvis_v0.1 as a placeholder) -- deferred; real chunk of work
  (synthetic training data, negative-audio dataset, slow CPU training).~~
  Solved differently, and better-fit to the actual constraint that
  mattered: no continued external API calls monitoring for the wake word,
  ever, not even a cheap one. `WAKE_WORD_ENGINE=local` (new default --
  argus/voice/local_wake_word.py) runs Silero VAD continuously (already a
  dependency for barge-in, ~0.5ms/chunk on this hardware -- effectively
  free) to notice real speech, and only then runs *local* faster-whisper
  (never Groq) on that clip and regex-matches "argus"/"argos"/"arcus"
  (common mishearings) as a whole word. Zero training, zero downloads
  beyond what Whisper needed anyway, zero ongoing cost or cloud exposure
  while idle. Trade-off, stated plainly: a beat of transcribe-then-match
  latency vs. a streaming classifier's near-instant per-frame score --
  accepted deliberately once the cost/privacy profile of the alternatives
  (custom training, or continuous cloud STT) were weighed against it.
  A real side benefit, not just a workaround: since the wake word is
  detected by transcribing the WHOLE utterance it was spoken in, "Argus,
  what time is it" arrives as one already-transcribed clip -- no separate
  command-recording phase needed when the command's said in the same
  breath as the wake word (see VoiceLoop.run's wake_command_text
  passthrough). The openWakeWord path is kept intact and selectable
  (`WAKE_WORD_ENGINE=openwakeword`) for lower latency if a real "argus"
  model ever does get trained later. Non-hot-mic barge-in (interrupting
  Argus mid-reply outside the post-interaction grace window) needed its
  own fallback under the local engine, since there's no streaming
  classifier to reuse there either -- approximated with the same RMS+VAD
  gate hot-mic mode already used, just a stricter hold requirement.
  Live-verified end to end on real synthesized speech ("Argus, what time
  is it," via Windows SAPI TTS, not a mock): Silero VAD correctly flagged
  it as speech and true silence as not; the real local Whisper model
  transcribed it exactly, the wake pattern matched, and "What time is it?"
  was correctly extracted as the command.
- ~~Investigate why time-to-first-sentence in streaming mode is slower than
  expected (~13s in one live test) -- likely system-prompt size or API
  latency, not a code regression, but worth profiling separately.~~
  Investigated and fixed the real contributor: SYSTEM_PROMPT had grown to
  ~3400 tokens (41 tools' worth of schemas on top) by this point in the
  roadmap, resent in full on every single turn with zero reuse -- that's
  real input-token processing time paid fresh each call, not a fixed
  cost. Added Anthropic prompt caching (argus/llm/anthropic_client.py:
  `_system_param`/`_cached_tools`), but split deliberately: the static
  instructions (SYSTEM_PROMPT) get their own cache_control breakpoint,
  while the genuinely per-turn-varying part (current time down to the
  minute, recalled memory context) stays uncached in a separate block --
  caching requires an exact prefix match, so concatenating them back into
  one string the old way would mean the dynamic suffix's constant change
  breaks the cache on literally every call. A second breakpoint covers the
  tool definitions array (also identical across turns, also real bulk).
  Orchestrator._build_system split into a static/dynamic pair
  (_build_dynamic_system) to thread this through; ModelRouter and
  AnthropicClient's complete_with_tools/complete_with_tools_streaming
  gained a cacheable_system param. Live-verified against the real
  Anthropic API twice: once with synthetic text isolating the mechanism
  (call 1: cache_creation_input_tokens=4526, cache_read=0; call 2 with a
  DIFFERENT dynamic suffix: cache_creation=0, cache_read=4526 -- proof the
  split actually works, not just that caching is turned on), and once at
  real production scale with the actual SYSTEM_PROMPT + all 41 real tools,
  where the second call's recorded spend was a small fraction of the
  first's, consistent with a real cache hit discount.
