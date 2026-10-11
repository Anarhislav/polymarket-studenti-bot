"""Telegram notifications.

Messages are sent from a background thread through a bounded queue, so a slow or unreachable
Telegram never delays trading; if the queue is full, new messages are dropped and logged.
The bot token is a secret: it is read from the environment and never logged.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
QUEUE_SIZE = 200
MAX_ATTEMPTS = 4


class NullNotifier:
    """Used when Telegram is not configured: every call is a no-op."""

    def send(self, text: str) -> None:
        pass

    def close(self, timeout: float = 5.0) -> None:
        pass


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, prefix: str = ""):
        self._url = API_URL.format(token=token)
        self._chat_id = chat_id
        self._prefix = prefix
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._thread = threading.Thread(target=self._run, name="telegram", daemon=True)
        self._thread.start()

    def send(self, text: str) -> None:
        message = f"{self._prefix}{text}" if self._prefix else text
        try:
            self._queue.put_nowait(message)
        except queue.Full:
            log.warning("telegram queue full, dropping a notification")

    def close(self, timeout: float = 5.0) -> None:
        """Give queued messages a moment to go out before the process exits."""
        deadline = time.time() + timeout
        while not self._queue.empty() and time.time() < deadline:
            time.sleep(0.1)

    def _run(self) -> None:
        while True:
            text = self._queue.get()
            self._deliver(text)
            self._queue.task_done()

    def _deliver(self, text: str) -> None:
        body = json.dumps({"chat_id": self._chat_id, "text": text,
                           "disable_web_page_preview": True}).encode()
        pause = 1.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            request = urllib.request.Request(self._url, data=body,
                                             headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=10) as resp:
                    resp.read()
                return
            except urllib.error.HTTPError as err:
                if err.code == 429:
                    pause = _retry_after(err) or pause
                elif 400 <= err.code < 500:
                    log.warning("telegram rejected a notification (HTTP %s); check the token and chat id",
                                err.code)
                    return
            except (urllib.error.URLError, OSError):
                pass
            if attempt < MAX_ATTEMPTS:
                time.sleep(pause)
                pause = min(pause * 2, 30.0)
        log.warning("telegram notification not delivered after %d attempts", MAX_ATTEMPTS)


def _retry_after(err: urllib.error.HTTPError) -> Optional[float]:
    try:
        return float(json.loads(err.read())["parameters"]["retry_after"])
    except Exception:
        return None


def from_env(prefix: str = ""):
    """TelegramNotifier when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set, else NullNotifier."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("telegram notifications disabled (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)")
        return NullNotifier()
    log.info("telegram notifications enabled")
    return TelegramNotifier(token, chat_id, prefix)
