"""Success notifications: desktop, Telegram, ntfy. Every channel is best-effort."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import urllib.request
from collections.abc import Callable

from oci_a1_hunter.config import Config

WarnFn = Callable[[str], None]


def _post(url: str, data: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https endpoints
        resp.read()


def desktop(title: str, message: str) -> None:
    system = platform.system()
    if system == "Darwin":
        # Pass text as argv so quotes in the message cannot break the AppleScript.
        script = (
            "on run argv\n"
            'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"\n'
            "end run"
        )
        subprocess.run(["osascript", "-e", script, title, message], check=True, timeout=10)
    elif system == "Linux" and shutil.which("notify-send"):
        subprocess.run(["notify-send", title, message], check=True, timeout=10)


def telegram(token: str, chat_id: str, text: str) -> None:
    body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
    _post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        body.encode(),
        {"Content-Type": "application/json"},
    )


def ntfy(server: str, topic: str, title: str, message: str) -> None:
    _post(
        f"{server.rstrip('/')}/{topic}",
        message.encode(),
        {"Title": title, "Priority": "high", "Tags": "white_check_mark"},
    )


def notify_all(cfg: Config, title: str, message: str, warn: WarnFn) -> None:
    channels: list[tuple[str, Callable[[], None]]] = []
    if cfg.desktop_notify:
        channels.append(("desktop", lambda: desktop(title, message)))
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        channels.append(
            (
                "telegram",
                lambda: telegram(
                    cfg.telegram_bot_token, cfg.telegram_chat_id, f"{title}\n{message}"
                ),
            )
        )
    if cfg.ntfy_topic:
        channels.append(("ntfy", lambda: ntfy(cfg.ntfy_server, cfg.ntfy_topic, title, message)))

    for name, send in channels:
        try:
            send()
        except Exception as exc:  # noqa: BLE001
            warn(f"{name} notification failed: {exc}")
