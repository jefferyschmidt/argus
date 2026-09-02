"""§8 acceptance, at the level the PRD actually asks about: a
composition exceeding its task budget fails cleanly, leaving no partial
file. Exercises the realistic path -- a Phase I task whose budget runs
out before it ever reaches the point of calling compose_document --
through the real TaskRunner, not just compose()'s own atomicity."""

from unittest.mock import MagicMock

from argus.config import settings
from argus.spine.store import SpineStore
from argus.tasks.store import TaskStore
from argus.tasks.worker import TaskRunner


def _wait_until(predicate, timeout=3.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_task_that_never_reaches_compose_leaves_no_document_file(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.compose.compose.settings.argus_data_dir", str(tmp_path))

    store = TaskStore(tmp_path / "argus.db")
    spine = SpineStore(tmp_path / "spine.db")
    router = MagicMock()

    def fake_complete_with_tools(goal, **kwargs):
        on_tool_call = kwargs["on_tool_call"]
        # Simulates gathering content via other tools, never getting far
        # enough to call compose_document before the budget trips.
        for i in range(1, 1000):
            on_tool_call("search_web", {"q": "x"}, "some result", tokens_used=i * 1000)
        raise AssertionError("must never reach here -- the budget should stop it first")

    router.complete_with_tools.side_effect = fake_complete_with_tools
    runner = TaskRunner(store, spine, router)

    task_id = runner.submit(
        title="Compose a weekly digest", prompt="Gather this week's activity and compose_document it.",
        budget_tokens=5000, budget_seconds=600.0,
    )

    assert _wait_until(lambda: store.get(task_id).status == "failed")

    documents_dir = settings.data_dir / "documents"
    assert not documents_dir.exists() or list(documents_dir.iterdir()) == []
    assert spine.query(kinds=["document.composed"]) == []


def test_task_that_completes_composition_before_budget_exceeded_leaves_a_real_file(tmp_path, monkeypatch):
    """Contrast case: when the budget is NOT exceeded, compose_document
    genuinely runs (via the real tool registry) and the file exists."""
    from argus.tools.compose import _build_compose_document
    from argus.tools.registry import ToolRegistry

    monkeypatch.setattr("argus.compose.compose.settings.argus_data_dir", str(tmp_path))

    store = TaskStore(tmp_path / "argus.db")
    spine = SpineStore(tmp_path / "spine.db")
    registry = ToolRegistry(confirmer=lambda name, tool_input: True)
    registry.register(_build_compose_document(spine))
    router = MagicMock()

    def fake_complete_with_tools(goal, **kwargs):
        on_tool_call = kwargs["on_tool_call"]
        result = registry.execute("compose_document", {"title": "Digest", "sections": [{"body": "All quiet."}]})
        on_tool_call("compose_document", {"title": "Digest"}, result, tokens_used=100)
        completion = MagicMock()
        completion.text = result
        completion.model = "test"
        return completion

    router.complete_with_tools.side_effect = fake_complete_with_tools
    runner = TaskRunner(store, spine, router, tool_registry=registry)

    task_id = runner.submit(title="Compose a digest", prompt="compose it", budget_tokens=100_000, budget_seconds=600.0)

    assert _wait_until(lambda: store.get(task_id).status == "done")
    documents_dir = settings.data_dir / "documents"
    assert list(documents_dir.iterdir()) != []
    assert spine.count(kind="document.composed") == 1
