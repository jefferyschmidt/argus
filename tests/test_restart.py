from unittest.mock import patch

import argus.restart as restart_mod


class _SyncThread:
    """Stands in for threading.Thread but runs the target immediately and
    synchronously -- so this test never actually leaves a background timer
    that could fire os.execv (replacing the pytest process!) mid-suite."""

    def __init__(self, target, daemon):
        self._target = target

    def start(self):
        self._target()


def test_request_restart_targets_module_entrypoint_with_original_args(monkeypatch):
    monkeypatch.setattr(restart_mod.sys, "argv", ["argus", "voice"])
    with patch.object(restart_mod, "threading") as mock_threading, \
         patch.object(restart_mod.time, "sleep") as mock_sleep, \
         patch.object(restart_mod.os, "execv") as mock_execv:
        mock_threading.Thread.side_effect = _SyncThread
        restart_mod.request_restart(delay=0)

    mock_sleep.assert_called_once_with(0)
    mock_execv.assert_called_once_with(
        restart_mod.sys.executable, [restart_mod.sys.executable, "-m", "argus.cli", "voice"]
    )


def test_request_restart_defaults_to_voice_with_no_extra_args(monkeypatch):
    monkeypatch.setattr(restart_mod.sys, "argv", ["argus"])
    with patch.object(restart_mod, "threading") as mock_threading, \
         patch.object(restart_mod.time, "sleep"), \
         patch.object(restart_mod.os, "execv") as mock_execv:
        mock_threading.Thread.side_effect = _SyncThread
        restart_mod.request_restart(delay=0)

    mock_execv.assert_called_once_with(
        restart_mod.sys.executable, [restart_mod.sys.executable, "-m", "argus.cli", "voice"]
    )
