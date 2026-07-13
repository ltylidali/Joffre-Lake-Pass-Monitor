from __future__ import annotations

import argparse
import random
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from browser_check import (
    BrowserSettings,
    CheckResult,
    DebugSettings,
    PageState,
    TargetSettings,
    run_cycle_checks,
)
from notifier import MacOSSettings, Notifier, TelegramSettings


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor BC Parks Joffre Lakes day-use pass availability.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--headful", action="store_true", help="Show the browser for visual debugging")
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    parser.add_argument("--test-notify", action="store_true", help="Send test notifications without opening Playwright")
    parser.add_argument("--test-available", action="store_true", help="Send a test availability alert without opening Playwright")
    args = parser.parse_args()

    load_dotenv()
    config = _load_config(Path(args.config))
    target = _target_settings(config)
    browser_settings = _browser_settings(config, force_headful=args.headful)
    debug_settings = _debug_settings(config)
    notifier = _notifier(config)

    if args.test_notify:
        _run_notification_test(notifier)
        return
    if args.test_available:
        _send_available_notification(notifier, target, target.target_dates[0], "https://reserve.bcparks.ca/dayuse/")
        return

    interval = int(config.get("monitor", {}).get("interval_seconds", 25))
    jitter = int(config.get("monitor", {}).get("jitter_seconds", 5))
    alert_repeat = bool(config.get("monitor", {}).get("alert_repeat", False))
    alert_repeat_seconds = int(config.get("monitor", {}).get("alert_repeat_seconds", 15))
    alert_repeat_window_seconds = int(config.get("monitor", {}).get("alert_repeat_window_seconds", 300))
    notified_available_dates: set[date] = set()
    first_available_seen_at: dict[date, float] = {}
    last_alert_sent_at: dict[date, float] = {}
    cycle = 0

    print(
        f"Monitoring {target.park} for {', '.join(d.isoformat() for d in target.target_dates)} "
        f"every {interval}-{interval + jitter}s. Booking is not automated."
    )

    while True:
        cycle += 1
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        now = time.time()
        results = run_cycle_checks(
            target,
            browser_settings,
            debug_settings,
            on_available=lambda available_results: [
                _handle_available_result(
                    result,
                    target,
                    notifier,
                    now,
                    notified_available_dates,
                    first_available_seen_at,
                    last_alert_sent_at,
                    alert_repeat=alert_repeat,
                    alert_repeat_seconds=alert_repeat_seconds,
                    alert_repeat_window_seconds=alert_repeat_window_seconds,
                )
                for result in available_results
            ],
        )
        if len(results) == 1:
            result = results[0]
            print(
                f"[cycle {cycle} | {started}] {result.target_date.isoformat()}: "
                f"{result.state.value} | reason=\"{_log_reason(result)}\""
            )
        else:
            overall_state = _overall_state(results)
            summary = ", ".join(_result_summary(result) for result in results)
            print(f"[cycle {cycle} | {started}] {overall_state.value} - {summary}")

        for result in results:
            print(
                f"  {result.target_date.isoformat()}: park_selected={result.park_selected} "
                f"pass_type_selected={result.pass_type_selected} date_selected={result.date_selected}"
            )
            print(f"  {result.target_date.isoformat()}: classification_text={result.classification_text or '(none)'}")
            if result.screenshot_path:
                print(f"  {result.target_date.isoformat()}: screenshot={result.screenshot_path}")

        now = time.time()
        for result in results:
            if result.state == PageState.AVAILABLE:
                _handle_available_result(
                    result,
                    target,
                    notifier,
                    now,
                    notified_available_dates,
                    first_available_seen_at,
                    last_alert_sent_at,
                    alert_repeat=alert_repeat,
                    alert_repeat_seconds=alert_repeat_seconds,
                    alert_repeat_window_seconds=alert_repeat_window_seconds,
                )
            else:
                notified_available_dates.discard(result.target_date)
                first_available_seen_at.pop(result.target_date, None)
                last_alert_sent_at.pop(result.target_date, None)

        if args.once:
            return

        sleep_for = interval + random.uniform(0, max(0, jitter))
        time.sleep(sleep_for)


def _load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _target_settings(config: dict[str, Any]) -> TargetSettings:
    target = config.get("target", {})
    if target.get("dates"):
        target_dates = [date.fromisoformat(str(value)) for value in target["dates"]]
    else:
        target_dates = [date.fromisoformat(str(target.get("date", "2026-07-07")))]
    return TargetSettings(
        park=str(target.get("park", "Pipi7iyekw / Joffre Lakes Park")),
        park_search_terms=list(target.get("park_search_terms", ["Joffre Lakes"])),
        pass_type=str(target.get("pass_type", "Joffre Lakes - Trail")),
        target_dates=target_dates,
    )


