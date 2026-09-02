import logging

from argus.config import settings
from argus.tools.base import PermissionTier, Tool
from argus.tools.calendar import create_calendar_event_tool, list_calendar_events_tool
from argus.tools.desktop import (
    capture_camera_tool,
    click_tool,
    list_ui_elements_tool,
    list_windows_tool,
    open_app_tool,
    press_key_tool,
    scroll_tool,
    take_screenshot_tool,
    type_text_tool,
)
from argus.tools.email import (
    delete_email_tool,
    list_recent_emails_tool,
    send_email_tool,
    unsubscribe_from_email_tool,
)
from argus.tools.filesystem import list_dir_tool, read_file_tool, run_shell_tool, write_file_tool
from argus.tools.ingest import ingest_document_tool
from argus.tools.journal import search_journal_tool
from argus.tools.knowledge_graph import (
    forget_relationship_tool,
    query_relationships_tool,
    remember_relationship_tool,
)
from argus.tools.memory_review import (
    confirm_core_memory_tool,
    delete_core_memory_tool,
    list_core_memories_tool,
    list_pending_core_memories_tool,
    reject_core_memory_tool,
)
from argus.tools.registry import ToolRegistry, console_confirmer
from argus.tools.reminders import cancel_reminder_tool, list_reminders_tool, set_reminder_tool
from argus.tools.research_topics import (
    list_research_topics_tool,
    track_research_topic_tool,
    untrack_research_topic_tool,
)
from argus.tools.routines import (
    cancel_scheduled_routine_tool,
    create_scheduled_routine_tool,
    list_scheduled_routines_tool,
)
from argus.tools.scan_document import _build_scan_document
from argus.tools.second_opinion import _build_second_opinion
from argus.tools.rules import (
    _build_activate_mode,
    _build_deactivate_mode,
    _build_explain_last_action,
    _build_list_rules,
    _build_revoke_rule,
)
from argus.tools.self_improve import (
    commit_own_changes_tool,
    list_own_source_tool,
    read_own_source_tool,
    restart_argus_tool,
    run_own_tests_tool,
    write_own_source_tool,
)
from argus.tools.tasks import _build_cancel_task, _build_start_task, _build_task_status
from argus.tools.undo import list_recent_writes_tool, undo_last_write_tool
from argus.tools.web_content import close_show_window_tool, fetch_image_tool, show_website_tool

log = logging.getLogger(__name__)


