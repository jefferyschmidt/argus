"""Generic bridge from an external MCP server -- a local subprocess (stdio
transport, e.g. Playwright MCP) or a remote hosted server (streamable
HTTP, e.g. Zapier/Home Assistant's own MCP endpoints) -- into Argus's own
Tool/ToolRegistry shape (ROADMAP.md Phase 3/4).

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
    """Launches one external MCP server -- stdio subprocess (pass
    `command`/`args`) or a remote HTTP server (pass `url`, optionally
    `headers` for auth) -- and exposes its tools as Argus Tool objects.
    Exactly one of `command` or `url` must be given. One instance per
    server; construction blocks until the connection is up (or raises) so
    a caller never registers tools for a server that isn't actually
    reachable."""

    def __init__(
        self,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        if (command is None) == (url is None):
            raise ValueError("McpServerBridge needs exactly one of command= or url=")
        self._command = command
        self._args = args or []
        self._url = url
        self._headers = headers
        self._label = command or url
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session = None
        self._ready = threading.Event()
        self._start_error: Exception | None = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=_CONNECT_TIMEOUT_SECONDS):
            raise TimeoutError(f"MCP server '{self._label}' did not respond within {_CONNECT_TIMEOUT_SECONDS}s")
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
        if self._command is not None:
            await self._connect_stdio()
        else:
            await self._connect_http()

    async def _open_session(self, read, write) -> None:
        """Shared by both transports: wraps the raw streams in a
        ClientSession, initializes it, and publishes it -- held as an
        instance attribute (not a local returned from _connect) for the
        same reason the transport-specific context managers are (see
        _connect_stdio): letting it fall out of scope would let it get
        garbage-collected and close the connection."""
        from mcp import ClientSession

        self._session_cm = ClientSession(read, write)
        session = await self._session_cm.__aenter__()
        await session.initialize()
        self._session = session
        self._ready.set()

    async def _connect_stdio(self) -> None:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self._command, args=self._args)
        # An @asynccontextmanager-backed generator -- confirmed live as a
        # real bug: entering it via __aenter__() without keeping a
        # reference to the CM object itself let the generator (and the
        # subprocess/stdio pipes it's driving) get garbage-collected the
        # moment _connect() returned, closing the connection before a
        # single request could go out ("Connection closed"). Held as an
        # instance attribute so it lives exactly as long as this bridge
        # does; __aexit__ is never called (this connection is meant to
        # outlive the coroutine that opened it -- close() stops the loop
        # instead, ending the subprocess with it).
        self._transport_cm = stdio_client(params)
        read, write = await self._transport_cm.__aenter__()
        await self._open_session(read, write)

    async def _connect_http(self) -> None:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        http_client = httpx2.AsyncClient(headers=self._headers) if self._headers else None
        self._transport_cm = streamable_http_client(self._url, http_client=http_client)
        read, write = await self._transport_cm.__aenter__()
        await self._open_session(read, write)

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
