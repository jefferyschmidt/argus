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
