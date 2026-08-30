"""Generic bridge from an external MCP server (a subprocess speaking MCP
over stdio) into Argus's own Tool/ToolRegistry shape (ROADMAP.md Phase 3).

Confirmed live as the actual motivating case: pure screenshot-and-guess
desktop control burned 20+ tool-call iterations, $0.26, and a malformed
click call trying to delete an email through a webmail UI before
delete_email existed. The same failure mode applies to any web task
without its own dedicated tool -- Playwright MCP fixes it for the web the
way list_ui_elements fixes it for the desktop: structured accessibility
data instead of pixel-coordinate guessing.

ClientSession's methods are async and the underlying subprocess/session
needs to stay alive across many tool calls (restarting a browser process
per call would be both slow and wasteful) -- so this runs its own asyncio
event loop on a dedicated background thread for the server's whole
lifetime, and bridges Argus's synchronous Tool.handler calls into it via
run_coroutine_threadsafe(...).result(timeout=...)."""

import asyncio
import logging
import threading

from argus.tools.base import PermissionTier, Tool

log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 30.0
_CALL_TIMEOUT_SECONDS = 60.0


class McpServerBridge:
    """Launches one external MCP server as a subprocess and exposes its
    tools as Argus Tool objects. One instance per server; construction
    blocks until the connection is up (or raises) so a caller never
    registers tools for a server that isn't actually reachable."""

    def __init__(self, command: str, args: list[str] | None = None):
        self._command = command
        self._args = args or []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session = None
        self._ready = threading.Event()
        self._start_error: Exception | None = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=_CONNECT_TIMEOUT_SECONDS):
            raise TimeoutError(f"MCP server '{command}' did not respond within {_CONNECT_TIMEOUT_SECONDS}s")
        if self._start_error is not None:
            raise self._start_error

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._connect())
        except Exception as e:
            self._start_error = e
            self._ready.set()
            return
        loop.run_forever()

    async def _connect(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self._command, args=self._args)
        # Both context managers are @asynccontextmanager-backed generators
        # -- confirmed live as a real bug: entering them via __aenter__()
        # without keeping a reference to the CM object itself let the
        # generator (and the subprocess/stdio pipes it's driving) get
        # garbage-collected the moment _connect() returned, closing the
        # connection before a single request could go out ("Connection
        # closed"). Held as instance attributes so they live exactly as
        # long as this bridge does; __aexit__ is never called (this
        # connection is meant to outlive the coroutine that opened it --
        # close() stops the loop instead, ending the subprocess with it).
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        session = await self._session_cm.__aenter__()
        await session.initialize()
        self._session = session
        self._ready.set()

    def _run_coroutine(self, coro, timeout: float):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def list_tools(self) -> list[dict]:
        """Provider-neutral tool descriptors -- same shape
        ToolRegistry.schemas() already produces for in-process tools."""
        result = self._run_coroutine(self._session.list_tools(), _CONNECT_TIMEOUT_SECONDS)
        return [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in result.tools
        ]

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._run_coroutine(self._session.call_tool(name, arguments), _CALL_TIMEOUT_SECONDS)
        parts = [block.text for block in result.content if getattr(block, "text", None)]
        text = "\n".join(parts) if parts else str(result)
        if getattr(result, "isError", False):
            return f"error: {text}"
        return text

    def build_tools(
        self,
        name_prefix: str = "",
        tier: PermissionTier = PermissionTier.CONFIRM,
        repeatable: bool = True,
        group: str | None = None,
    ) -> list[Tool]:
        """Wraps every tool this server currently advertises as an Argus
        Tool. repeatable+group default to True/set -- an external MCP
        server's actions (browser clicks, navigation) are exactly the
        "cheap, reversible, called many times in a row" shape repeatable
        tools already exist for (see Tool.repeatable's docstring) -- one
        confirmation should cover a whole browser task, not one per
        click, same as desktop control today."""
        tools = []
        for schema in self.list_tools():
            mcp_name = schema["name"]

            def handler(arguments: dict, _name: str = mcp_name) -> str:
                return self.call_tool(_name, arguments)

            tools.append(Tool(
                name=f"{name_prefix}{mcp_name}",
                description=schema["description"],
                input_schema=schema["input_schema"] or {"type": "object", "properties": {}},
                tier=tier,
                handler=handler,
                repeatable=repeatable,
                group=group,
            ))
        return tools

    def close(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
