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

    def speak(self, text: str, stop_event: threading.Event | None = None) -> None:
        if not text.strip():
            return
        audio_chunks = list(self._voice.synthesize(text))
        if not audio_chunks:
            return
        samples = np.concatenate([c.audio_int16_array for c in audio_chunks])
        play_audio(samples, sample_rate=audio_chunks[0].sample_rate, stop_event=stop_event)


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
