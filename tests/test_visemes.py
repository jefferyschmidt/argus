from argus.voice.visemes import build_viseme_timeline, viseme_for_phoneme


def test_bilabials_map_to_closed():
    assert viseme_for_phoneme("m") == "closed"
    assert viseme_for_phoneme("b") == "closed"
    assert viseme_for_phoneme("p") == "closed"


def test_open_vowels_map_to_open_wide():
    assert viseme_for_phoneme("aɪ") == "open_wide"
    assert viseme_for_phoneme("ɑ") == "open_wide"


def test_unknown_phoneme_falls_back_to_narrow():
    assert viseme_for_phoneme("???") == "narrow"


def test_build_viseme_timeline_pairs_correctly():
    timeline = build_viseme_timeline(["h", "ə", "l"], [0.0, 0.1, 0.2], [0.1, 0.2, 0.3])
    assert timeline == [
        {"viseme": "narrow", "start": 0.0, "end": 0.1},
        {"viseme": "mid", "start": 0.1, "end": 0.2},
        {"viseme": "narrow", "start": 0.2, "end": 0.3},
    ]
