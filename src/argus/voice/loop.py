import logging
import queue
import re
import threading
import time

import numpy as np
import sounddevice as sd
from rich.console import Console

from argus.config import settings
from argus.orchestrator import Orchestrator
from argus.voice.audio_io import ListeningPaused, record_followup
from argus.voice.speaker_factory import build_speaker
from argus.voice.stt import Transcriber
from argus.voice.wake_word import WakeWordListener
from argus.ui import commands as ui_commands
from argus.ui import events as ui_events

log = logging.getLogger(__name__)
console = Console()

_STOP_LISTENING_PHRASES = ("stop listening", "stop barging in", "quit interrupting")
_QUIET_MODE_ON_PHRASES = ("quiet mode", "go quiet", "stop talking out loud", "mute yourself", "text only")
_QUIET_MODE_OFF_PHRASES = ("unmute", "you can talk again", "quiet mode off", "talk out loud again")
_SUPPRESS_CONTEXT_PHRASES = ("don't ask me about this", "don't bring that up", "stop asking about this")
_PROACTIVE_CONTEXT_OFF_PHRASES = ("stop watching my screen", "proactive mode off", "stop checking in")
_PROACTIVE_CONTEXT_ON_PHRASES = ("proactive mode on", "start checking in", "you can check in again")
_JOURNAL_TRIGGER = re.compile(
    r"^(note to self|journal this|take a note|log this thought)\s*[:,-]?\s*(.*)$", re.IGNORECASE
)
_EMAIL_CHECK_PHRASES = ("check my email", "check my mail", "any new email", "any new mail")
_EMAIL_WATCH_OFF_PHRASES = ("stop checking my email", "stop watching my email", "email watch off")
_EMAIL_WATCH_ON_PHRASES = ("check my email again", "start checking my email", "email watch on")
_BARGE_IN_HOLD_FRAMES = 3  # ~240ms of sustained energy (at 80ms/chunk) before hot-mic barge-in fires
# Local wake-word engine has no streaming per-frame classifier to reuse for
# non-hot-mic barge-in (it only ever knows the wake word after transcribing
# a full utterance -- see LocalWakeWordListener). Approximates the same
# intent as the openWakeWord score threshold (outside the hot-mic grace
# window, only sustained deliberate speech interrupts) with RMS+VAD and a
# longer hold than hot-mic's, since there's no wake-word-quality gate here.
_NON_HOT_MIC_LOCAL_BARGE_IN_HOLD_FRAMES = 6  # ~480ms
# How long _speak_with_barge_in waits for the barge-in watcher thread to
# exit on its own after playback finishes, before giving up on it and
# returning anyway. Confirmed live as a real bug: the watcher used to run
# synchronously, so a stall in its own blocking mic read (no timeout on
# sd.InputStream.read()) could hang the entire turn -- and the visible
# "speaking" state with it -- forever, even with audio long since done.
_BARGE_IN_WATCHER_GRACE_SECONDS = 5.0
_LIKELY_ADDRESSED = re.compile(
    r"^(argus|hey\s+argus|can|could|would|will|are|do|does|did|is|"
    r"what|why|how|when|where|who|please)\b",
    re.IGNORECASE,
)


class _BargeInInterrupt(Exception):
    """Raised from the streaming callback to unwind out of the generation
    call early once barge-in has fired -- otherwise the model keeps
    generating (and we keep paying for it) after the user already
    interrupted."""


