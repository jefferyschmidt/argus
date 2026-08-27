import numpy as np

from argus.voice.audio_io import compute_envelope


def test_empty_samples_returns_empty_envelope():
    assert compute_envelope(np.array([], dtype=np.int16), 16000) == []


def test_silence_returns_all_zero_envelope():
    samples = np.zeros(16000, dtype=np.int16)
    env = compute_envelope(samples, 16000)
    assert env and all(v == 0.0 for v in env)


def test_envelope_normalized_to_own_peak():
    sr = 16000
    # loud chunk followed by quiet chunk
    loud = np.full(int(sr * 0.04), 20000, dtype=np.int16)
    quiet = np.full(int(sr * 0.04), 2000, dtype=np.int16)
    samples = np.concatenate([loud, quiet])
    env = compute_envelope(samples, sr, chunk_ms=40)

    assert len(env) == 2
    assert env[0] == 1.0  # peak chunk normalizes to 1.0
    assert 0 < env[1] < 1.0
