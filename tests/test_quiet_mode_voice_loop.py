from argus.ui import commands as ui_commands
from argus.voice.loop import VoiceLoop


def test_speak_with_barge_in_skips_synthesis_in_quiet_mode():
    loop = VoiceLoop.__new__(VoiceLoop)  # bypass __init__ -- no mic/models needed for this
    ui_commands.set_quiet_mode(True)
    try:
        # If quiet mode weren't checked first, this would blow up trying to
        # use loop.speaker, which was never set.
        result = loop._speak_with_barge_in("this should not actually be spoken")
        assert result is False
    finally:
        ui_commands.set_quiet_mode(False)
