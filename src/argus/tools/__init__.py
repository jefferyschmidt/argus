from argus.tools.base import PermissionTier, Tool
from argus.tools.desktop import (
    capture_camera_tool,
    click_tool,
    list_windows_tool,
    open_app_tool,
    press_key_tool,
    take_screenshot_tool,
    type_text_tool,
)
from argus.tools.email import list_recent_emails_tool, send_email_tool
from argus.tools.filesystem import list_dir_tool, read_file_tool, run_shell_tool, write_file_tool
from argus.tools.journal import search_journal_tool
from argus.tools.registry import ToolRegistry, console_confirmer
from argus.tools.reminders import cancel_reminder_tool, list_reminders_tool, set_reminder_tool
from argus.tools.routines import (
    cancel_scheduled_routine_tool,
    create_scheduled_routine_tool,
    list_scheduled_routines_tool,
)
from argus.tools.self_improve import (
    commit_own_changes_tool,
    list_own_source_tool,
    read_own_source_tool,
    restart_argus_tool,
    run_own_tests_tool,
    write_own_source_tool,
)
from argus.tools.web_content import fetch_image_tool


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        read_file_tool,
        list_dir_tool,
        write_file_tool,
        run_shell_tool,
        take_screenshot_tool,
        capture_camera_tool,
        list_windows_tool,
        click_tool,
        type_text_tool,
        press_key_tool,
        open_app_tool,
        fetch_image_tool,
        set_reminder_tool,
        list_reminders_tool,
        cancel_reminder_tool,
        search_journal_tool,
        list_recent_emails_tool,
        send_email_tool,
        create_scheduled_routine_tool,
        list_scheduled_routines_tool,
        cancel_scheduled_routine_tool,
        read_own_source_tool,
        list_own_source_tool,
        write_own_source_tool,
        run_own_tests_tool,
        commit_own_changes_tool,
        restart_argus_tool,
    ):
        registry.register(tool)
    return registry


__all__ = [
    "PermissionTier",
    "Tool",
    "ToolRegistry",
    "console_confirmer",
    "build_default_registry",
]
