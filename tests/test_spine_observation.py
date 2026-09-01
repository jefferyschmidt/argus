from argus.spine.observation import KINDS, Observation


def test_observation_defaults():
    obs = Observation(source="test", kind="focus.changed", ts=1000.0)
    assert obs.subject is None
    assert obs.payload == {}
    assert obs.confidence == 1.0
    assert obs.dedupe_key is None


def test_observation_kinds_cover_the_prd_vocabulary():
    for kind in (
        "focus.changed", "mail.received", "calendar.event_upcoming",
        "reminder.due", "routine.due", "git.commit", "git.branch_stale",
        "argus.integration_failed", "argus.credential_failed", "argus.spend_recorded",
        "task.finished", "document.composed", "thread.opened",
    ):
        assert kind in KINDS
