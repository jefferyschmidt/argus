from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    anthropic_api_key: str = ""
    # Optional native speech-to-speech mode. This is intentionally separate
    # from the text/tool pipeline so voice conversation can stay immediate.
    openai_api_key: str = ""
    voice_mode: str = "pipeline"  # "pipeline" or "realtime"
    openai_realtime_model: str = "gpt-realtime-2.1-mini"
    openai_realtime_voice: str = "marin"
    ollama_host: str = "http://localhost:11434"
    ollama_local_model: str = "llama3.2:3b"

    # ROADMAP.md Phase 3: web tasks via structured accessibility data
    # (same principle as list_ui_elements, applied to the browser) instead
    # of screenshot-and-guess desktop control. Off by default -- launching
    # the Playwright MCP server spawns a real Node/browser process and
    # takes a few seconds, cost not worth paying at every Argus startup
    # for a capability most turns never use.
    enable_playwright_mcp: bool = False

    # ROADMAP.md Phase 4: remote hosted MCP servers (streamable HTTP, not
    # a local subprocess). Both off by default (empty url = skipped, same
    # "opt in, never slow down a normal startup" pattern as Playwright
    # above). The URL is account-specific -- generated in each service's
    # own dashboard -- so it's supplied whole rather than guessed/built
    # from a template here.
    #
    # Zapier: paste the MCP server URL from your Zapier account's MCP
    # integration setup. zapier_mcp_api_key is only needed if that URL
    # doesn't already embed its own auth.
    zapier_mcp_url: str = ""
    zapier_mcp_api_key: str = ""
    # Home Assistant: the official "MCP Server" integration exposes this
    # at <your-ha-url>/mcp_server/sse once enabled in Settings ->
    # Integrations -> Add Integration -> MCP Server. Token is a
    # long-lived access token from your HA profile page.
    home_assistant_mcp_url: str = ""
    home_assistant_mcp_token: str = ""

    # ROADMAP.md Phase 5: unlike Zapier/Home Assistant, these two have
    # fixed, documented endpoints (not account-specific dashboard-
    # generated URLs), so only an enable flag (+ a token, for GitHub) is
    # needed -- no URL to paste in.
    #
    # GitHub's official remote MCP server -- issues/PRs/repo management,
    # complementing (not replacing) the existing local self-editing tools.
    # https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/set-up-the-github-mcp-server
    enable_github_mcp: bool = False
    github_mcp_token: str = ""  # a GitHub personal access token
    github_mcp_url: str = "https://api.githubcopilot.com/mcp/"
    # Figma's Dev Mode MCP server -- runs locally inside the Figma desktop
    # app (Dev Mode -> Enable desktop MCP server), bound to 127.0.0.1, no
    # auth needed. Requires the desktop app actually running with a file
    # open in Dev Mode; connection just fails (caught, logged, skipped)
    # if it isn't.
    # https://developers.figma.com/docs/figma-mcp-server/local-server-installation/
    enable_figma_mcp: bool = False
    figma_mcp_url: str = "http://127.0.0.1:3845/mcp"

    # ROADMAP.md Phase 5: Stability AI image generation -- a local stdio
    # server (npx mcp-server-stability-ai), same shape as Playwright, but
    # needs its key via a subprocess ENV VAR, not an HTTP header -- get an
    # API key at https://platform.stability.ai/account/keys.
    enable_stability_mcp: bool = False
    stability_ai_api_key: str = ""

    # ROADMAP.md Phase 6: Spotify playback control -- also a local stdio
    # server (npx @tbrgeek/spotify-mcp-server). Confirmed live: it starts
    # and connects fine with zero credentials configured, exposing only
    # setup/auth-status tools until authenticated -- Argus can walk the
    # user through the rest itself once enabled, no API key needed here.
    enable_spotify_mcp: bool = False

    # Optional -- if set, Groq replaces Ollama in the low-latency "local"
    # slot (small talk, addressee-gate classification): hosted, no cold
    # start, ~300-500 tok/s vs. Ollama's CPU-bound generation on this
    # hardware. Ollama is kept regardless as the genuine offline fallback
    # (see ModelRouter.offline_fallback) since Groq still needs internet.
    groq_api_key: str = ""
    # gpt-oss-20b is a reasoning model -- without reasoning_effort='low' it
    # burns its whole token budget on hidden reasoning and returns empty
    # content (confirmed live: 198/200 tokens on reasoning, 0 on the actual
    # reply). 'low' keeps latency near-instant (~0.3-0.7s measured) while
    # still returning real text -- see GroqClient.complete().
    groq_model: str = "openai/gpt-oss-20b"
    # Hosted Whisper -- when GROQ_API_KEY is set, this replaces local
    # faster-whisper for STT (same CPU-bound-cold-hardware problem as
    # Ollama had). Local faster-whisper is kept as the offline fallback,
    # loaded lazily only if actually needed.
    groq_whisper_model: str = "whisper-large-v3-turbo"

    anthropic_model: str = "claude-haiku-4-5-20251001"
    anthropic_advanced_model: str = "claude-sonnet-5"

    argus_data_dir: str = "data"
    # Console defaults to WARNING+ only for normal interactive use (INFO+
    # always goes to data/argus.log regardless) -- set to INFO for a debug
    # session to see the real-time detail (wake-word detections, tool
    # calls, turn failures) without tailing the log file separately.
    console_log_level: str = "WARNING"
    daily_budget_usd: float = 5.0
    # Optional -- set in .env so location-dependent questions (weather, "near
    # me" searches) don't need to be asked every time. Empty by default.
    user_location: str = ""

    # Voice (Phase 3). openWakeWord ships a few pretrained wake words but not
    # "Argus" -- hey_jarvis_v0.1 is the closest bundled model, and training a
    # real custom one needs hours of CPU training + multi-GB downloads (see
    # README). "local" is the default instead: no trained model at all,
    # Silero VAD + local (never cloud) Whisper transcription-and-match on
    # each detected speech burst -- zero ongoing API cost, zero training,
    # at the cost of a beat of latency vs. a streaming classifier. See
    # argus/voice/local_wake_word.py. Set to "openwakeword" to use the
    # trained-classifier path instead (lower latency, needs
    # openwakeword_model_name to actually be "argus" once/if a custom one
    # gets trained).
    wake_word_engine: str = "local"
    # Deliberately NOT named wake_word_model -- confirmed live that name
    # alone was misleading enough that Argus itself, asked what to say to
    # get its attention, surfaced this inert placeholder ("hey jarvis")
    # instead of the actual wake word ("Argus", hardcoded in
    # local_wake_word.py's _WAKE_PATTERN) -- this value does literally
    # nothing while wake_word_engine is "local" (the default).
    openwakeword_model_name: str = "hey_jarvis_v0.1"
    wake_word_threshold: float = 0.5
    # "base" (multilingual), not "base.en" -- the ".en" variants are
    # trained ONLY on English and can't transcribe anything else no matter
    # what language is requested. Needed for on-the-fly translation to
    # work through the local/offline STT fallback, not just via Groq's
    # already-multilingual hosted Whisper.
    whisper_model_size: str = "base"
    # Empty = auto-detect the spoken language (needed for translation
    # requests spoken in a non-English language). Pin to a code like "en"
    # if auto-detection ever misfires on short commands -- a real risk
    # Whisper has on very short/ambiguous audio clips.
    stt_language: str = ""
    piper_voice: str = "en_US-lessac-medium"
    # Optional. If set, Cartesia is used for TTS (much more natural), with
    # automatic fallback to Piper if unset, unreachable, or it errors.
    cartesia_api_key: str = ""
    cartesia_voice_id: str = "ef191366-f52f-447a-a398-ed8c0f2943a1"  # Archie -- warm, conversational British male
    cartesia_model: str = "sonic-3"
    audio_sample_rate: int = 16000
    # RMS threshold above which a frame counts as speech (for
    # record-until-silence VAD). Mic-dependent -- tune with
    # scripts/calibrate_mic.py if speech isn't being detected.
    voice_silence_rms_threshold: float = 60.0
    # After Argus finishes speaking, how long to keep listening without
    # requiring the wake word again before falling back to wake-word-only.
    # Reported live as needing the wake word "too often during normal
    # usage" at the old 10s default -- a completely ordinary pause to read
    # a reply or think for a moment easily exceeds that. Each follow-up
    # utterance renews the window (see voice/loop.py's run()), so this is
    # "how long since the last thing said," not a hard per-turn cap.
    followup_window_seconds: float = 30.0
    # How much NEW speech has to arrive before the console's live "hearing"
    # caption re-transcribes. Each pass is a real (rate-limited) hosted-STT
    # call on the whole buffer so far, so an ungated preview burns quota
    # that the actual command transcription then has to queue behind --
    # see _start_hearing_watcher. Raise to cut API calls further, lower for
    # a snappier caption; 0 disables the throttle entirely.
    hearing_preview_min_new_seconds: float = 1.5
    # Barge-in runs continuous wake-word inference while speech plays, which
    # competes with Piper's own onnx compute for CPU on this hardware. If it
    # causes stalls/silence, set VOICE_BARGE_IN_ENABLED=false in .env.
    voice_barge_in_enabled: bool = True
    # "Hot mic" window: once the wake word is heard, interrupting Argus
    # mid-sentence doesn't require saying it again for this many seconds
    # (refreshed by activity). Uses plain volume detection rather than
    # wake-word matching, so it's much more prone to self-triggering on
    # Argus's own voice without headphones -- set to 0 to disable.
    open_barge_in_seconds: float = 30.0

    # Remote access (README item 12) -- lets Argus be reached from your
    # phone without exposing any port on this machine: the bridge only
    # makes outbound long-poll requests to Telegram's servers, Telegram
    # handles the actual networking. Both empty by default (feature off).
    # telegram_allowed_chat_id is a real access control, not a convenience --
    # without it, anyone who finds the bot could command Argus.
    telegram_bot_token: str = ""
    telegram_allowed_chat_id: str = ""

    # Proactive context awareness: periodically glances at the active
    # window and, when it seems genuinely worth it, says something
    # unprompted -- see argus/context_awareness.py. On by default since
    # it's opt-out-in-conversation ("quiet mode" / suppression phrases),
    # not a separate thing you have to turn on.
    proactive_context_enabled: bool = True
    proactive_context_scan_seconds: float = 45.0
    # How long in the same window before a same-context check-in becomes
    # worth considering (still gated by the model's own judgment call on
    # top of this -- most scans past this threshold still produce nothing).
    proactive_context_idle_threshold_minutes: float = 120.0
    # Floor between proactive prompts, regardless of trigger, so it can
    # never feel like running commentary.
    proactive_context_cooldown_minutes: float = 5.0

    # Email monitoring (argus/email_watcher.py) -- IMAP, not SMTP/POP3
    # (IMAP is the read/monitor protocol; SMTP only sends; POP3 downloads-
    # and-removes, which would interfere with your normal mail client's
    # view of the same inbox). Both need an app-specific password, not
    # your real account password -- Gmail and Yahoo both gate third-party
    # IMAP behind one now. Either or both accounts can be left blank to
    # skip that account entirely.
    gmail_imap_user: str = ""
    gmail_imap_app_password: str = ""
    yahoo_imap_user: str = ""
    yahoo_imap_app_password: str = ""
    email_watch_enabled: bool = True
    email_watch_poll_seconds: float = 120.0
    # PRD §19/§20 unit 44b: a real 2026-08-31 incident had a bad password
    # tarpitted by Yahoo -- the resulting dropped connections hung on the
    # OS default (~21s) with no timeout set, masquerading as a network
    # fault. Applied to every imaplib.IMAP4_SSL(...) call site.
    imap_connect_timeout_seconds: float = 10.0
    # PRD §19/§20 unit 44c: consecutive AUTHENTICATIONFAILED logins (never
    # a connection timeout -- retrying that CAN fix it) before an account
    # stops polling and reports exactly one argus.credential_failed.
    imap_auth_failure_limit: int = 3

    # PRD §19/§20 unit 44a: strip whitespace from ONLY these two fields --
    # nothing else. A general password can legitimately contain spaces; an
    # IMAP app password never does. The 2026-08-31 incident's root cause
    # was a Yahoo app password stored with the spaces Yahoo displays it
    # with (19 chars incl. 3 spaces; the real password is 16, no spaces),
    # which made every login AUTHENTICATIONFAILED.
    @field_validator("gmail_imap_app_password", "yahoo_imap_app_password")
    @classmethod
    def _strip_imap_app_password_whitespace(cls, value: str) -> str:
        return "".join(value.split())

    # Google Calendar (argus/google_calendar.py) -- real OAuth2, not the
    # browser-automation approach used for the rest of Google Calendar's
    # sibling features here; the user asked for the API specifically once
    # they saw the tradeoff. From a Google Cloud Console OAuth client
    # (type: Desktop app) with the Calendar API enabled. One-time setup:
    # `argus calendar auth` opens a browser once for consent, then stores
    # a refresh token in data/ -- never re-prompts after that.
    google_calendar_client_id: str = ""
    google_calendar_client_secret: str = ""

    # Second-brain ingestion (argus/ingest.py, argus/knowledge_watcher.py):
    # drop a PDF/txt/md into this folder and it's auto-extracted, chunked,
    # and stored into semantic memory -- recallable in conversation with no
    # extra step. Empty by default (opt-in -- unlike email/calendar this
    # touches arbitrary local files, so it only watches a folder the user
    # explicitly points it at).
    knowledge_watch_folder: str = ""
    knowledge_watch_enabled: bool = True
    knowledge_watch_poll_seconds: float = 60.0

    # Proactive research digests (argus/research_digest.py): tell it topics
    # you care about (track_research_topic) and it periodically web-
    # searches for genuinely new developments, staying quiet (like
    # proactive context awareness's NONE escape hatch) when there's
    # nothing worth surfacing rather than repeating stale info on a fixed
    # schedule regardless of whether anything's changed.
    research_digest_enabled: bool = True
    research_digest_poll_seconds: float = 21600.0  # 6 hours -- news-cadence, not chat-cadence

    # Ambient stuck-detection (argus/stuck_detection.py): builds on the same
    # active-window tracking as proactive context awareness, but on a much
    # shorter fuse and looking at actual screen content (a real screenshot,
    # not just the window title) -- "stuck on the same error for 8 minutes"
    # is a meaningfully different signal than "been in the same app for two
    # hours," which is what proactive_context_idle_threshold_minutes is
    # tuned for.
    stuck_detection_enabled: bool = True
    stuck_detection_scan_seconds: float = 60.0
    stuck_detection_idle_minutes: float = 8.0

    # Memory consolidation (argus/memory/consolidation.py): the 2026-08-28
    # "best-ever memory system" design set an explicit cost goal -- feel
    # like accumulated knowledge without the token cost of ever-growing raw
    # history in context. Periodically distills new episodic turns into
    # durable core-memory candidates on the cheap local tier (never the
    # frontier model), going through the exact same propose/review flow as
    # any other agent-proposed core memory -- nothing is ever auto-
    # confirmed. The raw episodes stay in the episodic/semantic stores
    # untouched (nothing is deleted), so full detail is still searchable
    # even after a distilled version has entered core memory.
    memory_consolidation_enabled: bool = True
    memory_consolidation_poll_seconds: float = 600.0  # 10 minutes

    # Phase A event spine (PRD.md §3): one durable, queryable timeline of
    # everything Argus observes. A SEPARATE sqlite file from argus.db --
    # see PRD §3.2/P1, sensors have no interaction-lock equivalent to make
    # sharing memory/store.py's connection safe. Empty = default location
    # (data_dir / "spine.db").
    spine_db_path: str = ""
    spine_retention_days: int = 400
    # Local repo directories GitActivitySensor scans for new commits and
    # stale branches (PRD §3.4). Empty by default -- opt-in, like
    # knowledge_watch_folder above, since it's pointed at arbitrary local
    # paths.
    watched_repos: list[str] = []
    git_stale_branch_days: float = 14.0

    # Phase B world model -- rhythms (PRD Appendix A.4). Below
    # rhythm_min_days of history, every baseline reports confidence=0.0
    # and must never be asserted as fact (ROADMAP.md P1).
    rhythm_window_days: int = 30
    rhythm_min_days: int = 14
    rhythm_min_confidence: float = 0.5
    # app_class baseline: an app whose mean contiguous session is at
    # least this many minutes is classified "focus", else "browse".
    focus_session_minutes: float = 12.0

    # Phase B world model -- the snapshot (PRD §4.3). horizon_hours bounds
    # how far ahead the calendar horizon looks; world_snapshot_ttl_seconds
    # is how long a snapshot is reused before recomputing (invalidated
    # early on thread open/close).
    horizon_hours: float = 12.0
    world_snapshot_ttl_seconds: float = 5.0

    # Phase C salience scoring (PRD Appendix A.2). Every weight is its own
    # setting so it's tunable without a code change; defaults are exactly
    # A.2's worked example's weights.
    salience_weight_urgency: float = 0.45
    salience_weight_staleness: float = 0.20
    salience_weight_rhythm: float = 0.20
    salience_weight_rule: float = 0.30
    salience_weight_cost: float = 0.35
    staleness_saturation_hours: float = 72.0
    speak_threshold: float = 0.62
    ambient_threshold: float = 0.30
    # Ambiguous band: a score landing between these two gets an LLM
    # tie-break (capped by salience_llm_calls_per_hour) instead of a
    # direct threshold decision.
    salience_ambiguous_band_low: float = 0.45
    salience_ambiguous_band_high: float = 0.55
    salience_llm_calls_per_hour: int = 6
    # PRD §5.3 -- persisted so a restart doesn't refill it.
    interruptions_per_hour: int = 3
    # PRD §5.5 -- how often the escalation scheduler thread checks for due
    # steps. Not given an explicit default in the PRD; chosen fine enough
    # to feel timely against escalation windows measured in minutes
    # without polling wastefully.
    escalation_poll_seconds: float = 15.0

    # Phase I autonomous tasks (PRD §6). max_concurrent_tasks is given a
    # default (2) in §9; default_task_budget_tokens/seconds are listed
    # there with no default value specified -- 100k tokens / 30 minutes is
    # a reasonable ceiling for an unattended run, chosen here since the
    # column is NOT NULL and every task needs one.
    max_concurrent_tasks: int = 2
    default_task_budget_tokens: int = 100_000
    default_task_budget_seconds: float = 1800.0
    # PRD §9: new subsystems ship disabled by default where they change
    # behavior. Gates whether start_task/task_status/cancel_task are even
    # registered, and whether Orchestrator constructs a TaskRunner at all.
    enable_task_runner: bool = False

    # PRD §7.5 (G4 induction). Exceeding this makes it the nagging it
    # exists to remove -- counts ALL induced proposals created in the
    # trailing 7 days, not just still-pending ones.
    induced_rule_proposals_per_week: int = 2

    # Unit 24: realtime voice confirmation timing. Confirmed live -- the
    # old fixed listen window started the moment the question was SENT,
    # before OpenAI had spoken a word of it, so both retry attempts timed
    # out and fell through to the console card even with a spoken answer.
    # voice_confirm_speak_timeout_seconds caps how long _ask_voice_
    # confirmation waits for the question's audio to actually start and
    # finish playing before giving up on it entirely (a failed generation
    # must not hang the confirmer or the tool call). voice_confirm_listen_
    # seconds is the answer window itself, started only once that audio
    # has actually finished -- longer than pipeline mode's equivalent
    # because realtime mode also pays for async transcription latency
    # pipeline mode doesn't.
    voice_confirm_speak_timeout_seconds: float = 15.0
    voice_confirm_listen_seconds: float = 10.0

    # PRD §14 (unit 27): standing authorizations. Every grant has an
    # expiry -- never auto-renewed, never permanent -- so a forgotten
    # grant ages out on its own rather than silently authorizing forever.
    authorization_default_days: int = 90

    # PRD §15 (Phase H, unit 29): how often the dashboard's own JS polls
    # GET /api/state. Independent of world_snapshot_ttl_seconds -- that
    # TTL bounds how often the *server* actually recomputes a snapshot;
    # this bounds how often the *client* asks at all, so a slow client
    # (or several open tabs) can't hammer the endpoint faster than a
    # human dashboard glance ever needs.
    dashboard_poll_seconds: float = 5.0

    # PRD §15 unit 32: a completed transcript matching an acknowledgment
    # phrase only closes the most recently SPOKEN-about thread if it
    # arrives within this many seconds of Argus actually saying it
    # (hold/ambient deliveries don't count -- the user never heard
    # those). Long enough to cover a natural "got it" a few seconds
    # after Argus finishes a sentence, short enough that an unrelated
    # "got it" minutes later about something else entirely doesn't
    # reach back and close a stale thread.
    acknowledgment_window_seconds: float = 180.0

    # PRD §16 unit 34: a realtime session can run for hours, so the
    # date/time/timezone grounding sent at connect drifts. Refreshed on
    # the user's own turns once a refresh is this many seconds stale --
    # the timezone (the actual reported bug) is already correct from the
    # first connect regardless; this keeps clock math (e.g. "3pm this
    # afternoon") honest over a long session.
    realtime_time_refresh_seconds: float = 300.0

    # PRD §19 unit 37: how often ProactiveEngine's one periodic tick runs
    # -- reminders, rule firing, reap-on-timer, escalation draining,
    # induction, and retention all live on this one cadence.
    proactive_tick_seconds: float = 15.0
    # Appendix A.1 evaluation cadence: "on a timer (default 60)". Already
    # referenced by world/threads.py's own docstring/comments before this
    # setting actually existed -- the timer itself is unit 37's fix.
    thread_reap_seconds: float = 60.0

    @property
    def data_dir(self) -> Path:
        d = PROJECT_ROOT / self.argus_data_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def workspace_dir(self) -> Path:
        """Default working directory for relative file-tool paths."""
        d = self.data_dir / "workspace"
        d.mkdir(parents=True, exist_ok=True)
        return d


settings = Settings()


def resolved_voice_mode(settings_obj: "Settings | None" = None) -> str:
    """PRD §19 unit 40: realtime is now the default, actively-developed
    voice mode; pipeline is kept only as an unmaintained fallback (no
    OpenAI dependency, or explicit cost/offline preference). VOICE_MODE
    unset resolves to realtime when an OpenAI key is present, pipeline
    otherwise -- VOICE_MODE explicitly set (env, .env, or passed
    directly) is never overridden.

    `model_fields_set` (not a plain attribute read) is what "unset"
    means here: pydantic-settings only populates it from what was
    actually supplied at construction (env/.env/kwargs), not from a
    later plain attribute assignment -- which is also why this takes an
    optional Settings instance rather than only ever reading the module
    singleton: monkeypatching `settings.voice_mode` post-construction
    would never register as "explicitly set" the way a real VOICE_MODE
    env var does, making that path untestable against the module
    singleton alone."""
    s = settings_obj if settings_obj is not None else settings
    if "voice_mode" in s.model_fields_set:
        return s.voice_mode
    return "realtime" if s.openai_api_key else "pipeline"
