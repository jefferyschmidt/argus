"""Maps IPA phonemes (as returned by Cartesia's phoneme timestamps) to a
small set of mouth-shape categories the UI can animate between. Not
linguistically exhaustive -- just enough distinct shapes to look like an
actual mouth forming sounds instead of a bar pulsing with volume."""

# category -> (openness 0-1, width 0-1) target shape
VISEME_SHAPES = {
    "closed": (0.05, 0.35),      # bilabials: m, b, p -- lips together
    "open_wide": (1.0, 0.75),    # open vowels: aɪ, aʊ, ɑ, æ, ʌ, a
    "rounded": (0.55, 0.30),     # rounded vowels: oʊ, uː, ʊ, w, ɔ
    "mid": (0.45, 0.55),         # mid vowels: ə, ɛ, ɪ, iː, eɪ, ɜ
    "narrow": (0.20, 0.45),      # most consonants -- slight movement
    "silence": (0.04, 0.40),
}

_PHONEME_TO_VISEME = {
    # bilabials
    "m": "closed", "b": "closed", "p": "closed",
    # open vowels
    "a": "open_wide", "ɑ": "open_wide", "æ": "open_wide", "ʌ": "open_wide",
    "aɪ": "open_wide", "aʊ": "open_wide", "aː": "open_wide",
    # rounded vowels + labial-velar
    "oʊ": "rounded", "o": "rounded", "uː": "rounded", "u": "rounded",
    "ʊ": "rounded", "ɔ": "rounded", "ɔɪ": "rounded", "w": "rounded",
    # mid vowels
    "ə": "mid", "ɛ": "mid", "ɪ": "mid", "iː": "mid", "i": "mid",
    "eɪ": "mid", "ɜ": "mid", "ɜː": "mid", "e": "mid",
    # everything else (fricatives, plosives, nasals, liquids, glides)
    "f": "narrow", "v": "narrow", "s": "narrow", "z": "narrow",
    "t": "narrow", "d": "narrow", "k": "narrow", "g": "narrow",
    "n": "narrow", "l": "narrow", "r": "narrow", "h": "narrow",
    "ŋ": "narrow", "ð": "narrow", "θ": "narrow", "ʃ": "narrow",
    "ʒ": "narrow", "tʃ": "narrow", "dʒ": "narrow", "j": "narrow",
}


def viseme_for_phoneme(phoneme: str) -> str:
    return _PHONEME_TO_VISEME.get(phoneme, "narrow")


def build_viseme_timeline(phonemes: list[str], starts: list[float], ends: list[float]) -> list[dict]:
    """Cartesia's phoneme timestamps -> a compact timeline the UI can walk
    through by elapsed playback time."""
    return [
        {"viseme": viseme_for_phoneme(p), "start": round(s, 3), "end": round(e, 3)}
        for p, s, e in zip(phonemes, starts, ends)
    ]
