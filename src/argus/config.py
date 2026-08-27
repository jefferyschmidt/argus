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

    # Voice (Phase 3). openWakeWord ships a few pretrained wake words but not
    # "Argus" -- hey_jarvis_v0.1 is the closest bundled model until a custom
    # one is trained (see README). Swap via WAKE_WORD_MODEL once you have one.
    wake_word_model: str = "hey_jarvis_v0.1"
    wake_word_threshold: float = 0.5
    whisper_model_size: str = "base.en"
    piper_voice: str = "en_US-lessac-medium"
    audio_sample_rate: int = 16000
    # RMS threshold above which a frame counts as speech (for
    # record-until-silence VAD). Mic-dependent -- tune with
    # scripts/calibrate_mic.py if speech isn't being detected.
    voice_silence_rms_threshold: float = 60.0

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


settings = Settings()
