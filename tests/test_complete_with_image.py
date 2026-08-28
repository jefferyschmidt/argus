from unittest.mock import MagicMock

from argus.llm.base import CompletionResult, Tier
from argus.llm.cost_governor import CostGovernor
from argus.llm.router import ModelRouter


def _isolated_router(tmp_path, daily_cap_usd=5.0) -> ModelRouter:
    # ModelRouter's own CostGovernor writes to the REAL data dir's
    # spend.json by default -- swap in one backed by a tmp file so these
    # tests don't corrupt the actual app's real daily spend tracking.
    router = ModelRouter(daily_cap_usd=daily_cap_usd)
    router.cost_governor = CostGovernor(daily_cap_usd=daily_cap_usd, path=tmp_path / "spend.json")
    return router


def test_complete_with_image_delegates_to_frontier_and_records_cost(tmp_path):
    router = _isolated_router(tmp_path)
    router.frontier = MagicMock()
    router.frontier.complete_with_image.return_value = CompletionResult(
        text="a receipt", tier=Tier.FAST, model="test", input_tokens=100, output_tokens=50
    )

    result = router.complete_with_image(b"fake-jpeg", "describe this")

    assert result.text == "a receipt"
    router.frontier.complete_with_image.assert_called_once_with(
        b"fake-jpeg", "describe this", tier=Tier.FAST, media_type="image/jpeg"
    )
    assert router.cost_governor.spend_today > 0


def test_complete_with_image_respects_daily_cap(tmp_path):
    from argus.llm.cost_governor import BudgetExceeded

    router = _isolated_router(tmp_path, daily_cap_usd=0.0)
    router.frontier = MagicMock()

    try:
        router.complete_with_image(b"fake-jpeg", "describe this")
        assert False, "expected BudgetExceeded"
    except BudgetExceeded:
        pass
    router.frontier.complete_with_image.assert_not_called()
