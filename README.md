# joffre-lakes-pass-monitor

A small Python + Playwright monitor for checking BC Parks day-use pass availability for Pipi7iyekw / Joffre Lakes Park.

The app watches selected target dates and sends alerts when a pass appears to be available. It is designed for personal monitoring and learning purposes.

It does not complete bookings, bypass CAPTCHA, skip queues, evade rate limits, or automate checkout.

## What It Does

This monitor:

- Opens the BC Parks day-use pass page
- Selects Joffre Lakes Provincial Park
- Selects the pass type Joffre Lakes - Trail
- Checks one or more target dates
- Detects whether the selected date is Full or available
- Sends a Telegram / macOS / sound alert when availability is detected

The final booking is always completed manually by the user.

## Screenshots

Recommended screenshots to include:

```text
assets/joffre-booking-page.png
assets/pass-full.png
assets/pass-low.png
assets/telegram-alert.png
```

Example availability states:

```text
Pass availability - Full  -> not available
Pass availability - Low   -> available
```

Do not upload screenshots containing your Telegram token, chat ID, browser cookies, or personal information.

## Project Structure

```text
joffre-lakes-pass-monitor/
  main.py
  browser_check.py
  notifier.py
  config.yaml
  requirements.txt
  README.md
  .gitignore
  debug/
```

## Setup

Create and activate a virtual environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

If your system uses `python3` instead of `python`, use:

```bash
python3 -m playwright install chromium
```

## Telegram Notification Setup

Create a Telegram bot using @BotFather, then copy the example environment file:

```bash
cp .env.example .env
```

Fill in your own Telegram bot token and chat ID in `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

Never commit `.env` to GitHub.

Your `.gitignore` should include:

```gitignore
.env
.venv/
.venv-*/
debug/
.browser-home/
__pycache__/
*.pyc
.DS_Store
playwright-report/
test-results/
```

To test notification setup:

```bash
python main.py --test-notify
```

This should send a Telegram test message, trigger a macOS notification, and play a sound if enabled in `config.yaml`.

## Configuration

Edit `config.yaml` to change target dates, polling interval, notification settings, and debug behavior.

Example:

```yaml
target:
  park: "Pipi7iyekw / Joffre Lakes Park"
  pass_type: "Joffre Lakes - Trail"
  park_search_terms:
    - "Pipi7iyekw"
    - "Pipi7íyekw"
    - "Joffre Lakes"
    - "Joffre Lakes Park"
  dates:
    - "2026-07-07"

browser:
  headless: true
  slow_mo_ms: 0
  timeout_ms: 30000
  user_agent: ""

monitor:
  interval_seconds: 45
  jitter_seconds: 20
  save_debug_on_state_change: true
  debug_verbose: false
  alert_repeat: false
  alert_repeat_seconds: 15
  alert_repeat_window_seconds: 300
  debug_dir: "debug"

notifications:
  telegram:
    enabled: true
    bot_token_env: "TELEGRAM_BOT_TOKEN"
    chat_id_env: "TELEGRAM_CHAT_ID"
  macos:
    enabled: true
    sound: true
    sound_file: "/System/Library/Sounds/Glass.aiff"
```

The app also supports the older single-date format:

```yaml
target:
  date: "2026-07-07"
```

If `target.dates` is present, it is preferred over `target.date`.

## Run

Run one check with the browser visible:

```bash
python main.py --once --headful
```

Run continuously:

```bash
python main.py
```

Run continuously with the browser visible:

```bash
python main.py --headful
```

On macOS, use `caffeinate` to prevent the computer from sleeping:

```bash
caffeinate -dimsu python main.py --headful
```

To stop the monitor:

```bash
Control + C
```

## Availability Logic

For Joffre Lakes, the page currently shows one booking time:

```text
ALL DAY
```

The monitor classifies availability based on the selected date's pass availability text:

```text
Pass availability - Full  -> FULL
Pass availability - Low   -> AVAILABLE
```

More generally:

- Pass availability - Full means the date is full.
- Pass availability - Low, Medium, High, or another non-full availability value means the date may be available.
- If the app cannot confirm the park, pass type, date, or availability text, it returns UNKNOWN.

When availability is detected, the app sends alerts and keeps the booking manual.

## Debug Files

The app writes debug files for:

- AVAILABLE
- UNKNOWN
- ERROR
- every state if `debug_verbose` is enabled

Debug files may include:

```text
debug/YYYYMMDD-HHMMSS-YYYY-MM-DD-state.png
debug/YYYYMMDD-HHMMSS-YYYY-MM-DD-state.txt
```

Use these files to inspect page changes if BC Parks updates its website structure.

## Safety and Usage Notes

This project is intended for personal notification only.

Please use a conservative polling interval and follow BC Parks rules. Do not use this project to automate checkout, bypass queues, bypass CAPTCHA, overload the website, or reserve passes in ways that violate the site's terms.

Recommended polling interval:

```yaml
monitor:
  interval_seconds: 45
  jitter_seconds: 20
```

Avoid running multiple monitor instances at the same time.

## Troubleshooting

If the app reports UNKNOWN, run:

```bash
python main.py --once --headful
```

Then check the latest files in `debug/`.

If Telegram does not work, run:

```bash
python main.py --test-notify
```

Then verify:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

If the browser opens but does not reach the correct page, BC Parks may have changed the page layout. Inspect the latest screenshot in `debug/` and update the selectors in `browser_check.py`.

## Disclaimer

This is an unofficial personal project and is not affiliated with BC Parks or the Government of British Columbia.
