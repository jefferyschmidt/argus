import argparse
import ctypes
import logging
import re
import sys

from rich.console import Console

from argus.logging_config import setup_logging
from argus.memory.manager import MemoryManager
from argus.orchestrator import Orchestrator

console = Console()
log = logging.getLogger(__name__)


def _maybe_start_ui_server(port: int = 8765) -> None:
    """Best-effort: the visual console runs in-process (it reads live state
    off an in-memory event bus), so it only has anything to show when
    started alongside chat/voice, not as a standalone command."""
    try:
        from argus.ui.server import run as run_ui
    except ImportError:
        console.print('[dim](Visual console not installed -- pip install -e ".[ui]" to enable it)[/dim]')
        return

    import threading
    import webbrowser

    threading.Thread(target=run_ui, kwargs={"port": port}, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    console.print(f"[dim]Visual console: {url}[/dim]")
    try:
        # open_new(), not open() -- open() lets the browser decide, which in
        # practice almost always means a new tab in whatever window's
        # already focused, burying the console behind chat/terminal windows.
        # open_new() asks for an actual separate window instead.
        webbrowser.open_new(url)
    except Exception:
        pass


def _chat_announce(text: str) -> bool:
    """PRD §19 unit 40 (Part 1): chat has no voice output -- a proactive
    item (a reminder, an escalation follow-up, any salience-arbitrated
    announcement) surfaces as plain text instead, printed the same way
    an ordinary reply is. SalienceDispatcher._deliver() doesn't inspect
    this return value; the True matches the other speak_fns' contract."""
    console.print(f"\n[bold cyan]argus>[/bold cyan] {text}\n")
    return True


def _start_chat_proactive(orch: Orchestrator, interaction_lock):
    """PRD §19 unit 40 (Part 1): "today chat starts none of it" -- no
    ProactiveEngine at all, so reminders/threads/rules never ran in
    chat. Wrapped here (not inside start_proactive_engine itself, which
    voice's own construction relies on failing loudly same as always)
    because this is the first entry point where an Orchestrator
    conversation is expected to work with NO proactive layer at all --
    a construction failure must degrade to that, not take chat down.
    Split out from chat() itself so this wiring is directly testable
    without driving chat()'s own blocking console.input() REPL loop."""
    try:
        from argus.proactive_engine import start_proactive_engine
        return start_proactive_engine(orch, _chat_announce, interaction_lock)
    except Exception:
        log.exception("Could not start the proactive layer for chat -- continuing without it")
        return None


def chat() -> None:
    import threading

    orch = Orchestrator()
    interaction_lock = threading.Lock()
    _start_chat_proactive(orch, interaction_lock)
    _maybe_start_ui_server()
    console.print("[bold cyan]Argus[/bold cyan] online. Type 'exit' to quit.\n")
    while True:
        try:
            user_text = console.input("[bold green]you>[/bold green] ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_text.strip().lower() in {"exit", "quit"}:
            break
        if not user_text.strip():
            continue
        with interaction_lock:
            reply = orch.handle(user_text)
        tag = f"[dim]({orch.last_tier.value}: {orch.last_model})[/dim]"
        console.print(f"[bold cyan]argus>[/bold cyan] {reply} {tag}\n")


def voice() -> None:
    from argus.config import resolved_voice_mode

    # PRD §19 unit 40: realtime is the default, actively-developed mode
    # once an OpenAI key is present; pipeline is kept only as the
    # unmaintained fallback (no realtime API dependency). VOICE_MODE
    # explicitly set overrides this resolution either way.
    try:
        if resolved_voice_mode() == "realtime":
            from argus.voice.realtime import RealtimeVoiceLoop
            loop = RealtimeVoiceLoop()
        else:
            from argus.voice.loop import VoiceLoop
            loop = VoiceLoop()
    except ImportError as e:
        console.print(
            f"[red]Voice dependencies not installed:[/red] {e}\n"
            "Install with: pip install -e \".[voice]\""
        )
        return
    _maybe_start_ui_server()

    # Remote access (README item 12): wired into voice mode specifically,
    # not chat, since voice already runs an always-on background worker
    # draining the same text-message queue a Telegram message is pushed
    # onto -- chat's input loop is a plain synchronous console.input(),
    # with no consumer that could pick up a message arriving from
    # elsewhere. No-ops entirely if TELEGRAM_BOT_TOKEN isn't set.
    from argus.telegram_bridge import TelegramBridge
    TelegramBridge().start()

    try:
        loop.run()
    except KeyboardInterrupt:
        pass


def agent(goal: str) -> None:
    from argus.agent.runner import AgentRunner

    # I6: the Orchestrator's own full registry (rules, AuthorizationChecker,
    # decision_log, spine wired in), matching how tasks/worker.py and
    # voice/realtime.py already do it -- never a bare AgentRunner() that
    # would fall through to a bare build_default_registry(...) with none of that.
    orch = Orchestrator()
    runner = AgentRunner(tool_registry=orch.tools)
    console.print(f"[bold cyan]Argus[/bold cyan] working autonomously on:\n  {goal}\n")
    result = runner.run(goal)
    console.print(f"\n[bold cyan]Result>[/bold cyan] {result}")
    console.print(f"[dim]Audit log: {runner.audit.path}[/dim]")


def memory_review() -> None:
    mem = MemoryManager()
    pending = mem.core.list_pending()
    if not pending:
        console.print("No pending core memories.")
        return
    for row in pending:
        console.print(f"\n[yellow]#{row['id']}[/yellow] ({row['ts']}): {row['content']}")
        choice = console.input("Confirm as core memory? [y/N/d(elete)] ").strip().lower()
        if choice == "y":
            mem.core.confirm(row["id"])
            console.print("  confirmed.")
        elif choice == "d":
            mem.core.reject(row["id"])
            console.print("  deleted.")
        else:
            console.print("  skipped (left pending).")


def memory_export(path: str) -> None:
    import json
    from pathlib import Path

    mem = MemoryManager()
    data = mem.export_all()
    out_path = Path(path)
    out_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    console.print(
        f"Exported {len(data['episodic'])} episodic entries, "
        f"{len(data['semantic'])} semantic entries, "
        f"{len(data['core_confirmed'])} confirmed + {len(data['core_pending'])} pending core memories "
        f"to [bold]{out_path}[/bold]"
    )


def memory_forget() -> None:
    console.print(
        "[yellow]This permanently deletes all conversation history (episodic + semantic memory).[/yellow]\n"
        "Core memories (confirmed standing facts) are NOT affected -- manage those individually with "
        "'argus memory review'.\n"
        "Consider 'argus memory export <path>' first if you want a copy."
    )
    choice = console.input("Type 'forget everything' to confirm: ").strip()
    if choice != "forget everything":
        console.print("Cancelled -- nothing was deleted.")
        return
    mem = MemoryManager()
    result = mem.forget_everything_except_core()
    console.print(
        f"Deleted {result['episodic_deleted']} episodic entries and "
        f"{result['semantic_deleted']} semantic entries."
    )


def journal_list(query: str | None) -> None:
    from argus.memory.journal import JournalStore
    from argus.memory.store import get_connection

    conn = get_connection()
    try:
        store = JournalStore(conn)
        rows = store.search(query, limit=50) if query else store.list_recent(limit=50)
    finally:
        conn.close()

    if not rows:
        console.print("No matching journal entries." if query else "No journal entries yet.")
        return
    for row in rows:
        console.print(f"[dim]{row['ts']}[/dim]  {row['text']}")


def backup_create(path: str) -> None:
    import getpass
    from pathlib import Path

    from argus.backup import create_backup

    passphrase = getpass.getpass("Backup passphrase (remember this -- there's no recovery without it): ")
    if not passphrase:
        console.print("[red]Passphrase can't be empty -- nothing was created.[/red]")
        return
    if passphrase != getpass.getpass("Confirm passphrase: "):
        console.print("[red]Passphrases didn't match -- nothing was created.[/red]")
        return

    result = create_backup(Path(path), passphrase)
    console.print(
        f"Backup created: [bold]{result['path']}[/bold] "
        f"({result['entries']} files, {result['size_bytes']} bytes, encrypted)"
    )


def backup_restore(path: str) -> None:
    import getpass
    from pathlib import Path

    from argus.backup import WrongPassphraseOrCorruptBackup, restore_backup

    console.print(
        "[yellow]This overwrites your current memory (sqlite db, semantic memory, "
        "workspace files) with the contents of this backup.[/yellow]"
    )
    if console.input("Type 'restore' to confirm: ").strip() != "restore":
        console.print("Cancelled -- nothing was restored.")
        return

    passphrase = getpass.getpass("Backup passphrase: ")
    try:
        result = restore_backup(Path(path), passphrase)
    except (WrongPassphraseOrCorruptBackup, FileNotFoundError) as e:
        console.print(f"[red]{e}[/red]")
        return
    console.print(f"Restored {result['entries']} files from backup.")


def calendar_auth() -> None:
    from argus.google_calendar import NotAuthorized, is_configured, run_oauth_flow

    if not is_configured():
        console.print(
            "[red]GOOGLE_CALENDAR_CLIENT_ID and GOOGLE_CALENDAR_CLIENT_SECRET must be set in .env first.[/red]\n"
            "From Google Cloud Console: enable the Calendar API, create an OAuth client "
            "(type: Desktop app), and put the client ID/secret in .env."
        )
        return
    console.print("Opening a browser for Google sign-in and consent...")
    try:
        run_oauth_flow()
    except NotAuthorized as e:
        console.print(f"[red]{e}[/red]")
        return
    console.print("[green]Authorized.[/green] Argus can now read and create calendar events.")


_SINCE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)([smhd])")
_SINCE_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_since(value: str) -> float:
    """"24h", "30m", "7d", "90s" -> a unix epoch timestamp that far in the
    past. Kept deliberately simple (no full duration-string library) --
    this is the only format `argus timeline --since` needs to accept."""
    import time

    match = _SINCE_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid --since value {value!r} (expected e.g. 24h, 30m, 7d)")
    amount, unit = match.groups()
    return time.time() - float(amount) * _SINCE_UNIT_SECONDS[unit]


def timeline(since: str | None, kind: str | None, limit: int) -> None:
    """PRD.md §3.6 -- the proof Phase A works, independently useful on
    its own as a way to see what Argus has actually observed."""
    from datetime import datetime, timezone

    from argus.spine.store import SpineStore

    try:
        since_ts = _parse_since(since) if since else None
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return

    store = SpineStore()
    observations = store.query(kinds=[kind] if kind else None, since=since_ts, limit=limit)
    if not observations:
        console.print("No observations recorded yet.")
        return
    from rich.markup import escape

    for obs in observations:
        when = datetime.fromtimestamp(obs.ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        # escape() the whole bracketed segment, not just obs.subject --
        # Console.print() treats [...] as markup, and it's the literal
        # brackets added here (not just characters that might appear
        # inside an observed subject) that need protecting, or the
        # subject is silently swallowed as an unrecognized style tag.
        subject = escape(f" [{obs.subject}]") if obs.subject else ""
        console.print(f"[dim]{when}[/dim] {obs.kind}{subject}  [dim]({obs.source})[/dim]")


def held(action: str, item_id: int | None) -> None:
    """PRD.md §5.4 -- nothing is ever silently dropped; until Phase H
    renders held_items, this is how you see what's waiting."""
    from datetime import datetime

    from argus.salience.held import HeldQueue
    from rich.markup import escape

    queue = HeldQueue()

    if action == "dismiss":
        if item_id is None:
            console.print("[red]Usage: argus held dismiss <id>[/red]")
            return
        if queue.dismiss(item_id):
            console.print(f"Dismissed held item #{item_id}.")
        else:
            console.print(f"[red]No pending held item with id {item_id}.[/red]")
        return

    items = queue.pending()
    if not items:
        console.print("Nothing held.")
        return
    for item in items:
        when = datetime.fromtimestamp(item.created_ts).strftime("%Y-%m-%d %H:%M:%S")
        subject = escape(f" [{item.subject}]") if item.subject else ""
        console.print(f"[dim]#{item.id} {when}[/dim] {escape(item.text)}{subject}  [dim](score {item.score:.2f}, {item.kind})[/dim]")


def _make_dpi_aware() -> None:
    """Without this, a scaled Windows display (anything above 100%) puts
    pyautogui's screenshot/click coordinates in two DIFFERENT spaces --
    confirmed live as the actual cause of a real reported bug (Argus
    "clicked" but nothing happened): this process reported the screen as
    1536x864 (GetSystemMetrics, DPI-virtualized) while pyautogui.screenshot()
    was capturing the real physical 1920x1080 (125% scaling) -- so a
    coordinate read off a screenshot was consistently off by the scaling
    factor when clicked. Must be called once, before pyautogui/pygetwindow
    are used anywhere (desktop.py imports them lazily inside each tool
    function specifically so this can run first)."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        log.warning("Failed to set process DPI awareness -- desktop-control clicks may be misaligned on a scaled display")


def main() -> None:
    _make_dpi_aware()
    setup_logging()
    parser = argparse.ArgumentParser(prog="argus")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("chat", help="Start an interactive chat session")
    sub.add_parser(
        "voice",
        help="Start voice mode -- realtime (native, continuous) by default when an OpenAI key is "
        "set; pipeline is the fallback (local STT/TTS, no realtime API)",
    )

    agent_parser = sub.add_parser("agent", help="Run an autonomous goal")
    agent_parser.add_argument("goal", help="What Argus should figure out and do")

    memory_parser = sub.add_parser("memory", help="Memory management")
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    memory_sub.add_parser("review", help="Review agent-proposed core memories")
    export_parser = memory_sub.add_parser("export", help="Export all memory (core, episodic, semantic) to a JSON file")
    export_parser.add_argument("path", help="Output file path")
    memory_sub.add_parser("forget", help="Permanently delete all episodic + semantic memory (core memory untouched)")

    journal_parser = sub.add_parser("journal", help="View voice-journal entries")
    journal_parser.add_argument("query", nargs="?", default=None, help="Optional search text")

    backup_parser = sub.add_parser("backup", help="Create an encrypted backup of all memory")
    backup_parser.add_argument("path", help="Output file path for the backup")

    restore_parser = sub.add_parser("restore", help="Restore memory from an encrypted backup")
    restore_parser.add_argument("path", help="Path to the backup file")

    calendar_parser = sub.add_parser("calendar", help="Google Calendar setup")
    calendar_sub = calendar_parser.add_subparsers(dest="calendar_command")
    calendar_sub.add_parser("auth", help="One-time OAuth authorization for Google Calendar")

    timeline_parser = sub.add_parser("timeline", help="Show recent spine observations (PRD.md Phase A)")
    timeline_parser.add_argument("--since", default=None, help="e.g. 24h, 30m, 7d")
    timeline_parser.add_argument("--kind", default=None, help="Filter to one observation kind, e.g. mail.received")
    timeline_parser.add_argument("--limit", type=int, default=50)

    held_parser = sub.add_parser("held", help="Show items salience held back rather than spoke (PRD.md §5.4)")
    held_sub = held_parser.add_subparsers(dest="held_command")
    dismiss_parser = held_sub.add_parser("dismiss", help="Dismiss a held item")
    dismiss_parser.add_argument("id", type=int)

    args = parser.parse_args()

    if args.command == "memory" and args.memory_command == "review":
        memory_review()
    elif args.command == "memory" and args.memory_command == "export":
        memory_export(args.path)
    elif args.command == "memory" and args.memory_command == "forget":
        memory_forget()
    elif args.command == "memory":
        memory_parser.print_help()
    elif args.command == "journal":
        journal_list(args.query)
    elif args.command == "backup":
        backup_create(args.path)
    elif args.command == "restore":
        backup_restore(args.path)
    elif args.command == "calendar" and args.calendar_command == "auth":
        calendar_auth()
    elif args.command == "calendar":
        calendar_parser.print_help()
    elif args.command == "timeline":
        timeline(args.since, args.kind, args.limit)
    elif args.command == "held" and args.held_command == "dismiss":
        held("dismiss", args.id)
    elif args.command == "held":
        held("list", None)
    elif args.command == "voice":
        voice()
    elif args.command == "agent":
        agent(args.goal)
    else:
        chat()


if __name__ == "__main__":
    main()
