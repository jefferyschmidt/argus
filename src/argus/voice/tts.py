import threading
from pathlib import Path

import numpy as np

from argus.config import settings
from argus.voice.audio_io import play_audio


class Speaker:
    def __init__(self):
        from piper import PiperVoice

        model_path = _voice_model_path(settings.piper_voice)
        self._voice = PiperVoice.load(str(model_path))

    def synthesize(self, text: str) -> tuple[np.ndarray, int] | None:
        """CPU-heavy step (onnxruntime). Deliberately separate from play()
        so it can run to completion BEFORE anything else (like the barge-in
        watcher's continuous wake-word inference) starts competing for CPU
        -- on this CPU-only hardware, overlapping the two caused silent/
        stalled playback with no exception."""
        if not text.strip():
            return None
        audio_chunks = list(self._voice.synthesize(text))
        if not audio_chunks:
            return None
        samples = np.concatenate([c.audio_int16_array for c in audio_chunks])
        return samples, audio_chunks[0].sample_rate

    def speak(self, text: str, stop_event: threading.Event | None = None) -> None:
        """Convenience wrapper for callers that don't need to control the
        synthesize/play split (e.g. one-off scripts, text chat)."""
        synthesized = self.synthesize(text)
        if synthesized is None:
            return
        samples, sample_rate = synthesized
        play_audio(samples, sample_rate=sample_rate, stop_event=stop_event)


def _voice_model_path(voice_name: str) -> Path:
    voices_dir = settings.data_dir / "piper_voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    model_path = voices_dir / f"{voice_name}.onnx"
    if not model_path.exists():
        _download_voice(voice_name, voices_dir)
    return model_path


def _download_voice(voice_name: str, voices_dir: Path) -> None:
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-m", "piper.download_voices", voice_name, "--download-dir", str(voices_dir)],
        check=True,
    )
