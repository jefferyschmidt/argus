"""Generates a synthetic positive/negative dataset for training a custom
"hey argus" openWakeWord model. Uses Cartesia's large voice library for
real speaker diversity (a from-scratch wake word normally needs real
recordings from many speakers -- TTS is the practical substitute for a
single-user local project). Saves fixed-length 16kHz mono int16 clips as
.npy arrays under data/wake_word_dataset/.

Real API cost: a few hundred short TTS calls (~5-15 chars each). Small but
not free -- this is a deliberate one-time training-data cost, not routine
usage.
"""

import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from argus.config import settings  # noqa: E402
from cartesia import Cartesia  # noqa: E402

SR = 16000
CLIP_SECONDS = 1.6
CLIP_SAMPLES = int(SR * CLIP_SECONDS)
OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "wake_word_dataset"

POSITIVE_PHRASES = ["Argus", "Hey Argus", "OK Argus", "Argus?"]

# Broad, phonetically varied negative sentences -- everyday speech that
# should NOT trigger the wake word. Deliberately includes some words that
# share sounds with "Argus" (car, guess, us, are) without being adversarial
# near-misses (those come from generate_adversarial_texts separately).
GENERIC_NEGATIVES = [
    "What time is it right now", "Can you turn off the lights",
    "I need to go to the store later", "Let's grab dinner tonight",
    "The weather looks nice today", "Turn up the volume please",
    "I parked the car in the garage", "Take a guess what happened",
    "It's just the two of us here", "Where are my keys",
    "Play some music for me", "Set a timer for ten minutes",
    "How do I get to the airport", "Remind me to call mom",
    "That's a great question actually", "I'm not sure about that one",
    "Can you check the news for me", "What's on my calendar today",
    "Let's watch a movie tonight", "I think it's going to rain",
    "Open the garage door please", "Send a message to my brother",
    "What's the capital of France", "How far away is the meeting",
    "I need a bigger cup of coffee", "The dog needs to go outside",
    "Can you look that up for me", "What's the score of the game",
    "Let's order pizza for dinner", "I forgot where I put my phone",
    "Good morning, how did you sleep", "That was a really long day",
    "Can we talk about this later", "I'll be there in ten minutes",
    "Thanks so much for your help", "Let me think about it",
    "This traffic is unbelievable today", "I really need a vacation",
    "Did you see that email I sent", "Let's schedule a call for Monday",
    "The kids are asleep already", "I'm heading out the door now",
    "What's the wifi password again", "Can you grab my jacket",
    "We should leave in a few minutes", "I already ate lunch",
    "That restaurant was amazing", "Let's split the bill evenly",
    "I need to charge my phone", "The battery is almost dead",
    "Hey, can you hear me okay", "Sorry, I missed that",
    "One more thing before you go", "Let's circle back on this",
    "I appreciate you taking the time", "This is exactly what I needed",
    "Nothing much, just relaxing", "I'll follow up tomorrow morning",
]


def load_voice_ids(n: int) -> list[str]:
    client = Cartesia(api_key=settings.cartesia_api_key)
    voices = list(client.voices.list())
    english = [v for v in voices if v.language == "en"]
    random.shuffle(english)
    return [v.id for v in english[:n]]


def synth_clip(client: Cartesia, text: str, voice_id: str) -> np.ndarray | None:
    try:
        response = client.tts.generate(
            model_id=settings.cartesia_model,
            transcript=text,
            voice={"id": voice_id},
            output_format={"container": "raw", "encoding": "pcm_s16le", "sample_rate": SR},
        )
        audio = np.frombuffer(response.read(), dtype=np.int16)
    except Exception:
        return None
    if audio.size == 0:
        return None
    if audio.size >= CLIP_SAMPLES:
        # random crop start so the word isn't always at the exact same offset
        start = random.randint(0, audio.size - CLIP_SAMPLES)
        return audio[start:start + CLIP_SAMPLES]
    padded = np.zeros(CLIP_SAMPLES, dtype=np.int16)
    offset = random.randint(0, CLIP_SAMPLES - audio.size)
    padded[offset:offset + audio.size] = audio
    return padded


def generate_set(phrases_and_voices: list[tuple[str, str]], label: str, max_workers: int = 10) -> np.ndarray:
    client = Cartesia(api_key=settings.cartesia_api_key)
    clips = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(synth_clip, client, text, vid): (text, vid) for text, vid in phrases_and_voices}
        done = 0
        for fut in as_completed(futures):
            done += 1
            result = fut.result()
            if result is not None:
                clips.append(result)
            if done % 20 == 0:
                print(f"  [{label}] {done}/{len(phrases_and_voices)}")
    return np.stack(clips) if clips else np.empty((0, CLIP_SAMPLES), dtype=np.int16)


def main() -> None:
    from openwakeword.data import generate_adversarial_texts

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    start = time.time()

    print("Sampling voices...")
    pos_voices = load_voice_ids(60)
    neg_voices = load_voice_ids(25)

    print("Building positive job list...")
    positive_jobs = [(phrase, v) for phrase in POSITIVE_PHRASES for v in pos_voices]
    random.shuffle(positive_jobs)
    positive_jobs = positive_jobs[:220]

    print("Generating adversarial (hard) negative phrases...")
    adversarial = []
    for phrase in POSITIVE_PHRASES:
        try:
            adversarial.extend(generate_adversarial_texts(phrase.lower().rstrip("?"), 25))
        except Exception as e:
            print(f"  (adversarial generation skipped for '{phrase}': {e})")
    adversarial = list(set(adversarial))[:80]
    print(f"  {len(adversarial)} adversarial phrases")

    negative_phrases = GENERIC_NEGATIVES + adversarial
    negative_jobs = [(phrase, random.choice(neg_voices)) for phrase in negative_phrases for _ in range(3)]
    random.shuffle(negative_jobs)
    negative_jobs = negative_jobs[:260]

    print(f"Synthesizing {len(positive_jobs)} positive clips...")
    positive_clips = generate_set(positive_jobs, "positive")
    print(f"Synthesizing {len(negative_jobs)} negative clips...")
    negative_clips = generate_set(negative_jobs, "negative")

    np.save(OUT_DIR / "positive.npy", positive_clips)
    np.save(OUT_DIR / "negative.npy", negative_clips)

    print(f"\nDone in {time.time()-start:.0f}s")
    print(f"positive: {positive_clips.shape}")
    print(f"negative: {negative_clips.shape}")
    print(f"saved to {OUT_DIR}")


if __name__ == "__main__":
    main()
