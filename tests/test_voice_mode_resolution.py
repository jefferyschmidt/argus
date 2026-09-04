"""PRD.md §19 unit 40 (Part 1, item 1): VOICE_MODE unset resolves to
realtime when an OpenAI key is present, pipeline otherwise. VOICE_MODE
explicitly set is never overridden either way.

Each case constructs a fresh Settings() rather than monkeypatching the
module singleton's attributes -- model_fields_set (what "explicitly
set" means here) is only populated by pydantic-settings at construction
time from env/.env/kwargs, not by a later plain attribute assignment,
so a monkeypatched singleton could never exercise the "explicitly set"
branch honestly."""

from argus.config import Settings, resolved_voice_mode

# This repo's own .env sets VOICE_MODE=realtime for real interactive use
# (unit 33/34 having landed) -- _env_file=None keeps every "unset" case
# below honest about what a genuinely bare environment resolves to,
# rather than silently testing against this developer's own dotenv.


def test_unset_with_a_key_present_resolves_to_realtime():
    settings = Settings(_env_file=None, openai_api_key="sk-test")
    assert resolved_voice_mode(settings) == "realtime"


def test_unset_with_no_key_resolves_to_pipeline():
    settings = Settings(_env_file=None, openai_api_key="")
    assert resolved_voice_mode(settings) == "pipeline"


def test_explicit_pipeline_is_never_overridden_even_with_a_key_present():
    settings = Settings(_env_file=None, openai_api_key="sk-test", voice_mode="pipeline")
    assert resolved_voice_mode(settings) == "pipeline"


def test_explicit_realtime_is_respected_even_with_no_key():
    """Not a live-usable configuration (RealtimeVoiceLoop itself still
    raises without a key) -- but resolution must not silently second-
    guess an explicit VOICE_MODE=realtime by downgrading it."""
    settings = Settings(_env_file=None, openai_api_key="", voice_mode="realtime")
    assert resolved_voice_mode(settings) == "realtime"


def test_default_argument_reads_the_module_singleton():
    from argus.config import settings as module_settings

    assert resolved_voice_mode() == resolved_voice_mode(module_settings)
