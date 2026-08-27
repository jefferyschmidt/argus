import argparse
import logging

from rich.console import Console

from argus.memory.manager import MemoryManager
from argus.orchestrator import Orchestrator

console = Console()


def chat() -> None:
    orch = Orchestrator()
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
        console.print(f"[bold cyan]argus>[/bold cyan] {reply}\n")


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
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(prog="argus")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("chat", help="Start an interactive chat session")

    memory_parser = sub.add_parser("memory", help="Memory management")
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    memory_sub.add_parser("review", help="Review agent-proposed core memories")

    args = parser.parse_args()

    if args.command == "memory" and args.memory_command == "review":
        memory_review()
    else:
        chat()


if __name__ == "__main__":
    main()
