"""Manual smoke test: exercises handle_streaming end to end against the real
API, printing each sentence as it arrives with a timestamp so time-to-first-
sentence is visible. Not part of the pytest suite -- costs real API tokens."""

import time

from argus.orchestrator import Orchestrator

if __name__ == "__main__":
    orch = Orchestrator()
    start = time.monotonic()
    sentences = []

    def on_sentence(s: str) -> None:
        elapsed = time.monotonic() - start
        print(f"[{elapsed:5.2f}s] {s}")
        sentences.append(s)

    full_reply = orch.handle_streaming(
        "Give me a three sentence overview of why the sky is blue.",
        on_sentence=on_sentence,
    )
    print("\nFINAL TEXT:", full_reply)
    print("TIER/MODEL:", orch.last_tier, orch.last_model)
    print("Sentences streamed:", len(sentences))
