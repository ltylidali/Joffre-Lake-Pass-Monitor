from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool
    bot_token_env: str
    chat_id_env: str


@dataclass(frozen=True)
class MacOSSettings:
    enabled: bool
    sound: bool
    sound_file: str


class Notifier:
    def __init__(self, telegram: TelegramSettings, macos: MacOSSettings) -> None:
        self.telegram = telegram
        self.macos = macos

    def notify_available(self, message: str) -> None:
        self.send_telegram(message)
        self.send_macos("BC Parks pass may be available", message)
        self.play_sound()

    def send_telegram(self, message: str) -> bool:
        if not self.telegram.enabled:
            return False

        token = os.getenv(self.telegram.bot_token_env, "").strip()
        chat_id = os.getenv(self.telegram.chat_id_env, "").strip()
        if not token or not chat_id:
            print(
                "Telegram is enabled, but token/chat ID env vars are missing. "
                f"Expected {self.telegram.bot_token_env} and {self.telegram.chat_id_env}."
            )
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            response = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "disable_web_page_preview": False,
                },
                timeout=15,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "request failed")
            print(f"Telegram notification failed: {status}")
            return False

    def send_macos(self, title: str, message: str) -> bool:
        if not self.macos.enabled:
            return False

        escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
        escaped_message = message.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{escaped_message}" with title "{escaped_title}"'
        try:
            completed = subprocess.run(["osascript", "-e", script], check=False, timeout=10)
            return completed.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"macOS notification failed: {exc}")
            return False

    def play_sound(self) -> bool:
        if not self.macos.enabled or not self.macos.sound:
            return False

        try:
            completed = subprocess.run(["afplay", self.macos.sound_file], check=False, timeout=15)
            return completed.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"macOS sound failed: {exc}")
            return False
