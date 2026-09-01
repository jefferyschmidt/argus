import time

from argus.spine.observation import Observation
from argus.spine.store import SpineStore
from argus.world.rhythms import RhythmStore


def _rhythm_store(tmp_path) -> RhythmStore:
    return RhythmStore(tmp_path / "argus.db")


def _spine(tmp_path) -> SpineStore:
    return SpineStore(tmp_path / "spine.db")


def _record(spine, kind, ts, subject=None, payload=None, key=None, source="test"):
    spine.record(Observation(
        source=source, kind=kind, ts=ts, subject=subject, payload=payload or {},
        dedupe_key=key or f"{kind}:{ts}:{subject}",
    ))


# -- confidence floor --------------------------------------------------

def test_below_rhythm_min_days_yields_zero_confidence_for_all_four(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.rhythms.settings.rhythm_min_days", 14)
    spine = _spine(tmp_path)
    rhythms = _rhythm_store(tmp_path)
    now = time.time()
    # Only 3 distinct days of activity -- well under the 14-day floor.
    for day in range(3):
        _record(spine, "mail.received", now - day * 86400, subject="a@x.com", key=f"m{day}")

    rhythms.recompute(spine)

    for name in ("active_hours", "app_class", "sender_importance", "session_length"):
        row = rhythms.get(name)
        assert row["confidence"] == 0.0, name


# -- active_hours --------------------------------------------------------

def test_active_hours_buckets_are_normalized_to_peak_of_one(tmp_path):
    spine = _spine(tmp_path)
    rhythms = _rhythm_store(tmp_path)
    now = time.time()

    import datetime
    peak_hour_dt = datetime.datetime.fromtimestamp(now).replace(hour=9, minute=0, second=0, microsecond=0)
    other_hour_dt = peak_hour_dt.replace(hour=3)
    for i in range(10):
        _record(spine, "focus.changed", peak_hour_dt.timestamp() - i * 86400, subject=f"w{i}", key=f"peak{i}")
    for i in range(3):
        _record(spine, "focus.changed", other_hour_dt.timestamp() - i * 86400, subject=f"w{i}", key=f"other{i}")

    rhythms.recompute(spine)
    row = rhythms.get("active_hours")
    buckets = row["value"]["buckets"]

    assert len(buckets) == 24
    assert max(buckets) == 1.0
    assert buckets[9] == 1.0
    assert 0 < buckets[3] < 1.0


# -- app_class -------------------------------------------------------------

def test_app_seen_only_3_times_does_not_receive_a_class(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.rhythms.settings.rhythm_min_days", 1)
    spine = _spine(tmp_path)
    rhythms = _rhythm_store(tmp_path)
    now = time.time()

    # 3 sessions in "rareapp" -- below app_class's min_samples of 30.
    ts = now - 5 * 86400
    for i in range(4):  # 4 focus.changed events -> 3 contiguous sessions
        _record(spine, "focus.changed", ts + i * 600, subject="RareApp - file.py", key=f"rare{i}")

    rhythms.recompute(spine)
    row = rhythms.get("app_class")
    assert "rareapp" not in row["value"]


def test_app_class_with_enough_samples_is_classified_focus_or_browse(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.rhythms.settings.rhythm_min_days", 1)
    monkeypatch.setattr("argus.world.rhythms.settings.focus_session_minutes", 12.0)
    spine = _spine(tmp_path)
    rhythms = _rhythm_store(tmp_path)
    now = time.time() - 5 * 86400

    ts = now
    # 35 long (20 min) sessions in "vscode" -- above min_samples, mean >= focus threshold.
    for i in range(36):
        _record(spine, "focus.changed", ts + i * 1200, subject="VS Code - main.py", key=f"vs{i}")

    rhythms.recompute(spine)
    row = rhythms.get("app_class")
    assert row["value"]["vs code"]["class"] == "focus"
    assert row["value"]["vs code"]["n"] == 35


# -- sender_importance -------------------------------------------------------

def test_sender_importance_ratio_reflects_acted_vs_received(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.rhythms.settings.rhythm_min_days", 1)
    spine = _spine(tmp_path)
    rhythms = _rhythm_store(tmp_path)
    now = time.time() - 5 * 86400

    for i in range(10):
        _record(spine, "mail.received", now + i * 60, subject="julia@x.com", key=f"recv{i}")
    for i in range(6):
        _record(spine, "mail.replied", now + i * 60 + 10, subject="julia@x.com", key=f"reply{i}")

    rhythms.recompute(spine)
    row = rhythms.get("sender_importance")
    assert row["value"]["julia@x.com"]["n"] == 10
    assert row["value"]["julia@x.com"]["ratio"] == 0.6


def test_sender_below_min_samples_is_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.rhythms.settings.rhythm_min_days", 1)
    spine = _spine(tmp_path)
    rhythms = _rhythm_store(tmp_path)
    now = time.time() - 5 * 86400
    for i in range(3):  # below sender_importance's min_samples of 5
        _record(spine, "mail.received", now + i * 60, subject="rare@x.com", key=f"r{i}")

    rhythms.recompute(spine)
    row = rhythms.get("sender_importance")
    assert "rare@x.com" not in row["value"]


# -- session_length -------------------------------------------------------

def test_session_length_is_the_median_of_contiguous_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.rhythms.settings.rhythm_min_days", 1)
    spine = _spine(tmp_path)
    rhythms = _rhythm_store(tmp_path)
    now = time.time() - 5 * 86400
    # Sessions of 10, 20, 30 minutes.
    _record(spine, "focus.changed", now, subject="A", key="s0")
    _record(spine, "focus.changed", now + 10 * 60, subject="B", key="s1")
    _record(spine, "focus.changed", now + 30 * 60, subject="C", key="s2")
    _record(spine, "focus.changed", now + 60 * 60, subject="D", key="s3")

    rhythms.recompute(spine)
    row = rhythms.get("session_length")
    assert row["value"]["median_minutes"] == 20.0


# -- rhythm_fit -------------------------------------------------------------

def test_rhythm_fit_returns_half_when_confidence_below_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.rhythms.settings.rhythm_min_days", 14)
    spine = _spine(tmp_path)
    rhythms = _rhythm_store(tmp_path)
    now = time.time()
    _record(spine, "mail.received", now, subject="a@x.com", key="only-one")

    assert rhythms.rhythm_fit("mail.received", hour=9, spine=spine) == 0.5


def test_rhythm_fit_with_no_observations_of_kind_is_half(tmp_path):
    spine = _spine(tmp_path)
    rhythms = _rhythm_store(tmp_path)
    assert rhythms.rhythm_fit("git.commit", hour=9, spine=spine) == 0.5


def test_rhythm_fit_peak_hour_returns_one(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.rhythms.settings.rhythm_min_days", 1)
    monkeypatch.setattr("argus.world.rhythms.settings.rhythm_min_confidence", 0.0)
    spine = _spine(tmp_path)
    rhythms = _rhythm_store(tmp_path)

    import datetime
    base = datetime.datetime.fromtimestamp(time.time()).replace(hour=9, minute=0, second=0, microsecond=0)
    for day in range(25):
        ts = base.timestamp() - day * 86400
        _record(spine, "mail.received", ts, subject=f"s{day}@x.com", key=f"recv{day}")
        _record(spine, "mail.replied", ts + 10, subject=f"s{day}@x.com", key=f"reply{day}")

    assert rhythms.rhythm_fit("mail.received", hour=9, spine=spine) == 1.0


# -- persistence + recompute overwrite --------------------------------------

def test_recompute_persists_and_get_reads_back_across_restart(tmp_path):
    db_path = tmp_path / "argus.db"
    spine = _spine(tmp_path)
    _record(spine, "mail.received", time.time(), subject="a@x.com", key="a")

    RhythmStore(db_path).recompute(spine)

    reopened = RhythmStore(db_path)
    row = reopened.get("active_hours")
    assert row is not None
    assert row["samples"] >= 1


def test_recompute_overwrites_previous_value(tmp_path):
    db_path = tmp_path / "argus.db"
    spine = _spine(tmp_path)
    store = RhythmStore(db_path)

    _record(spine, "mail.received", time.time(), subject="a@x.com", key="a")
    store.recompute(spine)
    first_samples = store.get("active_hours")["samples"]

    _record(spine, "mail.received", time.time(), subject="b@x.com", key="b")
    store.recompute(spine)
    second_samples = store.get("active_hours")["samples"]

    assert second_samples == first_samples + 1


def test_get_unknown_rhythm_returns_none(tmp_path):
    assert _rhythm_store(tmp_path).get("active_hours") is None


# -- performance / zero LLM calls -------------------------------------------

def test_recompute_over_100k_observations_is_fast_and_makes_no_llm_calls(tmp_path):
    spine = _spine(tmp_path)
    base = time.time() - 20 * 86400
    rows = [
        (base + i * 15, "bulk", "focus.changed", f"App{i % 50} - x", "{}", 1.0, f"bulk-{i}")
        for i in range(100_000)
    ]
    with spine._lock:
        spine._conn.executemany(
            "INSERT OR IGNORE INTO observations (ts, source, kind, subject, payload, confidence, dedupe_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        spine._conn.commit()

    rhythms = _rhythm_store(tmp_path)
    start = time.monotonic()
    rhythms.recompute(spine)  # no LLM client is imported or called anywhere in this module
    elapsed = time.monotonic() - start

    assert elapsed < 5.0


def test_rhythm_store_survives_concurrent_reads_from_many_threads(tmp_path):
    """Found at the U-C4 gate. RhythmStore took a shared argus.db
    connection with no lock, and after U-C4 SalienceEngine.decide() reads
    it on every candidate -- from five worker threads at once, while the
    orchestrator uses that same connection for memory from its own thread.
    That connection is only safe under _interaction_lock, which covers
    none of those readers. Same P1 finding fixed for ThreadStore at the
    Phase B gate, reappearing here. Encodes the mechanism (concurrent
    access from independent threads), not a symptom."""
    import threading as _threading

    store = RhythmStore(tmp_path / "argus.db")
    store._save("active_hours", {"buckets": [0.5] * 24}, days_observed=30, samples=999, confidence=1.0)

    errors: list[Exception] = []

    def hammer():
        try:
            for _ in range(40):
                assert store.get("active_hours")["confidence"] == 1.0
        except Exception as exc:  # pragma: no cover - only on regression
            errors.append(exc)

    threads = [_threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, f"concurrent reads failed: {errors[0]!r}"