def _browser_settings(config: dict[str, Any], *, force_headful: bool) -> BrowserSettings:
    browser = config.get("browser", {})
    headless = bool(browser.get("headless", True))
    if force_headful:
        headless = False
    return BrowserSettings(
        headless=headless,
        slow_mo_ms=int(browser.get("slow_mo_ms", 0)),
        timeout_ms=int(browser.get("timeout_ms", 30000)),
        user_agent=str(browser.get("user_agent", "")),
    )


def _debug_settings(config: dict[str, Any]) -> DebugSettings:
    monitor = config.get("monitor", {})
    return DebugSettings(
        debug_dir=Path(str(monitor.get("debug_dir", "debug"))),
        save_on_state_change=bool(monitor.get("save_debug_on_state_change", True)),
        debug_verbose=bool(monitor.get("debug_verbose", False)),
    )


def _notifier(config: dict[str, Any]) -> Notifier:
    notifications = config.get("notifications", {})
    telegram = notifications.get("telegram", {})
    macos = notifications.get("macos", {})
    return Notifier(
        TelegramSettings(
            enabled=bool(telegram.get("enabled", True)),
            bot_token_env=str(telegram.get("bot_token_env", "TELEGRAM_BOT_TOKEN")),
            chat_id_env=str(telegram.get("chat_id_env", "TELEGRAM_CHAT_ID")),
        ),
        MacOSSettings(
            enabled=bool(macos.get("enabled", True)),
            sound=bool(macos.get("sound", True)),
            sound_file=str(macos.get("sound_file", "/System/Library/Sounds/Glass.aiff")),
        ),
    )


def _run_notification_test(notifier: Notifier) -> None:
    message = (
        "BC Parks Joffre Lakes monitor test notification.\n"
        "If you see this, Telegram alerts are configured."
    )
    telegram_ok = notifier.send_telegram(message)
    macos_ok = notifier.send_macos("BC Parks monitor test", message)
    sound_ok = notifier.play_sound()

    print(f"Telegram: {'success' if telegram_ok else 'failure or disabled'}")
    print(f"macOS notification: {'success' if macos_ok else 'failure or disabled'}")
    print(f"macOS sound: {'success' if sound_ok else 'failure or disabled'}")


def _send_available_notification(notifier: Notifier, target: TargetSettings, target_date: date, url: str) -> None:
    message = (
        "Joffre Lakes pass may be available!\n"
        f"Date: {target_date.isoformat()}\n"
        f"Pass type: {target.pass_type}\n"
        f"Open manually: {url}"
    )
    print(message)
    notifier.notify_available(message)


def _handle_available_result(
    result: CheckResult,
    target: TargetSettings,
    notifier: Notifier,
    now: float,
    notified_available_dates: set[date],
    first_available_seen_at: dict[date, float],
    last_alert_sent_at: dict[date, float],
    *,
    alert_repeat: bool,
    alert_repeat_seconds: int,
    alert_repeat_window_seconds: int,
) -> None:
    first_available_seen_at.setdefault(result.target_date, now)
    if not _should_notify(
        result.target_date,
        now,
        notified_available_dates,
        first_available_seen_at,
        last_alert_sent_at,
        alert_repeat=alert_repeat,
        alert_repeat_seconds=alert_repeat_seconds,
        alert_repeat_window_seconds=alert_repeat_window_seconds,
    ):
        return
    print(f"ALERT TRIGGERED for {result.target_date.isoformat()}")
    _send_available_notification(
        notifier,
        target,
        result.target_date,
        result.url or "https://reserve.bcparks.ca/dayuse/",
    )
    notified_available_dates.add(result.target_date)
    last_alert_sent_at[result.target_date] = now


def _overall_state(results: list[CheckResult]) -> PageState:
    if any(result.state == PageState.AVAILABLE for result in results):
        return PageState.AVAILABLE
    if any(result.state == PageState.ERROR for result in results):
        return PageState.ERROR
    if any(result.state == PageState.UNKNOWN for result in results):
        return PageState.UNKNOWN
    if all(result.state in {PageState.FULL, PageState.UNAVAILABLE} for result in results):
        return PageState.FULL
    return PageState.UNKNOWN


def _result_summary(result: CheckResult) -> str:
    label = result.state.value.lower()
    if result.state == PageState.UNAVAILABLE:
        label = "full"
    return f"{result.target_date.isoformat()} {label}"


def _log_reason(result: CheckResult) -> str:
    reason = result.classification_text or result.reason
    return reason.replace('"', "'")[:500]


def _should_notify(
    target_date: date,
    now: float,
    notified_available_dates: set[date],
    first_available_seen_at: dict[date, float],
    last_alert_sent_at: dict[date, float],
    *,
    alert_repeat: bool,
    alert_repeat_seconds: int,
    alert_repeat_window_seconds: int,
) -> bool:
    if target_date not in notified_available_dates:
        return True
    if not alert_repeat:
        return False
    first_seen = first_available_seen_at.get(target_date, now)
    last_sent = last_alert_sent_at.get(target_date, 0)
    if now - first_seen > alert_repeat_window_seconds:
        return False
    return now - last_sent >= alert_repeat_seconds


if __name__ == "__main__":
    main()
