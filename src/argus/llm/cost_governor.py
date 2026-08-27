import json
from datetime import date
from pathlib import Path

from argus.config import settings


class BudgetExceeded(Exception):
    pass


class CostGovernor:
    """Tracks running API spend and enforces a daily cap so an unattended
    agent loop can't rack up a surprise bill. Persisted to disk so the cap
    survives restarts within the same day."""

    def __init__(self, daily_cap_usd: float = 5.0, path: Path | None = None):
        self.daily_cap_usd = daily_cap_usd
        self.path = path or (settings.data_dir / "spend.json")
        self._state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if data.get("date") == str(date.today()):
                return data
        return {"date": str(date.today()), "spend_usd": 0.0}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._state))

    @property
    def spend_today(self) -> float:
        self._state = self._load()
        return self._state["spend_usd"]

    def check(self) -> None:
        if self.spend_today >= self.daily_cap_usd:
            raise BudgetExceeded(
                f"Daily budget of ${self.daily_cap_usd:.2f} reached "
                f"(spent ${self.spend_today:.2f}). Falling back to local model."
            )

    def record(self, cost_usd: float) -> None:
        self._state = self._load()
        self._state["spend_usd"] += cost_usd
        self._save()