def build_default_registry(router=None, task_runner=None, rule_store=None, decision_log=None) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        read_file_tool,
        list_dir_tool,
        write_file_tool,
        run_shell_tool,
        take_screenshot_tool,
        capture_camera_tool,
        list_windows_tool,
        list_ui_elements_tool,
        click_tool,
        type_text_tool,
        press_key_tool,
        scroll_tool,
        open_app_tool,
        fetch_image_tool,
        show_website_tool,
        close_show_window_tool,
        set_reminder_tool,
        list_reminders_tool,
        cancel_reminder_tool,
        search_journal_tool,
        list_recent_emails_tool,
        send_email_tool,
        unsubscribe_from_email_tool,
        delete_email_tool,
        list_calendar_events_tool,
        create_calendar_event_tool,
        create_scheduled_routine_tool,
        list_scheduled_routines_tool,
        cancel_scheduled_routine_tool,
        read_own_source_tool,
        list_own_source_tool,
        write_own_source_tool,
        run_own_tests_tool,
        commit_own_changes_tool,
        restart_argus_tool,
        undo_last_write_tool,
        list_recent_writes_tool,
        ingest_document_tool,
        track_research_topic_tool,
        list_research_topics_tool,
        untrack_research_topic_tool,
        remember_relationship_tool,
        query_relationships_tool,
        forget_relationship_tool,
        list_core_memories_tool,
        list_pending_core_memories_tool,
        confirm_core_memory_tool,
        reject_core_memory_tool,
        delete_core_memory_tool,
    ):
        registry.register(tool)

    # second_opinion and scan_document both need live access to a
    # ModelRouter (for the frontier tier and, just as importantly, the SAME
    # cost governor/daily cap the rest of the conversation uses -- a
    # second, separate ModelRouter would let their LLM calls silently
    # bypass that cap). Only registered when a router is actually supplied.
    if router is not None:
        registry.register(_build_second_opinion(router))
        registry.register(_build_scan_document(router))

    # Phase I autonomous tasks (PRD §6): off by default (enable_task_runner,
    # §9) -- only registered when both the flag is on and a live TaskRunner
    # was actually supplied, same "only if the collaborator exists" pattern
    # as second_opinion/scan_document above. A fresh TaskRunner per call
    # would lose the worker pool's concurrency bookkeeping, so this never
    # builds one itself -- see Orchestrator.__init__ for where the one
    # shared instance is constructed.
    if settings.enable_task_runner and task_runner is not None:
        registry.register(_build_start_task(task_runner))
        registry.register(_build_task_status(task_runner))
        registry.register(_build_cancel_task(task_runner))

    # Phase G introspection (PRD §7.6) -- not gated by a feature flag
    # (Phase G has never had one, unlike Phase I); only registered when
    # the collaborators actually exist, same "don't build one here"
    # pattern as everything else in this function.
    if rule_store is not None:
        registry.register(_build_list_rules(rule_store))
        registry.register(_build_revoke_rule(rule_store))
        registry.register(_build_activate_mode(rule_store))
        registry.register(_build_deactivate_mode(rule_store))
    if decision_log is not None:
        registry.register(_build_explain_last_action(decision_log))

    # Plugin tools (README item 13): auto-discovered from argus/plugins/,
    # no core-code changes needed to add a new one. A plugin can't
    # override a built-in by name -- skip and warn rather than letting an
    # unreviewed drop-in file silently replace a reviewed core tool.
    from argus.plugin_loader import load_plugin_tools

    for tool in load_plugin_tools():
        if tool.name in registry._tools:
            log.warning("Plugin tool '%s' has the same name as an existing tool -- skipping", tool.name)
            continue
        registry.register(tool)

    # ROADMAP.md Phase 3-6: every optional external MCP server this
    # registry can include. Connect-wrap-register-or-skip-and-warn is
    # identical for all seven (an unreachable/misconfigured server must
    # never take down every other tool Argus has, same spirit as the
    # plugin loader above) -- only how each one's bridge actually gets
    # built differs (stdio vs URL, env vars vs headers), which is what the
    # per-service `build` lambdas below capture. See _wire_mcp_server.
    #
    # Imported here, not at module level -- tests patch
    # "argus.mcp_bridge.McpServerBridge", which only takes effect for a
    # lookup that happens at call time (inside this function, each time
    # it runs), not one bound once at module-import time.
    from argus.mcp_bridge import McpServerBridge

    for enabled, label, prefix, group, build, hint in (
        (
            settings.enable_playwright_mcp, "Playwright", "playwright_", "playwright_mcp",
            lambda: McpServerBridge("npx", ["-y", "@playwright/mcp@latest", "--headless"]), "",
        ),
        (
            # Account-specific dashboard-generated URLs (Zapier, Home
            # Assistant) vs. fixed documented endpoints (GitHub, Figma,
            # below) -- both shapes end up as url=/headers= either way.
            bool(settings.zapier_mcp_url), "Zapier", "zapier_", "zapier_mcp",
            lambda: McpServerBridge(
                url=settings.zapier_mcp_url,
                headers={"Authorization": f"Bearer {settings.zapier_mcp_api_key}"} if settings.zapier_mcp_api_key else None,
            ), "",
        ),
        (
            bool(settings.home_assistant_mcp_url), "Home Assistant", "home_assistant_", "home_assistant_mcp",
            lambda: McpServerBridge(
                url=settings.home_assistant_mcp_url,
                headers={"Authorization": f"Bearer {settings.home_assistant_mcp_token}"} if settings.home_assistant_mcp_token else None,
            ), "",
        ),
        (
            settings.enable_github_mcp, "GitHub", "github_", "github_mcp",
            lambda: McpServerBridge(
                url=settings.github_mcp_url,
                headers={"Authorization": f"Bearer {settings.github_mcp_token}"} if settings.github_mcp_token else None,
            ), "",
        ),
        (
            settings.enable_figma_mcp, "Figma", "figma_", "figma_mcp",
            lambda: McpServerBridge(url=settings.figma_mcp_url),
            # Confirmed as the expected common case, not just theoretical:
            # this fails whenever the Figma desktop app isn't running with
            # Dev Mode's MCP server enabled.
            "desktop app not running with Dev Mode MCP enabled?",
        ),
        (
            settings.enable_stability_mcp, "Stability AI", "stability_", "stability_mcp",
            lambda: McpServerBridge("npx", ["-y", "mcp-server-stability-ai"], env={
                "STABILITY_AI_API_KEY": settings.stability_ai_api_key,
                "IMAGE_STORAGE_DIRECTORY": str(settings.workspace_dir),
            }), "",
        ),
        (
            settings.enable_spotify_mcp, "Spotify", "spotify_", "spotify_mcp",
            lambda: McpServerBridge("npx", ["-y", "@tbrgeek/spotify-mcp-server"]), "",
        ),
    ):
        if enabled:
            _wire_mcp_server(registry, label, prefix, group, build, hint)

    return registry


def _wire_mcp_server(registry: ToolRegistry, label: str, name_prefix: str, group: str, build_bridge, hint: str = "") -> None:
    """Shared connect/wrap/register/skip-and-warn plumbing for one
    optional external MCP server (ROADMAP.md Phase 3-6) -- see the
    call site above for why this exists as a helper rather than seven
    near-identical try/except blocks."""
    try:
        bridge = build_bridge()
        for tool in bridge.build_tools(name_prefix=name_prefix, group=group):
            registry.register(tool)
    except Exception:
        suffix = f" ({hint})" if hint else ""
        log.exception("%s MCP server unreachable%s -- its tools are unavailable this session", label, suffix)


__all__ = [
    "PermissionTier",
    "Tool",
    "ToolRegistry",
    "console_confirmer",
    "build_default_registry",
]
