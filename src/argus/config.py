from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    anthropic_api_key: str = ""
    ollama_host: str = "http://localhost:11434"
    ollama_local_model: str = "llama3.2:3b"

    anthropic_model: str = "claude-haiku-4-5-20251001"
    anthropic_advanced_model: str = "claude-sonnet-5"

    argus_data_dir: str = "data"
    daily_budget_usd: float = 5.0
    # Optional -- set in .env so location-dependent questions (weather, "near
    # me" searches) don't need to be asked every time. Empty by default.
    user_location: str = ""

    # Voice (Phase 3). openWakeWord ships a few pretrained wake words but not
    # "Argus" -- hey_jarvis_v0.1 is the closest bundled model until a custom
    # one is trained (see README). Swap via WAKE_WORD_MODEL once you have one.
    wake_word_model: str = "hey_jarvis_v0.1"
    wake_word_threshold: float = 0.5
    whisper_model_size: str = "base.en"
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
    followup_window_seconds: float = 10.0
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

    @property
    def data_dir(self) -> Path:
        d = PROJECT_ROOT / self.argus_data_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def workspace_dir(self) -> Path:
        """Sandbox root for file tools. Everything the file tools touch is
        confined under here until later phases widen scope deliberately."""
        d = self.data_dir / "workspace"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def real_fs_roots(self) -> list[Path]:
        """Real folders (beyond the sandbox) the file tools may touch.
        Deliberately narrow -- your actual Documents/Downloads/Desktop, not
        the whole filesystem. Extend by editing this list if you want more."""
        home = Path.home()
        return [p for p in (home / "Documents", home / "Downloads", home / "Desktop") if p.exists()]


settings = Settings()