class VoiceLoop:
    """wake word -> record -> transcribe -> streamed orchestrator reply ->
    sentence-by-sentence TTS, with everything also printed to the screen
    (spoken output is never the only output, per the original requirement).

    Streaming: replies are spoken sentence-by-sentence as they're generated
    rather than waiting for the full response, so time-to-first-sound is
    close to one sentence's worth of generation instead of the whole reply.

    Conversational follow-ups: after Argus finishes speaking, it keeps
    listening (no wake word needed) for settings.followup_window_seconds --
    if you keep talking, the conversation continues hands-free; go quiet
    for that window and it drops back to requiring the wake word.

    Barge-in: while a sentence plays, Argus listens in the background for an
    interruption. Two tiers:
    - Hot mic (settings.open_barge_in_seconds after the wake word, refreshed
      by activity): plain volume detection -- just start talking, no wake
      word needed. Higher self-feedback risk on open speakers since it's
      not checking for anything specific, just loudness.
    - Otherwise: the wake-word model listening for "hey jarvis" again, which
      is far less prone to false-triggering on Argus's own voice since it
      needs to phonetically match.
    Say "stop listening" to end the hot-mic window early."""

    @staticmethod
    def _build_wake_word_listener():
        """settings.wake_word_engine picks between the two wake-word
        implementations: "local" (default) needs no trained model and
        makes zero ongoing API calls (see LocalWakeWordListener),
        "openwakeword" uses the trained-classifier path (lower latency,
        needs openwakeword_model_name to actually be an "argus" model
        once/if one gets trained -- currently still the bundled
        hey_jarvis placeholder)."""
        if settings.wake_word_engine == "local":
            from argus.voice.local_wake_word import LocalWakeWordListener
            return LocalWakeWordListener()
        return WakeWordListener()

    def __init__(self, orchestrator: Orchestrator | None = None):
        console.print("[dim]Loading voice models (wake word, STT, TTS)...[/dim]")
        self.orchestrator = orchestrator or Orchestrator()
        self.wake_word = self._build_wake_word_listener()
        self.transcriber = Transcriber()
        self.speaker = build_speaker()
        from argus.voice.speech_detector import SpeechDetector
        self.speech_detector = SpeechDetector()
        self._hot_mic_until = 0.0
        # Held for the full duration of processing one utterance (voice,
        # typed, or push-to-talk) so two never run concurrently against the
        # same speaker/mic/orchestrator state.
        self._interaction_lock = threading.Lock()

        # Without this, a CONFIRM-tier tool call mid-task silently blocks on
        # a keyboard input() with no spoken prompt -- indistinguishable from
        # a hang if you're listening for a voice response, not watching the
        # terminal.
        from argus.voice.confirm import make_voice_confirmer
        self.orchestrator.tools.confirmer = make_voice_confirmer(self.speaker, self.transcriber)

        # Text input and push-to-talk both come from the console, not the
        # wake-word mic loop -- handled on their own thread so they aren't
        # blocked behind the main loop's wait for the wake word.
        threading.Thread(target=self._external_input_worker, daemon=True).start()
        threading.Thread(target=self._reminder_checker_worker, daemon=True).start()

        from argus.context_awareness import ContextAwarenessWorker
        self.context_awareness = ContextAwarenessWorker(
            self.orchestrator, self._speak_and_open_mic, self._interaction_lock
        )
        threading.Thread(target=self.context_awareness.run, daemon=True).start()

        from argus.email_watcher import EmailWatcher
        self.email_watcher = EmailWatcher(
            self.orchestrator, self._speak_and_open_mic, self._interaction_lock
        )
        threading.Thread(target=self.email_watcher.run, daemon=True).start()

        from argus.routine_worker import RoutineWorker
        self.routine_worker = RoutineWorker(
            self.orchestrator, self._speak_and_open_mic, self._interaction_lock
        )
        threading.Thread(target=self.routine_worker.run, daemon=True).start()

        from argus.knowledge_watcher import KnowledgeWatcher
        self.knowledge_watcher = KnowledgeWatcher(self._speak_and_open_mic, self._interaction_lock)
        threading.Thread(target=self.knowledge_watcher.run, daemon=True).start()

        from argus.research_digest import ResearchDigestWorker
        self.research_digest = ResearchDigestWorker(
            self.orchestrator.router, self._speak_and_open_mic, self._interaction_lock
        )
        threading.Thread(target=self.research_digest.run, daemon=True).start()

        from argus.stuck_detection import StuckDetectionWorker
        self.stuck_detection = StuckDetectionWorker(
            self.orchestrator.router, self._speak_and_open_mic, self._interaction_lock
        )
        threading.Thread(target=self.stuck_detection.run, daemon=True).start()

        from argus.memory.consolidation_worker import ConsolidationWorker
        self.consolidation_worker = ConsolidationWorker(self.orchestrator.router, self.orchestrator.memory)
        threading.Thread(target=self.consolidation_worker.run, daemon=True).start()

    def _reminder_checker_worker(self) -> None:
        """Reminders are meant to be surfaced proactively, not just answered
        once and forgotten -- polls for due-and-not-yet-announced reminders
        every ~25s and speaks them. Uses a non-blocking lock acquire so it
        never stalls or interrupts an in-progress conversation; a reminder
        that's due while Argus is mid-turn just waits for the next poll
        (it stays un-notified until it's actually announced)."""
        from datetime import datetime

        from argus.memory.reminders import ReminderStore
        from argus.memory.store import get_connection

        while True:
            time.sleep(25)
            conn = get_connection()
            try:
                due = ReminderStore(conn).list_due(datetime.now().astimezone().isoformat())
            finally:
                conn.close()

            for reminder in due:
                if not self._interaction_lock.acquire(blocking=False):
                    break  # busy with something else; retry next poll
                try:
                    self._announce_reminder(reminder)
                finally:
                    self._interaction_lock.release()

    def _announce_reminder(self, reminder) -> None:
        from argus.memory.reminders import ReminderStore
        from argus.memory.store import get_connection

        conn = get_connection()
        try:
            ReminderStore(conn).mark_notified(reminder["id"])
        finally:
            conn.close()

        text = f"Reminder: {reminder['text']}"
        console.print(f"[bold yellow]argus (reminder)>[/bold yellow] {text}")
        ui_events.publish({"type": "caption", "text": text})
        ui_events.publish({"type": "transcript", "role": "argus", "text": text})
        self._speak_and_open_mic(text)  # publishes its own "speaking" state event with real timing

    def _external_input_worker(self) -> None:
        from argus.voice.audio_io import record_while

        while True:
            text = ui_commands.get_text_message(timeout=0.2)
            if text:
                with self._interaction_lock:
                    self._process_utterance(text=text)
                continue
            if ui_commands.is_push_to_talk_active():
                with self._interaction_lock:
                    ui_events.publish({"type": "state", "value": "listening", "mode": "push_to_talk"})
                    chunks_out: list = []
                    stop_watcher = self._start_hearing_watcher(chunks_out)
                    samples = record_while(ui_commands.is_push_to_talk_active, chunks_out=chunks_out)
                    stop_watcher.set()
                    self._process_utterance(samples)

    def _start_hearing_watcher(self, chunks_out: list) -> threading.Event:
        """Runs alongside a recording call, periodically re-transcribing the
        audio captured so far and publishing it as a 'hearing' UI event --
        gives the console a near-real-time caption of what the mic is
        picking up while the user is still talking, rather than only
        showing the transcript once recording finishes. Cheap approximation
        of streaming STT: faster-whisper doesn't do incremental decoding,
        so this just re-runs it on the growing buffer every ~600ms."""
        stop_event = threading.Event()

        def watch() -> None:
            last_len = 0
            while not stop_event.is_set():
                time.sleep(0.6)
                current_len = len(chunks_out)
                # The local wake-word engine's listen loop clears chunks_out
                # between separate (non-matching) utterances now, so length
                # can drop back to 0, not just grow -- treat any CHANGE as
                # worth re-checking, not just growth, or a reset here would
                # leave the caption stuck showing the previous utterance
                # until the new one happened to out-grow the old one.
                if current_len == last_len:
                    continue
                last_len = current_len
                if current_len == 0:
                    continue
                # A snapshot copy, not a direct concatenate of chunks_out --
                # the listener thread can now clear() it concurrently
                # between the length check above and this line (it didn't
                # used to be cleared at all, only appended to), and
                # np.concatenate([]) raises on an empty sequence. list(...)
                # is a single atomic operation under the GIL; concatenating
                # THAT copy is safe even if the original gets cleared right
                # after this line runs.
                snapshot = list(chunks_out)
                if not snapshot:
                    continue
                samples = np.concatenate(snapshot)
                if samples.size < 16000 * 0.5:  # skip until at least ~0.5s captured
                    continue
                try:
                    text = self.transcriber.transcribe(samples)
                except Exception:
                    continue
                if text:
                    ui_events.publish({"type": "hearing", "text": text})

        threading.Thread(target=watch, daemon=True).start()
        return stop_event

    def _refresh_hot_mic(self) -> None:
        if settings.open_barge_in_seconds > 0:
            self._hot_mic_until = time.monotonic() + settings.open_barge_in_seconds

    def _hot_mic_active(self) -> bool:
        if ui_commands.is_listening_paused():
            self._hot_mic_until = 0.0
            return False
        return time.monotonic() < self._hot_mic_until

    def _speak_and_open_mic(self, text: str) -> bool:
        """Wraps _speak_with_barge_in for anything Argus says on its own
        initiative -- proactive context nudges, email alerts, reminders,
        scheduled routines, etc. Confirmed live as a real gap: only the
        normal reply flow (_process_utterance) refreshed the hot-mic
        window, so replying to one of these unprompted messages still
        needed the wake word first -- reported live as Argus "disregarding"
        a direct answer to a question it had just asked. Refreshing the
        window here, and having the wake-word listener honor it (see
        hot_mic_check in LocalWakeWordListener.listen_for_wake_and_command),
        means anything Argus says opens the same hands-free follow-up a
        normal reply does.

        Also records the turn into memory (remember_turn) -- a SEPARATE
        real gap confirmed live: only the normal reply flow did this too,
        so a proactive nudge like "need any help with those settings?"
        was invisible to Argus's own "look back at our conversation"
        recall. When the user answered it, Argus flatly denied ever
        asking -- it genuinely had no record of its own question. Every
        background worker already routes through this one method, so
        recording it here covers all of them without touching each
        worker file individually."""
        self.orchestrator.memory.remember_turn("assistant", text)
        interrupted = self._speak_with_barge_in(text)
        self._refresh_hot_mic()
        return interrupted

    def _wait_while_listening_paused(self) -> None:
        """Blocks here, doing no audio capture or transcription at all,
        for as long as "Stop listening" is active -- this is the real
        enforcement point for a persistent pause (see
        ui_commands.set_listening_paused's docstring for why the old
        one-shot version of this button didn't actually stop anything
        outside the hot-mic window). Ctrl+C while paused propagates
        normally -- the caller already wraps this in the same
        KeyboardInterrupt handling as the rest of the loop."""
        if not ui_commands.is_listening_paused():
            return
        console.print("[dim](listening paused from the console)[/dim]")
        ui_events.publish({"type": "state", "value": "idle", "mode": "paused"})
        while ui_commands.is_listening_paused():
            time.sleep(0.3)
        console.print("[dim](listening resumed)[/dim]")

    def run(self) -> None:
        console.print("[bold cyan]Argus[/bold cyan] listening for wake word. Ctrl+C to quit.\n")
        while True:
            try:
                self._wait_while_listening_paused()
            except KeyboardInterrupt:
                break

            ui_events.publish({"type": "state", "value": "listening", "mode": "wake_word"})
            try:
                def _on_wake():
                    console.print("[green](wake word heard, listening...)[/green]")
                    ui_events.publish({"type": "state", "value": "listening", "mode": "command"})
                    self._refresh_hot_mic()
                    from argus.voice.chime import play_listening_chime
                    play_listening_chime()

                def _on_checking():
                    # The local engine only knows whether it heard the wake
                    # word AFTER transcribing the whole utterance -- a real,
                    # noticeable few seconds on CPU, longer the very first
                    # time (the model loads lazily). Without this, the
                    # console just kept showing "waiting for the wake word"
                    # the entire time real speech was already captured and
                    # being checked -- confirmed live, reported as "it heard
                    # me but isn't doing anything."
                    console.print("[dim](checking what was heard...)[/dim]")
                    ui_events.publish({"type": "state", "value": "listening", "mode": "confirming"})

                wake_chunks: list = []
                via_hot_mic: list = []
                stop_watcher = self._start_hearing_watcher(wake_chunks)
                samples, wake_command_text = self.wake_word.listen_for_wake_and_command(
                    on_wake=_on_wake, chunks_out=wake_chunks, on_checking=_on_checking,
                    hot_mic_check=self._hot_mic_active,
                    should_stop=ui_commands.is_listening_paused,
                    via_hot_mic_out=via_hot_mic,
                )
                stop_watcher.set()
            except KeyboardInterrupt:
                break
            except ListeningPaused:
                # Confirmed live as a real gap: "Stop listening" only took
                # effect between utterance-capture attempts, so the mic
                # kept actively capturing (and transcribing) for however
                # long whatever was already in progress took -- reported
                # as "it needs to basically be a mute input button."
                # should_stop above raised this mid-capture, closing the
                # stream immediately; looping back to the top re-enters
                # _wait_while_listening_paused(), which blocks with no
                # stream open at all until resumed.
                stop_watcher.set()
                continue
            except sd.PortAudioError:
                # Confirmed live as a real crash: a transient audio-device
                # hiccup (Bluetooth mic dropping out, another app grabbing
                # exclusive access, a sleep/resume cycle) raised all the way
                # out of stream.read() and killed the whole process instead
                # of just this one listen attempt. The InputStream context
                # manager inside listen_for_wake_and_command has already
                # closed the broken stream by the time this is caught --
                # opening a fresh one next iteration is enough to recover
                # once the device is available again.
                log.exception("Audio device error while listening for the wake word -- retrying")
                console.print("[red](audio device error -- retrying in a few seconds)[/red]\n")
                time.sleep(3.0)
                continue

            # A genuine wake-word match is always explicit intent -- no
            # addressee check needed. But a hot-mic-window capture (see
            # via_hot_mic_out's docstring on LocalWakeWordListener) is
            # exactly as likely to be background noise/another
            # conversation as a normal follow-up-window utterance is, so
            # it needs the SAME addressee gate a follow-up gets -- caused
            # a real incident otherwise: background video audio during an
            # open hot-mic window got treated as a direct question and
            # replied to.
            check_addressee = bool(via_hot_mic)
            # wake_command_text is only ever set by the local engine (see
            # LocalWakeWordListener) -- it means the command was already
            # said in the SAME breath as the wake word ("Argus, what time
            # is it") and already transcribed locally as part of detecting
            # the wake word itself, so pass it straight through instead of
            # transcribing samples all over again (that second pass would
            # otherwise hit Groq, defeating the entire point of the local
            # engine).
            with self._interaction_lock:
                if wake_command_text:
                    processed = self._process_utterance(text=wake_command_text, check_addressee=check_addressee)
                else:
                    processed = self._process_utterance(samples, check_addressee=check_addressee)
            if not processed:
                console.print("[dim](heard nothing, back to listening)[/dim]\n")
                continue

            # Conversational follow-ups: keep listening without the wake
            # word until the user goes quiet for the timeout window. Unlike
            # the wake-word command, anything heard here could be genuine
            # follow-up OR the open mic picking up unrelated conversation --
            # gate it.
            while True:
                if ui_commands.is_listening_paused():
                    console.print("[dim](listening paused -- back to wake-word listening)[/dim]\n")
                    break
                console.print(f"[dim](listening for follow-up, {settings.followup_window_seconds:.0f}s...)[/dim]")
                ui_events.publish({
                    "type": "state", "value": "listening", "mode": "follow_up",
                    "window_seconds": settings.followup_window_seconds,
                })
                followup_chunks: list = []
                stop_watcher = self._start_hearing_watcher(followup_chunks)
                try:
                    followup = record_followup(
                        settings.followup_window_seconds, chunks_out=followup_chunks,
                        should_stop=ui_commands.is_listening_paused,
                    )
                except ListeningPaused:
                    stop_watcher.set()
                    console.print("[dim](listening paused mid-follow-up -- back to wake-word listening)[/dim]\n")
                    break
                except sd.PortAudioError:
                    # Same transient audio-device recovery as the wake-word
                    # listen above -- don't let it crash the whole process.
                    stop_watcher.set()
                    log.exception("Audio device error during follow-up listening -- retrying")
                    console.print("[red](audio device error -- retrying in a few seconds)[/red]\n")
                    time.sleep(3.0)
                    break
                stop_watcher.set()
                if followup is None:
                    console.print("[dim](back to wake-word listening)[/dim]\n")
                    break
                with self._interaction_lock:
                    kept_going = self._process_utterance(followup, check_addressee=True)
                if not kept_going:
                    console.print("[dim](back to wake-word listening)[/dim]\n")
                    break

    @staticmethod
    def _loudness_hint(samples) -> str | None:
        """Quieter audio is genuinely more likely to be distant background
        conversation/TV than a direct request -- confirmed live as a real
        missing signal: the addressee gate previously looked at the
        transcribed TEXT only, never how loud/close the utterance actually
        was. Peak RMS relative to the same silence floor already used for
        VAD gating (settings.voice_silence_rms_threshold) as a cheap,
        already-available proxy for distance/directness -- not a real
        source-localization signal, just "was this said right at the mic
        or picked up faintly from across the room." Returns None (no
        opinion) when there's nothing to measure."""
        if samples is None or samples.size == 0:
            return None
        peak_rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        floor = settings.voice_silence_rms_threshold
        if floor <= 0:
            return None
        ratio = peak_rms / floor
        if ratio < 2.0:
            return "quiet -- said at a volume close to background noise, more likely distant/incidental"
        if ratio < 4.0:
            return "moderate volume -- not unusually quiet, but not a clear close, direct voice either"
        return "clear, close volume -- consistent with speaking directly at the mic"

    def _seems_addressed_to_argus(self, text: str, samples=None) -> bool:
        """Cheap gate for follow-up-window utterances: is this actually
        meant for Argus, or is the open mic picking up stray conversation
        (e.g. talking to someone else in the room)? Real addressee
        detection is a harder, deferred problem (see README roadmap); this
        is a cheap first line of defense, not that.

        A clean question mark or a direct-address opener ("can you...",
        "argus...") is caught here without even asking the model -- live
        testing showed the local 3B classifier dropping obviously-addressed
        questions ("Can you still hear me?") as STRAY, which is far more
        disruptive than the rare bit of stray chatter it's meant to filter.
        Deliberately NOT gated on loudness -- a clear direct question asked
        quietly on purpose should still pass immediately. Only genuinely
        ambiguous statements (no "?", no direct opener) fall through to the
        model, WITH a loudness hint (see _loudness_hint) folded into its
        prompt as one more signal alongside the words themselves. Its own
        prompt is still biased toward ADDRESSED when uncertain, since
        wrongly continuing to listen for one more turn costs nothing but
        wrongly dropping a real question breaks the conversation."""
        if "?" in text or _LIKELY_ADDRESSED.match(text.strip()):
            return True

        from argus.llm.base import Message

        loudness_hint = self._loudness_hint(samples)
        loudness_line = f"Volume: {loudness_hint}.\n" if loudness_hint else ""

        try:
            prompt = (
                "Is the following something a person would say TO an AI "
                "voice assistant -- a question, request, or command -- or "
                "is it more likely stray conversation/background talk not "
                f'meant for the assistant?\nUtterance: "{text}"\n'
                f"{loudness_line}"
                "If genuinely uncertain, answer ADDRESSED -- only answer "
                "STRAY when it clearly sounds like a comment to someone "
                "else in the room (uses another person's name, or reads as "
                "one half of a conversation not involving the assistant), "
                "an unrelated snippet like TV/media audio, or (when given) "
                "a quiet/distant volume combined with otherwise ambiguous "
                "wording.\n"
                "Reply with exactly one word: ADDRESSED or STRAY."
            )
            result = self.orchestrator.router.local.complete([Message(role="user", content=prompt)])
            return "STRAY" not in result.text.upper()
        except Exception:
            log.exception("Addressee check failed; treating utterance as addressed")
            return True  # fail open -- never silently drop a real request

    def _process_utterance(self, samples=None, text: str | None = None, check_addressee: bool = False) -> bool:
        """Transcribes (unless text is already given -- typed input skips
        STT entirely), then streams the reply through the orchestrator,
        speaking each sentence as it's generated. Returns False only when
        there's genuinely nothing to keep the follow-up window open for
        (empty/silence) -- an ignored stray utterance still returns True,
        since the user may well resume talking to Argus a moment later and
        the conversation shouldn't end just because of one aside to someone
        else in the room."""
        if text is None:
            # Whisper is well-documented to hallucinate short boilerplate
            # phrases ("thank you", "thanks for watching", "bye") when fed
            # near-silent or ambient audio -- confirmed live as a real,
            # self-sustaining bug: the follow-up window's RMS-threshold
            # gate can be permissive enough to pass through Argus's own
            # TTS bleeding faintly back into the mic (or plain room noise)
            # right after he finishes talking, Whisper hallucinates "thank
            # you" from that near-silence, the addressee gate's own
            # documented "when uncertain, assume addressed" bias lets it
            # through, Argus replies "you're welcome," and THAT reply's
            # echo restarts the same cycle -- observed live running for
            # over a dozen exchanges unattended. A real, stricter check
            # here -- Silero VAD on the actual captured audio, not just an
            # RMS threshold -- rejects it before Whisper ever sees it,
            # closing off the hallucination vector at its source rather
            # than trying to filter transcribed text after the fact.
            if samples is not None and samples.size > 0 and not self.speech_detector.is_speech(samples):
                return False
            text = self.transcriber.transcribe(samples)
        if not text:
            return False

        if check_addressee and not self._seems_addressed_to_argus(text, samples=samples):
            console.print(f"[dim]you (ignored, seems unaddressed)> {text}[/dim]\n")
            # Confirmed live as a real, recurring complaint -- "struggling
            # to figure out when I'm talking to him and when I'm not."
            # Nothing previously recorded WHICH utterances got silently
            # dropped by the addressee gate or why -- this makes every
            # drop reviewable after the fact (see ui/events.py's event
            # log), not just guessable from console scrollback that's
            # long gone by the time it's reported.
            ui_events.publish({
                "type": "addressee_gate", "verdict": "stray", "text": text,
                "loudness": self._loudness_hint(samples),
            })
            return True  # keep the follow-up window open, just ignore this one

        console.print(f"[bold green]you>[/bold green] {text}")

        if any(phrase in text.lower() for phrase in _STOP_LISTENING_PHRASES):
            self._hot_mic_until = 0.0
            console.print("[dim](hot mic off -- wake word needed to interrupt again)[/dim]\n")
            return True

        lowered = text.lower()
        if not ui_commands.is_quiet_mode() and any(phrase in lowered for phrase in _QUIET_MODE_ON_PHRASES):
            ui_commands.set_quiet_mode(True)
            console.print("[dim](quiet mode on -- replies are text-only until you say otherwise)[/dim]\n")
            ui_events.publish({"type": "transcript", "role": "argus", "text": "Going quiet -- text replies only from here."})
            ui_events.publish({"type": "quiet_mode", "value": True})
            return True
        if ui_commands.is_quiet_mode() and any(phrase in lowered for phrase in _QUIET_MODE_OFF_PHRASES):
            ui_commands.set_quiet_mode(False)
            console.print("[dim](quiet mode off)[/dim]\n")
            ui_events.publish({"type": "quiet_mode", "value": False})
            confirmation = "Back with you -- I can talk again."
            ui_events.publish({"type": "transcript", "role": "argus", "text": confirmation})
            ui_events.publish({"type": "caption", "text": confirmation})
            self._speak_with_barge_in(confirmation)  # now actually audible -- proves quiet mode is really off
            return True

        if any(phrase in lowered for phrase in _SUPPRESS_CONTEXT_PHRASES):
            self.context_awareness.suppress_current()
            console.print("[dim](won't bring that up again for this window)[/dim]\n")
            confirmation = "Got it, I won't bring that up again."
            ui_events.publish({"type": "transcript", "role": "argus", "text": confirmation})
            ui_events.publish({"type": "caption", "text": confirmation})
            self._speak_with_barge_in(confirmation)
            return True
        if any(phrase in lowered for phrase in _PROACTIVE_CONTEXT_OFF_PHRASES):
            ui_commands.set_proactive_context_enabled(False)
            console.print("[dim](proactive check-ins off)[/dim]\n")
            confirmation = "Okay, I'll stay quiet unless you talk to me first."
            ui_events.publish({"type": "transcript", "role": "argus", "text": confirmation})
            ui_events.publish({"type": "caption", "text": confirmation})
            self._speak_with_barge_in(confirmation)
            return True
        if any(phrase in lowered for phrase in _PROACTIVE_CONTEXT_ON_PHRASES):
            ui_commands.set_proactive_context_enabled(True)
            console.print("[dim](proactive check-ins on)[/dim]\n")
            confirmation = "Will do -- I'll speak up if something seems worth mentioning."
            ui_events.publish({"type": "transcript", "role": "argus", "text": confirmation})
            ui_events.publish({"type": "caption", "text": confirmation})
            self._speak_with_barge_in(confirmation)
            return True

        journal_match = _JOURNAL_TRIGGER.match(text.strip())
        if journal_match:
            return self._handle_journal_trigger(journal_match.group(2).strip())

        if any(phrase in lowered for phrase in _EMAIL_CHECK_PHRASES):
            console.print("[dim](checking email...)[/dim]\n")
            confirmation = "Checking now -- I'll let you know if anything looks important."
            ui_events.publish({"type": "transcript", "role": "argus", "text": confirmation})
            ui_events.publish({"type": "caption", "text": confirmation})
            self._speak_with_barge_in(confirmation)
            # Runs in the background -- IMAP round-trips can take a few
            # seconds, no reason to hold the conversation open for it.
            threading.Thread(target=self.email_watcher.check_now, daemon=True).start()
            return True
        if any(phrase in lowered for phrase in _EMAIL_WATCH_OFF_PHRASES):
            ui_commands.set_email_watch_enabled(False)
            console.print("[dim](email watch off)[/dim]\n")
            confirmation = "Okay, I'll stop checking your email."
            ui_events.publish({"type": "transcript", "role": "argus", "text": confirmation})
            ui_events.publish({"type": "caption", "text": confirmation})
            self._speak_with_barge_in(confirmation)
            return True
        if any(phrase in lowered for phrase in _EMAIL_WATCH_ON_PHRASES):
            ui_commands.set_email_watch_enabled(True)
            console.print("[dim](email watch on)[/dim]\n")
            confirmation = "Will do -- I'll keep an eye on your email again."
            ui_events.publish({"type": "transcript", "role": "argus", "text": confirmation})
            ui_events.publish({"type": "caption", "text": confirmation})
            self._speak_with_barge_in(confirmation)
            return True

        self._refresh_hot_mic()

        sentence_queue: queue.Queue[str | None] = queue.Queue()
        interrupted = threading.Event()
        pending_unspoken: list[str] = []

        def consumer() -> None:
            while True:
                sentence = sentence_queue.get()
                if sentence is None:
                    return
                console.print(f"[bold cyan]argus>[/bold cyan] {sentence}")
                ui_events.publish({"type": "caption", "text": sentence})
                if self._speak_with_barge_in(sentence):
                    interrupted.set()
                    # This sentence, plus anything the model had already
                    # generated but this thread hasn't spoken yet, is real
                    # content we don't want to just throw away -- a false
                    # barge-in shouldn't mean the rest of the answer never
                    # gets said. See _resume_after_interruption below.
                    pending_unspoken.append(sentence)
                    while True:
                        try:
                            queued = sentence_queue.get_nowait()
                        except queue.Empty:
                            break
                        if queued is not None:
                            pending_unspoken.append(queued)
                    return

        consumer_thread = threading.Thread(target=consumer)
        consumer_thread.start()

        def on_sentence(sentence: str) -> None:
            if interrupted.is_set():
                raise _BargeInInterrupt()
            sentence_queue.put(sentence)

        try:
            self.orchestrator.handle_streaming(text, on_sentence=on_sentence)
        except _BargeInInterrupt:
            pass
        except Exception:
            # Previously uncaught here -- confirmed live that a single bad
            # API response (e.g. a tool-result content-type mismatch) took
            # down the entire `argus voice` process, not just that one
            # turn. A turn failing is recoverable; the whole session dying
            # because of it is not -- report it and keep listening instead.
            log.exception("Turn failed unexpectedly: %r", text)
            error_note = "Something went wrong on that one -- mind trying again?"
            ui_events.publish({"type": "transcript", "role": "argus", "text": error_note})
            ui_events.publish({"type": "caption", "text": error_note})
            self._speak_with_barge_in(error_note)
        finally:
            sentence_queue.put(None)
            consumer_thread.join()

        if self.orchestrator.last_tier is not None:
            tag = f"[dim]({self.orchestrator.last_tier.value}: {self.orchestrator.last_model})[/dim]"
            console.print(f"{tag}\n")

        if pending_unspoken:
            return self._resume_after_interruption(pending_unspoken)
        return True

    def _speak_sentences(self, sentences: list[str]) -> list[str]:
        """Speaks each sentence via the barge-in-aware player in order.
        Returns the sentences (the interrupted one plus anything after it,
        each in full -- no partial-sentence tracking) that were NOT spoken
        because playback was interrupted again partway through; empty if
        every sentence played to completion."""
        for i, sentence in enumerate(sentences):
            console.print(f"[bold cyan]argus>[/bold cyan] {sentence}")
            ui_events.publish({"type": "caption", "text": sentence})
            if self._speak_with_barge_in(sentence):
                return sentences[i:]
        return []

    def _resume_after_interruption(self, pending: list[str], depth: int = 0) -> bool:
        """A barge-in just cut Argus off mid-reply. Rather than assuming
        that was a real interruption and silently dropping the rest of the
        answer (the reported bug: "when there's no input to process, he
        just stops"), do one short listen first. If something real was
        actually said, handle it normally. If it was a false trigger --
        background noise, a click, nothing transcribable -- pick back up
        where playback left off instead of leaving the reply unfinished."""
        if depth > 4:  # pathological repeated false-triggers; stop retrying
            return True

        ui_events.publish({"type": "state", "value": "listening", "mode": "confirming"})
        console.print("[dim](confirming interruption...)[/dim]")
        chunks_out: list = []
        stop_watcher = self._start_hearing_watcher(chunks_out)
        heard = record_followup(min(settings.followup_window_seconds, 3.0), chunks_out=chunks_out)
        stop_watcher.set()

        if heard is not None:
            text = self.transcriber.transcribe(heard)
            if text and self._seems_addressed_to_argus(text):
                return self._process_utterance(text=text, check_addressee=False)

        console.print("[dim](no real interruption heard -- resuming)[/dim]")
        remaining = self._speak_sentences(pending)
        if remaining:
            return self._resume_after_interruption(remaining, depth=depth + 1)
        if self.orchestrator.last_tier is not None:
            tag = f"[dim]({self.orchestrator.last_tier.value}: {self.orchestrator.last_model})[/dim]"
            console.print(f"{tag}\n")
        return True

    def _handle_journal_trigger(self, inline_content: str) -> bool:
        """"Note to self" / "journal this" / "take a note", optionally with
        the entry inline in the same breath ("note to self: call the
        dentist tomorrow"). Bypasses the LLM entirely -- this is a
        deterministic capture-and-confirm, not something that needs
        reasoning, and skipping the model keeps it fast and free. If the
        trigger phrase was said alone, does one extra listen for the
        entry rather than requiring it be crammed into one breath."""
        if inline_content:
            self._save_journal_entry(inline_content)
            return True

        console.print("[dim](listening for your journal entry...)[/dim]")
        prompt_text = "Go ahead, I'm listening."
        ui_events.publish({"type": "transcript", "role": "argus", "text": prompt_text})
        ui_events.publish({"type": "caption", "text": prompt_text})
        self._speak_with_barge_in(prompt_text)

        entry_audio = record_followup(20.0)
        entry_text = self.transcriber.transcribe(entry_audio) if entry_audio is not None else ""
        if entry_text:
            self._save_journal_entry(entry_text)
        else:
            console.print("[dim](didn't catch a journal entry)[/dim]\n")
            confirmation = "Didn't catch that -- say 'note to self' again whenever you're ready."
            ui_events.publish({"type": "transcript", "role": "argus", "text": confirmation})
            ui_events.publish({"type": "caption", "text": confirmation})
            self._speak_with_barge_in(confirmation)
        return True

    def _save_journal_entry(self, text: str) -> None:
        from argus.memory.journal import JournalStore
        from argus.memory.store import get_connection

        conn = get_connection()
        try:
            JournalStore(conn).add(text)
        finally:
            conn.close()

        console.print(f"[dim](journal entry saved: {text})[/dim]\n")
        confirmation = "Got it, logged."
        ui_events.publish({"type": "transcript", "role": "argus", "text": confirmation})
        ui_events.publish({"type": "caption", "text": confirmation})
        self._speak_with_barge_in(confirmation)

    def _speak_with_barge_in(self, text: str) -> bool:
        """Synthesizes and plays one sentence. Returns True if barge-in
        interrupted it."""
        if ui_commands.is_quiet_mode():
            # Quiet mode: the caller already published the caption/
            # transcript text before calling this, so the reply is fully
            # visible in the console/Telegram either way -- just skip the
            # actual audio synthesis and playback. Nothing played, so
            # barge-in can't have interrupted anything.
            return False

        # Synthesize BEFORE starting the watcher -- Piper's synthesis is
        # itself onnxruntime compute, and letting it overlap with the
        # watcher's continuous wake-word inference was starving the CPU on
        # this hardware badly enough to produce long silences.
        visemes = None
        try:
            with_visemes = self.speaker.synthesize_with_visemes(text)
        except Exception:
            log.exception("Viseme synthesis failed; falling back to plain synthesis")
            with_visemes = None

        if with_visemes is not None:
            samples, sample_rate, visemes = with_visemes
        else:
            try:
                synthesized = self.speaker.synthesize(text)
            except Exception:
                log.exception("Speech synthesis failed")
                console.print("[red](speech synthesis failed -- see log)[/red]")
                return False
            if synthesized is None:
                return False
            samples, sample_rate = synthesized

        # Publish state + real mouth-shape data together, right as playback
        # is about to start. Real IPA phoneme timing (visemes) when the
        # backend supports it (Cartesia); otherwise a plain RMS amplitude
        # envelope so the mouth still moves with the actual audio, just
        # without distinct shapes.
        state_event = {
            "type": "state", "value": "speaking",
            "duration_ms": round(len(samples) / sample_rate * 1000),
        }
        if visemes:
            state_event["visemes"] = visemes
        else:
            from argus.voice.audio_io import compute_envelope
            state_event["envelope"] = compute_envelope(samples, sample_rate)
            state_event["chunk_ms"] = 40
        ui_events.publish(state_event)

        stop_event = threading.Event()

        def _play_safely():
            from argus.voice.audio_io import play_audio
            try:
                play_audio(samples, sample_rate=sample_rate, stop_event=stop_event)
            except Exception:
                log.exception("Speech playback failed")
                console.print("[red](speech playback failed -- see log)[/red]")

        if not settings.voice_barge_in_enabled:
            _play_safely()
            return False

        play_thread = threading.Thread(target=_play_safely)
        play_thread.start()

        # _watch_for_barge_in used to run synchronously right here, which
        # meant this whole call -- and with it, the visible "speaking"
        # state, since nothing downstream ever runs to publish a new one
        # until this returns -- blocked on however long ITS OWN blocking
        # sd.InputStream.read() calls took. That read has no timeout; a
        # mic-side hiccup (a device hiccup, or stream-open/close churn from
        # the local wake-word engine's own separate InputStream cycling)
        # could stall it indefinitely even after playback had genuinely
        # finished, which is exactly the "stuck in speaking mode forever"
        # bug reported live. Running it on its own thread means playback
        # finishing is no longer gated on the watcher also finishing --
        # play_thread.join() below returns as soon as audio is actually
        # done, regardless of what the watcher is doing.
        watcher_result: dict = {"interrupted": False}

        def _watch_safely() -> None:
            try:
                watcher_result["interrupted"] = self._watch_for_barge_in(stop_event, play_thread)
            except Exception:
                log.exception("Barge-in watcher failed; continuing without it")

        watcher_thread = threading.Thread(target=_watch_safely, daemon=True)
        watcher_thread.start()

        play_thread.join()
        # Playback is done -- give the watcher a brief grace period to
        # notice (it polls in ~80ms chunks) and exit cleanly. If it's still
        # not back, something's actually wrong with the input stream
        # specifically, not with playback -- log it and move on rather
        # than hanging this whole turn (and the visible "speaking" state)
        # forever. daemon=True means an abandoned watcher can't block
        # process exit either.
        watcher_thread.join(timeout=_BARGE_IN_WATCHER_GRACE_SECONDS)
        if watcher_thread.is_alive():
            log.warning("Barge-in watcher didn't exit after playback finished -- abandoning it for this turn")

        interrupted = watcher_result["interrupted"]
        if interrupted:
            self._refresh_hot_mic()
        return interrupted

    def _watch_for_barge_in(self, stop_event: threading.Event, play_thread: threading.Thread) -> bool:
        import numpy as np
        import sounddevice as sd

        hot_mic = self._hot_mic_active()
        # The local wake-word engine has no per-frame classifier at all
        # (see LocalWakeWordListener) -- it only ever knows the wake word
        # after transcribing a whole utterance, far too slow to drive
        # frame-by-frame barge-in. Falls back to the same RMS+VAD approach
        # hot-mic mode already uses, just with a stricter hold requirement
        # (see _NON_HOT_MIC_LOCAL_BARGE_IN_HOLD_FRAMES) standing in for the
        # "must sound wake-word-quality" gate openWakeWord's score provided.
        using_local_engine = settings.wake_word_engine == "local"

        # Without this, leftover prediction state from the wake-word model's
        # (or the VAD's own recurrent state's) last real detection can
        # cause an immediate spurious trigger here.
        self.wake_word.reset()
        if hot_mic or using_local_engine:
            self.speech_detector.reset()

        # openWakeWord's embedding model scores over a rolling ~1.3s window.
        # Right after reset() that buffer is mostly empty, and scoring
        # against it produced sharp spurious near-1.0 triggers within the
        # first 1-2 frames in testing -- an artifact of the cold buffer, not
        # real audio. Feed the model during warm-up without acting on scores
        # (skip entirely in hot-mic mode, or when there's no such model to
        # warm up at all).
        warmup_chunks = 0 if (hot_mic or using_local_engine) else 16  # ~1.28s at 80ms/chunk
        chunk = 1280
        # A single loud 80ms frame (a cough, a click, speaker/echo bleed) was
        # enough to stop Argus mid-sentence -- reported live as "he keeps
        # hearing background noise and stopping." Require the energy to hold
        # for a few consecutive frames before treating it as real speech;
        # genuine speech onset easily clears this, transient noise doesn't.
        hot_mic_speech_run = 0
        non_hot_mic_speech_run = 0
        with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=chunk) as stream:
            for _ in range(warmup_chunks):
                if not play_thread.is_alive():
                    return False
                frame, _ = stream.read(chunk)
                self.wake_word.score_frame(frame.reshape(-1))

            while play_thread.is_alive():
                frame, _ = stream.read(chunk)
                frame = frame.reshape(-1)

                if hot_mic:
                    rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
                    # RMS gates first (cheap, skips VAD on near-silence);
                    # WebRTC VAD then rejects loud-but-not-speech transients
                    # (coughs, clicks, echo bleed) that RMS alone can't tell
                    # apart from someone actually talking.
                    if rms > settings.voice_silence_rms_threshold and self.speech_detector.is_speech(frame):
                        hot_mic_speech_run += 1
                        if hot_mic_speech_run >= _BARGE_IN_HOLD_FRAMES:
                            console.print("[yellow](barge-in: hot mic heard you)[/yellow]")
                            log.info("hot-mic barge-in triggered at rms=%.1f", rms)
                            stop_event.set()
                            return True
                    else:
                        hot_mic_speech_run = 0
                    continue

                if using_local_engine:
                    rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
                    if rms > settings.voice_silence_rms_threshold and self.speech_detector.is_speech(frame):
                        non_hot_mic_speech_run += 1
                        if non_hot_mic_speech_run >= _NON_HOT_MIC_LOCAL_BARGE_IN_HOLD_FRAMES:
                            console.print("[yellow](barge-in: heard you)[/yellow]")
                            log.info("local-engine barge-in triggered at rms=%.1f", rms)
                            stop_event.set()
                            return True
                    else:
                        non_hot_mic_speech_run = 0
                    continue

                score = self.wake_word.score_frame(frame)
                if score > 0.2:
                    log.info("barge-in watch: score=%.3f", score)
                if score > 0.6:
                    console.print("[yellow](barge-in: stopping speech)[/yellow]")
                    log.info("barge-in triggered at score=%.3f", score)
                    stop_event.set()
                    return True
        return False
