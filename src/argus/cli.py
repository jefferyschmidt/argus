import argparse

from rich.console import Console

from argus.logging_config import setup_logging
from argus.memory.manager import MemoryManager
from argus.orchestrator import Orchestrator

console = Console()


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
        webbrowser.open(url)
    except Exception:
        pass


def chat() -> None:
    orch = Orchestrator()
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
        reply = orch.handle(user_text)
        tag = f"[dim]({orch.last_tier.value}: {orch.last_model})[/dim]"
        console.print(f"[bold cyan]argus>[/bold cyan] {reply} {tag}\n")


def voice() -> None:
    from argus.voice.loop import VoiceLoop

    try:
        loop = VoiceLoop()
    except ImportError as e:
        console.print(
            f"[red]Voice dependencies not installed:[/red] {e}\n"
            "Install with: pip install -e \".[voice]\""
        )
        return
    _maybe_start_ui_server()
    try:
        loop.run()
    except KeyboardInterrupt:
        pass


def agent(goal: str) -> None:
    from argus.agent.runner import AgentRunner

    runner = AgentRunner()
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


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(prog="argus")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("chat", help="Start an interactive chat session")
    sub.add_parser("voice", help="Start wake-word voice mode")

    agent_parser = sub.add_parser("agent", help="Run an autonomous goal")
    agent_parser.add_argument("goal", help="What Argus should figure out and do")

    memory_parser = sub.add_parser("memory", help="Memory management")
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    memory_sub.add_parser("review", help="Review agent-proposed core memories")

    args = parser.parse_args()

    if args.command == "memory" and args.memory_command == "review":
        memory_review()
    elif args.command == "voice":
        voice()
    elif args.command == "agent":
        agent(args.goal)
    else:
        chat()


if __name__ == "__main__":
    main()
