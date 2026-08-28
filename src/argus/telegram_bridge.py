import json
import logging
import threading
import time
import urllib.parse
import urllib.request

from argus.config import settings
from argus.ui import commands as ui_commands
from argus.ui import events as ui_events

log = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT_SECONDS = 30


def _api_call(method: str, **params) -> dict:
    url = _API_URL.format(token=settings.telegram_bot_token, method=method)
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=_POLL_TIMEOUT_SECONDS + 10) as resp:
        return json.loads(resp.read())


class TelegramBridge:
    """Lets Argus be reached from your phone via a Telegram bot instead of
    only being usable sitting at this PC (README roadmap item 12: "Argus
    is currently chained to this PC"). An incoming message is pushed onto
    the exact same ui_commands text-message queue the console's text-input
    box already uses -- VoiceLoop's external-input worker drains it either
    way, so a Telegram message gets full tool access, memory, and
    streaming replies for free, with zero duplicated orchestration logic.

    No inbound port is ever opened: this only makes outbound long-poll
    requests to Telegram's own servers (getUpdates), so there's nothing to
    port-forward or expose, and no TLS cert to manage."""

    def __init__(self):
        self._offset = 0
        self._allowed_chat_id = str(settings.telegram_allowed_chat_id) if settings.telegram_allowed_chat_id else None

    def start(self) -> None:
        if not settings.telegram_bot_token:
            return
        if not self._allowed_chat_id:
            log.warning(
                "TELEGRAM_BOT_TOKEN is set but TELEGRAM_ALLOWED_CHAT_ID is not -- "
                "Telegram bridge disabled (without an allowlist, anyone who finds "
                "the bot could command Argus)."
            )
            return
        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self._forward_replies_loop, daemon=True).start()
        log.info("Telegram bridge started")

    def _poll_loop(self) -> None:
        while True:
            try:
                result = _api_call("getUpdates", offset=self._offset, timeout=_POLL_TIMEOUT_SECONDS)
                for update in result.get("result", []):
                    self._offset = update["update_id"] + 1
                    self._handle_update(update)
            except Exception:
                log.exception("Telegram poll failed; retrying in 5s")
                time.sleep(5)

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if not text:
            return
        if chat_id != self._allowed_chat_id:
            log.warning("Ignoring Telegram message from unrecognized chat_id=%s", chat_id)
            return
        ui_commands.submit_text_message(text)

    def _forward_replies_loop(self) -> None:
        """Subscribes to the same in-process event bus the console uses and
        relays every Argus reply to the allowed chat. This is a single-
        owner personal assistant, so there's no ambiguity about who a
        reply is "for" -- every reply, regardless of whether it was
        triggered by voice, the console, or Telegram, goes to the one
        allowed chat."""
        event_queue = ui_events.subscribe()
        try:
            while True:
                event = event_queue.get()
                if event.get("type") == "transcript" and event.get("role") == "argus":
                    self._send(event.get("text", ""))
        finally:
            ui_events.unsubscribe(event_queue)

    def _send(self, text: str) -> None:
        if not text:
            return
        try:
            _api_call("sendMessage", chat_id=self._allowed_chat_id, text=text)
        except Exception:
            log.exception("Failed to send Telegram reply")
