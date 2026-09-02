from unittest.mock import MagicMock

from argus.orchestrator import Orchestrator
from argus.proactive_engine import ProactiveEngine


def test_orchestrator_always_constructs_spine_and_registers_compose_document():
    orch = Orchestrator()
    assert orch.spine is not None
    assert "compose_document" in orch.tools._tools


def test_proactive_engine_reuses_the_orchestrators_spine():
    orch = Orchestrator()
    engine = ProactiveEngine(orch, speak_fn=MagicMock(), interaction_lock=MagicMock())

    assert engine.spine is orch.spine


def test_a_document_composed_through_the_tool_is_visible_to_the_engines_spine(tmp_path, monkeypatch):
    """End-to-end proof the shared instance is real: composing through
    Orchestrator's tool registry is immediately visible to whatever
    ProactiveEngine's SalienceEngine reads the spine through."""
    monkeypatch.setattr("argus.spine.store.settings.spine_db_path", str(tmp_path / "spine.db"))
    monkeypatch.setattr("argus.compose.compose.settings.argus_data_dir", str(tmp_path))
    orch = Orchestrator()
    engine = ProactiveEngine(orch, speak_fn=MagicMock(), interaction_lock=MagicMock())

    # Calls the tool's handler directly, bypassing the CONFIRM-tier
    # console prompt -- this test is about spine sharing, not the
    # confirmation flow.
    orch.tools._tools["compose_document"].handler({"title": "x", "sections": [{"body": "y"}]})

    assert engine.spine.count(kind="document.composed") == 1
