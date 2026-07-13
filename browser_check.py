from __future__ import annotations

import re
import os
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Browser, Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DAYUSE_URL = "https://reserve.bcparks.ca/dayuse"
PASS_TYPE = "Joffre Lakes - Trail"


class PageState(str, Enum):
    FULL = "FULL"
    UNAVAILABLE = "UNAVAILABLE"
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CheckResult:
    state: PageState
    url: str
    reason: str
    target_date: date
    debug_prefix: str | None = None
    park_selected: bool = False
    pass_type_selected: bool = False
    date_selected: bool = False
    classification_text: str = ""
    screenshot_path: str | None = None


@dataclass(frozen=True)
class BrowserSettings:
    headless: bool
    slow_mo_ms: int
    timeout_ms: int
    user_agent: str = ""


@dataclass(frozen=True)
class TargetSettings:
    park: str
    park_search_terms: list[str]
    pass_type: str
    target_dates: list[date]

    @property
    def target_date(self) -> date:
        return self.target_dates[0]


@dataclass(frozen=True)
class DebugSettings:
    debug_dir: Path
    save_on_state_change: bool
    debug_verbose: bool


@dataclass(frozen=True)
class FlowContext:
    park_selected: bool
    pass_type_selected: bool
    date_selected: bool
    park_evidence: str
    pass_type_evidence: str
    date_evidence: str


@dataclass(frozen=True)
class AvailabilityInspection:
    availability_text: str
    visible_buttons: list[str]
    enabled_buttons: list[str]
    inputs: list[str]
    related_elements: list[str]
    has_full_signal: bool
    has_enabled_booking_option: bool
    has_availability_section: bool


def run_single_check(
    target: TargetSettings,
    browser_settings: BrowserSettings,
    debug_settings: DebugSettings,
    *,
    debug_prefix: str | None = None,
    force_debug: bool = False,
) -> CheckResult:
    return run_cycle_checks(
        target,
        browser_settings,
        debug_settings,
        debug_prefix=debug_prefix,
        force_debug=force_debug,
    )[0]


def run_cycle_checks(
    target: TargetSettings,
    browser_settings: BrowserSettings,
    debug_settings: DebugSettings,
    *,
    debug_prefix: str | None = None,
    force_debug: bool = False,
    on_available: Callable[[list[CheckResult]], None] | None = None,
) -> list[CheckResult]:
    with sync_playwright() as playwright:
        browser_home = Path.cwd() / ".browser-home"
        browser_home.mkdir(parents=True, exist_ok=True)
        browser: Browser | None = None
        page: Page | None = None
        try:
            browser = playwright.chromium.launch(
                headless=browser_settings.headless,
                slow_mo=browser_settings.slow_mo_ms,
                args=["--disable-crash-reporter", "--disable-crashpad"],
                env={**os.environ, "HOME": str(browser_home)},
            )
            context_kwargs: dict[str, Any] = {
                "viewport": {"width": 1440, "height": 1000},
                "locale": "en-CA",
                "timezone_id": "America/Vancouver",
            }
            if browser_settings.user_agent:
                context_kwargs["user_agent"] = browser_settings.user_agent

            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.set_default_timeout(browser_settings.timeout_ms)
            results = _check_page(page, target, debug_settings, debug_prefix=debug_prefix, force_debug=force_debug)
            available_results = [result for result in results if result.state == PageState.AVAILABLE]
            if available_results and on_available:
                on_available(available_results)
            if available_results and not browser_settings.headless:
                print("Availability detected. Keeping browser open on the booking page. Press Ctrl+C to stop.")
                while True:
                    page.wait_for_timeout(60_000)
            return results
        except Exception as exc:
            prefix = debug_prefix or _debug_prefix(target.target_date, PageState.ERROR)
            try:
                if page:
                    _save_debug(page, debug_settings.debug_dir, prefix)
                    url = page.url
                else:
                    url = DAYUSE_URL
            except Exception:
                url = DAYUSE_URL
            screenshot_path = str(debug_settings.debug_dir / f"{prefix}.png")
            return _flow_error_results(
                target,
                url,
                f"{type(exc).__name__}: {exc}",
                _compact_text(f"{type(exc).__name__}: {exc}", max_length=1000),
                prefix,
                screenshot_path,
            )
        finally:
            if browser:
                browser.close()


def _check_page(
    page: Page,
    target: TargetSettings,
    debug_settings: DebugSettings,
    *,
    debug_prefix: str | None,
    force_debug: bool,
) -> list[CheckResult]:
    page.goto(DAYUSE_URL, wait_until="domcontentloaded")
    _settle(page)
    _handle_cookie_banner(page)

    entry = _click_joffre_book_a_pass(page, target)
    if not entry:
        prefix = debug_prefix or _debug_prefix(target.target_date, PageState.ERROR)
        screenshot_path = _save_debug(page, debug_settings.debug_dir, prefix)
        text = _compact_text(_safe_inner_text(page.locator("body")))
        return _flow_error_results(
            target,
            page.url,
            "Could not find/click the Joffre Lakes Provincial Park Book a Pass control.",
            text,
            prefix,
            str(screenshot_path),
        )

    print("Found Joffre Lakes card")
    print("Clicked Joffre Lakes Book a Pass")
    print(f"Current URL after click: {page.url}")
    after_click_text = _compact_text(_safe_inner_text(page.locator("body")), max_length=1200)
    print(f"Visible page text after click: {after_click_text}")
    pass_type_picker_appeared = _pass_type_picker_appeared(page)
    print(f"Pass type dropdown/form appeared: {pass_type_picker_appeared}")

    if not pass_type_picker_appeared:
        prefix = debug_prefix or _debug_prefix(target.target_date, PageState.ERROR)
        screenshot_path = _save_debug(page, debug_settings.debug_dir, prefix)
        return _flow_error_results(
            target,
            page.url,
            "Joffre Lakes Book a Pass was clicked, but no pass type dropdown/form appeared.",
            after_click_text,
            prefix,
            str(screenshot_path),
            park_selected=True,
        )

    pass_type_selected = _select_pass_type(page, target.pass_type)
    after_pass_type_text = _compact_text(_safe_inner_text(page.locator("body")), max_length=1200)
    if pass_type_selected:
        print(f"Selected pass type: {target.pass_type}")
    print(f"Current URL after pass type selection: {page.url}")
    print(f"Visible page text after pass type selection: {after_pass_type_text}")
    after_pass_type_prefix = debug_prefix or (_debug_prefix(target.target_date, PageState.UNKNOWN) + "-after-pass-type")
    after_pass_type_screenshot = None
    if debug_settings.debug_verbose:
        after_pass_type_screenshot = _save_debug(page, debug_settings.debug_dir, after_pass_type_prefix)
        print(f"Post-pass-type screenshot: {after_pass_type_screenshot}")

    if not pass_type_selected or _pass_type_prompt_still_visible(page):
        screenshot_path = str(after_pass_type_screenshot) if after_pass_type_screenshot else None
        if not screenshot_path:
            after_pass_type_screenshot = _save_debug(page, debug_settings.debug_dir, after_pass_type_prefix)
            screenshot_path = str(after_pass_type_screenshot)
        return _flow_unknown_results(
            target,
            page.url,
            "pass type not selected",
            after_pass_type_text,
            after_pass_type_prefix,
            screenshot_path,
            park_selected=True,
        )

    date_ui_appeared = _booking_date_picker_appeared(page)
    print(f"Booking date picker/form appeared: {date_ui_appeared}")
    if not date_ui_appeared:
        prefix = debug_prefix or _debug_prefix(target.target_date, PageState.ERROR)
        screenshot_path = _save_debug(page, debug_settings.debug_dir, prefix)
        return _flow_error_results(
            target,
            page.url,
            "Pass type was selected, but no booking date picker/form appeared.",
            after_pass_type_text,
            prefix,
            str(screenshot_path),
            park_selected=True,
            pass_type_selected=True,
        )

    results: list[CheckResult] = []
    for target_date in target.target_dates:
        dated_target = _target_for_date(target, target_date)
        _select_target_date(page, target_date)
        _settle(page)

        context = _confirm_flow_context(page, dated_target)
        result = _detect_availability(page, dated_target, context)
        should_save_debug = (
            force_debug
            or debug_settings.debug_verbose
            or result.state in {PageState.AVAILABLE, PageState.FULL, PageState.UNKNOWN, PageState.ERROR}
        )
        if should_save_debug:
            prefix = debug_prefix or _debug_prefix(target_date, result.state)
            screenshot_path = _save_debug(page, debug_settings.debug_dir, prefix)
            result = CheckResult(
                result.state,
                page.url,
                result.reason,
                target_date,
                prefix,
                result.park_selected,
                result.pass_type_selected,
                result.date_selected,
                result.classification_text,
                str(screenshot_path),
            )
        results.append(result)

    return results


def _handle_cookie_banner(page: Page) -> None:
    labels = [
        "Accept all",
        "Accept",
        "I agree",
        "Agree",
        "OK",
        "Got it",
        "Close",
    ]
    for label in labels:
        button = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I))
        if _click_first(button, timeout_ms=1500):
            _settle(page)
            return


def _target_for_date(target: TargetSettings, target_date: date) -> TargetSettings:
    return TargetSettings(
        park=target.park,
        park_search_terms=target.park_search_terms,
        pass_type=target.pass_type,
        target_dates=[target_date],
    )


def _flow_error_results(
    target: TargetSettings,
    url: str,
    reason: str,
    classification_text: str,
    debug_prefix: str,
    screenshot_path: str,
    *,
    park_selected: bool = False,
    pass_type_selected: bool = False,
) -> list[CheckResult]:
    return [
        CheckResult(
            PageState.ERROR,
            url,
            reason,
            target_date,
            debug_prefix,
            park_selected,
            pass_type_selected,
            False,
            classification_text,
            screenshot_path,
        )
        for target_date in target.target_dates
    ]


def _flow_unknown_results(
    target: TargetSettings,
    url: str,
    reason: str,
    classification_text: str,
    debug_prefix: str,
    screenshot_path: str,
    *,
    park_selected: bool = False,
    pass_type_selected: bool = False,
) -> list[CheckResult]:
    return [
        CheckResult(
            PageState.UNKNOWN,
            url,
            reason,
            target_date,
            debug_prefix,
            park_selected,
            pass_type_selected,
            False,
            classification_text,
            screenshot_path,
        )
        for target_date in target.target_dates
    ]


def _click_joffre_book_a_pass(page: Page, target: TargetSettings) -> bool:
    exact_name = re.compile(r"Joffre\s+Lakes\s+Provincial\s+Park", re.I)
    book_name = re.compile(r"\bBook\s+a\s+Pass\b", re.I)

    card_candidates = [
        page.locator("article").filter(has_text=exact_name),
        page.locator("section").filter(has_text=exact_name),
        page.locator("[class*='card' i]").filter(has_text=exact_name),
        page.locator("[class*='park' i]").filter(has_text=exact_name),
        page.locator("li").filter(has_text=exact_name),
    ]

    for card in card_candidates:
        if _click_book_a_pass_inside(card, book_name):
            _settle(page)
            return True

    xpath_card = page.locator(
        "xpath=//*[contains(normalize-space(.), 'Joffre Lakes Provincial Park') "
        "and not(.//*[contains(normalize-space(.), 'Joffre Lakes Provincial Park')])]"
        "/ancestor::*[.//*[self::button or self::a][contains(normalize-space(.), 'Book a Pass')]][1]"
    )
    if _click_book_a_pass_inside(xpath_card, book_name):
        _settle(page)
        return True

    text_node = page.get_by_text(exact_name).first
    try:
        if text_node.is_visible(timeout=1500):
            ancestor = text_node.locator(
                "xpath=ancestor::*[.//*[self::button or self::a][contains(normalize-space(.), 'Book a Pass')]][1]"
            )
            if _click_book_a_pass_inside(ancestor, book_name):
                _settle(page)
                return True
    except Exception:
        pass

    return False


def _click_book_a_pass_inside(container: Locator, book_name: re.Pattern[str]) -> bool:
    try:
        count = min(container.count(), 10)
        for index in range(count):
            card = container.nth(index)
            if not card.is_visible(timeout=1000):
                continue
            controls = [
                card.get_by_role("button", name=book_name),
                card.get_by_role("link", name=book_name),
                card.locator("button").filter(has_text=book_name),
                card.locator("a").filter(has_text=book_name),
            ]
            for control in controls:
                if _click_first(control, timeout_ms=1500):
                    return True
    except Exception:
        return False
    return False


def _booking_date_picker_appeared(page: Page) -> bool:
    signals = [
        page.locator("input[type='date']"),
        page.locator("input[placeholder*='date' i]"),
        page.locator("input[aria-label*='date' i]"),
        page.locator("button[aria-label*='date' i]"),
        page.locator("[role='grid']"),
        page.locator("[role='dialog']").filter(has_text=re.compile(r"date|pass|Joffre", re.I)),
        page.get_by_text(re.compile(r"select.*date|date.*visit|day-use|day use|Joffre Lakes", re.I)),
    ]
    return any(_has_visible(locator) for locator in signals)


def _pass_type_picker_appeared(page: Page) -> bool:
    signals = [
        page.locator("select").filter(has_text=re.compile(r"Joffre Lakes|Select a pass type", re.I)),
        page.get_by_text(re.compile(r"Select a pass type|--Select a pass type--", re.I)),
        page.get_by_role("combobox", name=re.compile(r"pass type|pass", re.I)),
        page.locator("[aria-label*='pass type' i]"),
    ]
    return any(_has_visible(locator) for locator in signals)


def _select_pass_type(page: Page, pass_type: str) -> bool:
    if _pass_type_selected_evidence(page, pass_type):
        return True

    if _select_pass_type_native(page, pass_type):
        _wait_after_pass_type_change(page)
        return bool(_pass_type_selected_evidence(page, pass_type))

    if _select_pass_type_from_placeholder(page, pass_type):
        _wait_after_pass_type_change(page)
        return bool(_pass_type_selected_evidence(page, pass_type))

    if _select_pass_type_from_combobox(page, pass_type):
        _wait_after_pass_type_change(page)
        return bool(_pass_type_selected_evidence(page, pass_type))

    return False


def _select_pass_type_native(page: Page, pass_type: str) -> bool:
    selects = page.locator("select")
    try:
        count = min(selects.count(), 10)
        for index in range(count):
            select = selects.nth(index)
            if not select.is_visible(timeout=1000):
                continue
            option_text = _safe_inner_text(select)
            if pass_type not in option_text:
                option_text = select.evaluate(
                    "element => Array.from(element.options || []).map(option => option.textContent || '').join('\\n')"
                )
            if pass_type not in option_text:
                continue
            select.select_option(label=pass_type, timeout=3000)
            return True
    except Exception:
        return False
    return False


def _select_pass_type_from_placeholder(page: Page, pass_type: str) -> bool:
    placeholders = [
        page.get_by_text(re.compile(r"^--\s*Select a pass type\s*--$", re.I)),
        page.get_by_text(re.compile(r"Select a pass type", re.I)),
        page.get_by_role("button", name=re.compile(r"Select a pass type", re.I)),
    ]
    for placeholder in placeholders:
        if _click_first(placeholder, timeout_ms=2000):
            _settle(page)
            if _click_pass_type_option(page, pass_type):
                return True
    return False


def _select_pass_type_from_combobox(page: Page, pass_type: str) -> bool:
    labels = [
        page.get_by_role("combobox", name=re.compile(r"pass type|pass", re.I)),
        page.locator("[aria-label*='pass type' i]"),
        page.locator("[role='combobox']").filter(has_text=re.compile(r"Select a pass type|Joffre", re.I)),
        page.locator("[aria-haspopup='listbox'], [aria-haspopup='menu']").filter(
            has_text=re.compile(r"Select a pass type|Joffre", re.I)
        ),
    ]
    for combo in labels:
        if _click_first(combo, timeout_ms=2000):
            _settle(page)
            if _click_pass_type_option(page, pass_type):
                return True
    return False


def _click_pass_type_option(page: Page, pass_type: str) -> bool:
    exact = re.compile(rf"^{re.escape(pass_type)}$", re.I)
    options = [
        page.get_by_role("option", name=exact),
        page.get_by_role("menuitem", name=exact),
        page.get_by_role("button", name=exact),
        page.get_by_text(exact),
        page.locator("li, div, span").filter(has_text=exact),
    ]
    return any(_click_first(option, timeout_ms=2000) for option in options)


def _wait_after_pass_type_change(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=7000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(1500)


def _pass_type_prompt_still_visible(page: Page) -> bool:
    text = _normalize(_safe_inner_text(page.locator("body")))
    return "please select a pass type to see available passes" in text


def _select_target_date(page: Page, target_date: date) -> None:
    if _try_fill_date_input(page, target_date):
        _settle(page)
        return

    if _try_click_date(page, target_date):
        _settle(page)
        return

    _open_calendar(page)
    _navigate_calendar(page, target_date)
    _try_click_date(page, target_date)


def _try_fill_date_input(page: Page, target_date: date) -> bool:
    date_values = [
        target_date.isoformat(),
        target_date.strftime("%Y/%m/%d"),
        target_date.strftime("%m/%d/%Y"),
        target_date.strftime("%B %-d, %Y") if hasattr(target_date, "strftime") else "",
    ]
    input_locators = [
        page.locator("input[type='date']"),
        page.locator("input[placeholder*='date' i]"),
        page.locator("input[aria-label*='date' i]"),
        page.locator("input[name*='date' i]"),
    ]
    for locator in input_locators:
        for value in date_values:
            if value and _fill_first(locator, value):
                return True
    return False


def _open_calendar(page: Page) -> None:
    labels = ["date", "calendar", "arrival", "visit"]
    for label in labels:
        if _click_first(page.get_by_role("button", name=re.compile(label, re.I)), timeout_ms=1500):
            _settle(page)
            return
    _click_first(page.locator("[aria-haspopup='dialog'], [aria-haspopup='grid']"), timeout_ms=1500)


def _navigate_calendar(page: Page, target_date: date) -> None:
    month_name = target_date.strftime("%B")
    target_year = str(target_date.year)
    for _ in range(18):
        visible_text = _safe_inner_text(page.locator("body"))
        if month_name.lower() in visible_text.lower() and target_year in visible_text:
            return
        next_buttons = [
            page.get_by_role("button", name=re.compile(r"next|following|forward", re.I)),
            page.locator("button[aria-label*='next' i]"),
            page.locator("button:has-text('>')"),
        ]
        clicked = False
        for button in next_buttons:
            if _click_first(button, timeout_ms=1000):
                clicked = True
                _settle(page)
                break
        if not clicked:
            return


def _try_click_date(page: Page, target_date: date) -> bool:
    labels = _date_labels(target_date)
    for label in labels:
        locators = [
            page.get_by_role("button", name=re.compile(re.escape(label), re.I)),
            page.get_by_role("gridcell", name=re.compile(re.escape(label), re.I)),
            page.locator(f"[aria-label*='{label}' i]"),
            page.locator(f"text=/{re.escape(label)}/i"),
        ]
        for locator in locators:
            if _click_first_available(locator, timeout_ms=1500):
                return True

    day_button = page.get_by_role("button", name=re.compile(rf"^{target_date.day}$"))
    return _click_first_available(day_button, timeout_ms=1500)


def _confirm_flow_context(page: Page, target: TargetSettings) -> FlowContext:
    park_evidence = _selected_park_evidence(page, target)
    pass_type_evidence = _pass_type_selected_evidence(page, target.pass_type)
    date_evidence = _selected_date_evidence(page, target.target_date)
    return FlowContext(
        park_selected=bool(park_evidence),
        pass_type_selected=bool(pass_type_evidence),
        date_selected=bool(date_evidence),
        park_evidence=park_evidence,
        pass_type_evidence=pass_type_evidence,
        date_evidence=date_evidence,
    )


def _detect_availability(page: Page, target: TargetSettings, context: FlowContext) -> CheckResult:
    body_text = _safe_inner_text(page.locator("body"))

    if _pass_type_prompt_still_visible(page) or not context.pass_type_selected:
        evidence = _compact_text(
            " | ".join(
                part
                for part in [
                    f"park evidence: {context.park_evidence}" if context.park_evidence else "",
                    f"pass type evidence: {context.pass_type_evidence}" if context.pass_type_evidence else "",
                    _relevant_body_excerpt(body_text, target),
                ]
                if part
            )
        )
        return _result(PageState.UNKNOWN, page, target, context, "pass type not selected", evidence)

    if not context.park_selected or not context.date_selected:
        missing = []
        if not context.park_selected:
            missing.append("park")
        if not context.date_selected:
            missing.append("date")
        evidence = _compact_text(
            " | ".join(
                part
                for part in [
                    f"park evidence: {context.park_evidence}" if context.park_evidence else "",
                    f"pass type evidence: {context.pass_type_evidence}" if context.pass_type_evidence else "",
                    f"date evidence: {context.date_evidence}" if context.date_evidence else "",
                    _relevant_body_excerpt(body_text, target),
                ]
                if part
            )
        )
        return _result(
            PageState.UNKNOWN,
            page,
            target,
            context,
            f"Cannot classify availability until selected {' and '.join(missing)} is confirmed.",
            evidence,
        )

    availability_reason = _selected_pass_availability_reason(page, target)
    classification_text = availability_reason or _target_date_context_text(page, target) or _relevant_body_excerpt(body_text, target)
    if not availability_reason:
        result = _result(
            PageState.UNKNOWN,
            page,
            target,
            context,
            "Pass availability text was not found for the selected date.",
            classification_text,
        )
        debug_availability_dom(target.target_date, result, _inspect_availability_dom(page, target))
        return result

    if re.search(r"\b(full|sold out|unavailable|no passes available)\b", availability_reason, re.I):
        return _result(
            PageState.FULL,
            page,
            target,
            context,
            availability_reason,
            availability_reason,
        )

    return _result(
        PageState.AVAILABLE,
        page,
        target,
        context,
        availability_reason,
        availability_reason,
    )


def _result(
    state: PageState,
    page: Page,
    target: TargetSettings,
    context: FlowContext,
    reason: str,
    classification_text: str,
) -> CheckResult:
    return CheckResult(
        state,
        page.url,
        reason,
        target.target_date,
        park_selected=context.park_selected,
        pass_type_selected=context.pass_type_selected,
        date_selected=context.date_selected,
        classification_text=classification_text,
    )


def _find_clickable_proceed_control(page: Page, target: TargetSettings) -> str | None:
    labels = [
        "Reserve",
        "Select",
        "Continue",
        "Available",
    ]
    for label in labels:
        locator = page.get_by_role("button", name=re.compile(rf"\b{re.escape(label)}\b", re.I))
        text = _first_contextual_clickable_text(locator, target)
        if text:
            return f"Clickable '{label}' button detected for selected park/date."

        link = page.get_by_role("link", name=re.compile(rf"\b{re.escape(label)}\b", re.I))
        text = _first_contextual_clickable_text(link, target)
        if text:
            return f"Clickable '{label}' link detected for selected park/date."

    return None


def _selected_pass_availability_reason(page: Page, target: TargetSettings) -> str:
    snippets = [
        _target_date_context_text(page, target),
        _relevant_body_excerpt(_safe_inner_text(page.locator("body")), target),
        _safe_inner_text(page.locator("body")),
    ]
    pattern = re.compile(
        r"Pass\s+availability\s*-\s*(Full|Low|Available|High|Medium|Sold\s*out|Unavailable|No\s+passes\s+available)",
        re.I,
    )
    for snippet in snippets:
        match = pattern.search(snippet or "")
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip()
            return f"Pass availability - {value}"

    generic = re.compile(r"Pass\s+availability\s*-\s*([A-Za-z][A-Za-z ]{0,40})", re.I)
    for snippet in snippets:
        match = generic.search(snippet or "")
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" .:-")
            if value:
                return f"Pass availability - {value}"
    return ""


def _inspect_availability_dom(page: Page, target: TargetSettings) -> AvailabilityInspection:
    keywords = ["Reserve", "Select", "Book", "Continue", "Available", "AM", "PM", "ALL DAY", "Full"]
    try:
        data = page.evaluate(
            """({ keywords }) => {
                const isVisible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const textOf = (el) => [
                    el.innerText || '',
                    el.textContent || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                    el.getAttribute('value') || ''
                ].join(' ').replace(/\\s+/g, ' ').trim();
                const disabled = (el) =>
                    Boolean(el.disabled) ||
                    el.getAttribute('aria-disabled') === 'true' ||
                    /disabled|unavailable|sold|full/i.test(el.getAttribute('class') || '');
                const labelFor = (el) => {
                    const id = el.getAttribute('id');
                    const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                    const implicit = el.closest('label');
                    const parent = el.closest('[role], li, article, section, div');
                    return textOf(explicit || implicit || parent || el);
                };
                const nodeSummary = (el) => {
                    const text = textOf(el);
                    const tag = el.tagName.toLowerCase();
                    const role = el.getAttribute('role') || '';
                    const aria = el.getAttribute('aria-label') || '';
                    const enabled = !disabled(el);
                    return `${tag}${role ? `[role=${role}]` : ''}: ${text || aria} :: ${enabled ? 'enabled' : 'disabled'}`;
                };
                const all = Array.from(document.querySelectorAll('body *')).filter(isVisible);
                const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'))
                    .filter(isVisible)
                    .map(nodeSummary)
                    .filter(Boolean)
                    .slice(0, 80);
                const enabledButtons = Array.from(document.querySelectorAll('button, a, [role="button"]'))
                    .filter((el) => isVisible(el) && !disabled(el))
                    .map(nodeSummary)
                    .filter(Boolean)
                    .slice(0, 80);
                const inputs = Array.from(document.querySelectorAll('input, [role="radio"], [role="checkbox"], [role="option"]'))
                    .filter(isVisible)
                    .map((el) => {
                        const checked = el.checked || el.getAttribute('aria-checked') === 'true' || el.getAttribute('aria-selected') === 'true';
                        return `${labelFor(el)} :: ${checked ? 'checked' : 'unchecked'} :: ${disabled(el) ? 'disabled' : 'enabled'}`;
                    })
                    .filter(Boolean)
                    .slice(0, 80);
                const related = all
                    .filter((el) => keywords.some((keyword) => new RegExp(`\\\\b${keyword.replace(/ /g, '\\\\s+')}\\\\b`, 'i').test(textOf(el))))
                    .map(nodeSummary)
                    .filter(Boolean)
                    .slice(0, 120);
                const sectionCandidates = all.filter((el) => /pass availability|availability|available passes|all day|\\bam\\b|\\bpm\\b|reserve|select|continue|full/i.test(textOf(el)));
                const snippets = [];
                for (const el of sectionCandidates) {
                    let node = el;
                    for (let i = 0; node && i < 4; i += 1) {
                        const text = textOf(node);
                        if (text.length >= 20 && text.length <= 1800) {
                            snippets.push(text);
                            break;
                        }
                        node = node.parentElement;
                    }
                    if (snippets.length >= 20) break;
                }
                const unique = (items) => Array.from(new Set(items.map((item) => item.replace(/\\s+/g, ' ').trim()).filter(Boolean)));
                return {
                    visibleButtons: unique(buttons),
                    enabledButtons: unique(enabledButtons),
                    inputs: unique(inputs),
                    relatedElements: unique(related),
                    snippets: unique(snippets)
                };
            }""",
            {"keywords": keywords},
        )
    except Exception:
        data = {"visibleButtons": [], "enabledButtons": [], "inputs": [], "relatedElements": [], "snippets": []}

    visible_buttons = list(data.get("visibleButtons", []))
    enabled_buttons = list(data.get("enabledButtons", []))
    inputs = list(data.get("inputs", []))
    related_elements = list(data.get("relatedElements", []))
    snippets = list(data.get("snippets", []))
    availability_text = _compact_text("\n".join(snippets or related_elements), max_length=1800)
    full_signal_text = _normalize("\n".join(snippets or related_elements))
    option_text = _normalize("\n".join(enabled_buttons + inputs + related_elements))

    full_pattern = re.compile(r"\b(pass availability\s*-\s*full|full|sold out|unavailable|no passes available)\b", re.I)
    action_pattern = re.compile(r"\b(reserve|select|book|continue|available|am|pm|all day)\b", re.I)
    enabled_input_pattern = re.compile(r"\b(enabled)\b", re.I)
    disabled_pattern = re.compile(r"\b(disabled)\b", re.I)

    has_enabled_input_option = any(
        action_pattern.search(item) and enabled_input_pattern.search(item) and not disabled_pattern.search(item)
        for item in inputs
    )
    has_enabled_button_option = any(action_pattern.search(item) for item in enabled_buttons)
    has_related_enabled_option = any(
        action_pattern.search(item) and "disabled" not in item.casefold()
        for item in related_elements
    )

    return AvailabilityInspection(
        availability_text=availability_text,
        visible_buttons=visible_buttons,
        enabled_buttons=enabled_buttons,
        inputs=inputs,
        related_elements=related_elements,
        has_full_signal=bool(full_pattern.search(full_signal_text)),
        has_enabled_booking_option=has_enabled_button_option or has_enabled_input_option or has_related_enabled_option,
        has_availability_section=bool(snippets or related_elements or action_pattern.search(option_text)),
    )


def debug_availability_dom(target_date: date, result: CheckResult, inspection: AvailabilityInspection) -> None:
    print(f"debug_availability_dom({target_date.isoformat()})")
    print(f"  selected date: {target_date.isoformat()}")
    print(f"  state: {result.state.value}")
    print(f"  reason: {result.reason}")
    print(f"  availability text: {inspection.availability_text or '(none)'}")
    print("  visible buttons:")
    for item in inspection.visible_buttons or ["(none)"]:
        print(f"    - {item}")
    print("  visible enabled buttons:")
    for item in inspection.enabled_buttons or ["(none)"]:
        print(f"    - {item}")
    print("  visible inputs/radios:")
    for item in inspection.inputs or ["(none)"]:
        print(f"    - {item}")
    print("  availability-related snippets:")
    for item in inspection.related_elements or ["(none)"]:
        print(f"    - {item}")


def _target_date_looks_selectable(page: Page, target_date: date) -> str | None:
    disabled_tokens = re.compile(r"disabled|unavailable|sold|full|not-available|blocked", re.I)
    for label in _date_labels(target_date):
        locator = page.locator(f"[aria-label*='{label}' i], button:has-text('{target_date.day}')")
        count = min(locator.count(), 10)
        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible(timeout=500):
                    continue
                disabled = item.get_attribute("disabled")
                aria_disabled = item.get_attribute("aria-disabled")
                classes = item.get_attribute("class") or ""
                label_text = item.get_attribute("aria-label") or _safe_inner_text(item)
                if disabled or aria_disabled == "true" or disabled_tokens.search(classes + " " + label_text):
                    continue
                return f"Target date element appears selectable: {label_text.strip() or label}."
            except PlaywrightTimeoutError:
                continue
    return None


def _selected_park_evidence(page: Page, target: TargetSettings) -> str:
    patterns = _park_patterns(target)
    for locator in [
        page.locator("input"),
        page.locator("h1, h2, h3"),
        page.locator("[aria-selected='true']"),
        page.locator("[aria-pressed='true']"),
        page.locator("[data-selected='true']"),
        page.locator(".selected, .active, .is-selected"),
    ]:
        evidence = _first_matching_evidence(locator, patterns, include_values=True)
        if evidence:
            return evidence
    return ""


def _pass_type_selected_evidence(page: Page, pass_type: str) -> str:
    exact = re.compile(re.escape(pass_type), re.I)
    selects = page.locator("select")
    try:
        count = min(selects.count(), 10)
        for index in range(count):
            select = selects.nth(index)
            if not select.is_visible(timeout=500):
                continue
            selected_text = select.evaluate(
                "element => element.selectedOptions && element.selectedOptions[0] "
                "? element.selectedOptions[0].textContent || '' : ''"
            )
            selected_value = select.input_value(timeout=500)
            evidence = _compact_text(f"{selected_text} {selected_value}")
            if evidence and exact.search(evidence):
                return evidence
    except Exception:
        pass

    for locator in [
        page.locator("input"),
        page.locator("[aria-selected='true']"),
        page.locator("[aria-pressed='true']"),
        page.locator("[data-selected='true']"),
        page.locator(".selected, .active, .is-selected"),
        page.get_by_role("button", name=exact),
        page.get_by_role("combobox", name=exact),
        page.locator("[role='combobox']").filter(has_text=exact),
    ]:
        evidence = _first_matching_evidence(locator, [exact], include_values=True)
        if evidence and not re.search(r"select a pass type", evidence, re.I):
            return evidence
    return ""


def _selected_date_evidence(page: Page, target_date: date) -> str:
    patterns = [re.compile(re.escape(label), re.I) for label in _date_labels(target_date)]
    patterns.extend(
        [
            re.compile(rf"\b{target_date.day}\b.*\b(selected|active|current)\b", re.I),
            re.compile(rf"\b(selected|active|current)\b.*\b{target_date.day}\b", re.I),
        ]
    )

    for locator in [
        page.locator("input[type='date'], input[placeholder*='date' i], input[aria-label*='date' i], input[name*='date' i]"),
        page.locator("[aria-selected='true']"),
        page.locator("[aria-pressed='true']"),
        page.locator("[data-selected='true']"),
        page.locator(".selected, .active, .is-selected"),
    ]:
        evidence = _first_matching_evidence(locator, patterns, include_values=True)
        if evidence:
            return evidence
    return ""


def _target_date_context_text(page: Page, target: TargetSettings) -> str:
    snippets: list[str] = []
    for label in _date_labels(target.target_date):
        locator = page.locator(f"[aria-label*='{label}' i], button:has-text('{target.target_date.day}')")
        snippets.extend(_ancestor_snippets(locator, limit=3))

    selected = page.locator("[aria-selected='true'], [aria-pressed='true'], [data-selected='true'], .selected, .active, .is-selected")
    snippets.extend(_ancestor_snippets(selected, limit=5))

    filtered = [
        snippet
        for snippet in snippets
        if _mentions_target_date(snippet, target.target_date) or _mentions_park(snippet, target)
    ]
    return "\n".join(dict.fromkeys(_compact_text(snippet) for snippet in filtered if snippet))


def _first_contextual_clickable_text(locator: Locator, target: TargetSettings) -> str:
    try:
        count = min(locator.count(), 8)
        for index in range(count):
            item = locator.nth(index)
            if not _is_clickable(item):
                continue
            snippets = _ancestor_snippets(item, limit=1)
            context = _compact_text(snippets[0] if snippets else _safe_inner_text(item))
            if _mentions_target_date(context, target.target_date) or _mentions_park(context, target):
                return context
    except Exception:
        return ""
    return ""


def _first_matching_evidence(locator: Locator, patterns: list[re.Pattern[str]], *, include_values: bool) -> str:
    try:
        count = min(locator.count(), 20)
        for index in range(count):
            item = locator.nth(index)
            if not item.is_visible(timeout=500):
                continue
            evidence_parts = [
                item.get_attribute("aria-label") or "",
                item.get_attribute("title") or "",
                item.get_attribute("class") or "",
                _safe_inner_text(item),
            ]
            if include_values:
                evidence_parts.append(item.input_value(timeout=500) if _looks_like_input(item) else "")
            evidence = _compact_text(" ".join(part for part in evidence_parts if part))
            if evidence and any(pattern.search(evidence) for pattern in patterns):
                return evidence
    except Exception:
        return ""
    return ""


def _ancestor_snippets(locator: Locator, *, limit: int) -> list[str]:
    snippets: list[str] = []
    try:
        count = min(locator.count(), limit)
        for index in range(count):
            item = locator.nth(index)
            if not item.is_visible(timeout=500):
                continue
            snippet = item.evaluate(
                """element => {
                    let node = element;
                    for (let i = 0; node && i < 4; i += 1) {
                        const text = (node.innerText || node.textContent || '').trim();
                        if (text.length >= 20) return text.slice(0, 1200);
                        node = node.parentElement;
                    }
                    return (element.innerText || element.textContent || element.getAttribute('aria-label') || '').trim();
                }"""
            )
            if snippet:
                snippets.append(str(snippet))
    except Exception:
        return snippets
    return snippets


def _relevant_body_excerpt(body_text: str, target: TargetSettings) -> str:
    normalized_date_labels = [label.casefold() for label in _date_labels(target.target_date)]
    park_terms = [term.casefold() for term in target.park_search_terms]
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    matches = [
        line
        for line in lines
        if any(label in line.casefold() for label in normalized_date_labels)
        or any(term in line.casefold() for term in park_terms)
        or re.search(r"\b(full|sold out|unavailable|available|reserve|select|no passes)\b", line, re.I)
    ]
    return "\n".join(matches[:20])


def _mentions_target_date(text: str, target_date: date) -> bool:
    normalized = _normalize(text)
    return any(_normalize(label) in normalized for label in _date_labels(target_date)) or re.search(
        rf"\b{target_date.day}\b", normalized
    ) is not None


def _mentions_park(text: str, target: TargetSettings) -> bool:
    normalized = _normalize(text)
    return any(_normalize(term) in normalized for term in target.park_search_terms)


def _park_patterns(target: TargetSettings) -> list[re.Pattern[str]]:
    patterns = [re.compile(re.escape(term), re.I) for term in target.park_search_terms]
    patterns.extend([re.compile(r"Joffre\s+Lakes", re.I), re.compile(r"Pipi7", re.I)])
    return patterns


def _click_target_text(page: Page, target: TargetSettings) -> bool:
    patterns = [re.escape(term) for term in target.park_search_terms]
    patterns.append(r"Joffre\s+Lakes")
    patterns.append(r"Pipi7")
    pattern = re.compile("|".join(patterns), re.I)
    locators = [
        page.get_by_text(pattern),
        page.get_by_role("option", name=pattern),
        page.get_by_role("button", name=pattern),
        page.get_by_role("link", name=pattern),
    ]
    return any(_click_first(locator, timeout_ms=1500) for locator in locators)


def _page_mentions_target(page: Page, target: TargetSettings) -> bool:
    text = _normalize(_safe_inner_text(page.locator("body")))
    return any(_normalize(term) in text for term in target.park_search_terms)


def _date_labels(target_date: date) -> list[str]:
    return [
        target_date.strftime("%Y-%m-%d"),
        target_date.strftime("%B %-d, %Y"),
        target_date.strftime("%A, %B %-d, %Y"),
        target_date.strftime("%b %-d, %Y"),
        target_date.strftime("%m/%d/%Y"),
        target_date.strftime("%-m/%-d/%Y"),
    ]


def _fill_first(locator: Locator, value: str, *, timeout_ms: int = 2000) -> bool:
    try:
        count = min(locator.count(), 5)
        for index in range(count):
            item = locator.nth(index)
            if item.is_visible(timeout=timeout_ms):
                item.click(timeout=timeout_ms)
                item.fill(value, timeout=timeout_ms)
                return True
    except PlaywrightTimeoutError:
        return False
    except Exception:
        return False
    return False


def _click_first(locator: Locator, *, timeout_ms: int = 2000) -> bool:
    try:
        count = min(locator.count(), 5)
        for index in range(count):
            item = locator.nth(index)
            if item.is_visible(timeout=timeout_ms) and item.is_enabled(timeout=timeout_ms):
                item.click(timeout=timeout_ms)
                return True
    except PlaywrightTimeoutError:
        return False
    except Exception:
        return False
    return False


def _click_first_available(locator: Locator, *, timeout_ms: int = 2000) -> bool:
    try:
        count = min(locator.count(), 8)
        for index in range(count):
            item = locator.nth(index)
            if not item.is_visible(timeout=timeout_ms) or not item.is_enabled(timeout=timeout_ms):
                continue
            classes = item.get_attribute("class") or ""
            aria_disabled = item.get_attribute("aria-disabled")
            text = (item.get_attribute("aria-label") or "") + " " + _safe_inner_text(item)
            if aria_disabled == "true" or re.search(r"disabled|unavailable|sold|full", classes + " " + text, re.I):
                continue
            item.click(timeout=timeout_ms)
            return True
    except PlaywrightTimeoutError:
        return False
    except Exception:
        return False
    return False


def _has_clickable(locator: Locator) -> bool:
    try:
        count = min(locator.count(), 8)
        for index in range(count):
            item = locator.nth(index)
            if item.is_visible(timeout=500) and item.is_enabled(timeout=500):
                classes = item.get_attribute("class") or ""
                aria_disabled = item.get_attribute("aria-disabled")
                if aria_disabled != "true" and "disabled" not in classes.lower():
                    return True
    except Exception:
        return False
    return False


def _has_visible(locator: Locator) -> bool:
    try:
        count = min(locator.count(), 8)
        for index in range(count):
            if locator.nth(index).is_visible(timeout=800):
                return True
    except Exception:
        return False
    return False


def _is_clickable(locator: Locator) -> bool:
    try:
        if not locator.is_visible(timeout=500) or not locator.is_enabled(timeout=500):
            return False
        classes = locator.get_attribute("class") or ""
        aria_disabled = locator.get_attribute("aria-disabled")
        disabled = locator.get_attribute("disabled")
        return not disabled and aria_disabled != "true" and "disabled" not in classes.lower()
    except Exception:
        return False


def _looks_like_input(locator: Locator) -> bool:
    try:
        tag_name = locator.evaluate("element => element.tagName.toLowerCase()")
        return tag_name in {"input", "textarea", "select"}
    except Exception:
        return False


def _safe_inner_text(locator: Locator) -> str:
    try:
        return locator.inner_text(timeout=1500)
    except Exception:
        return ""


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _compact_text(value: str, *, max_length: int = 800) -> str:
    compacted = re.sub(r"\s+", " ", value).strip()
    if len(compacted) <= max_length:
        return compacted
    return compacted[: max_length - 3].rstrip() + "..."


def _settle(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(700)


def _debug_prefix(target_date: date, state: PageState) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{target_date.isoformat()}-{state.value.lower()}"


def _save_debug(page: Page, debug_dir: Path, prefix: str) -> Path:
    debug_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = debug_dir / f"{prefix}.png"
    page.screenshot(path=screenshot_path, full_page=True)
    (debug_dir / f"{prefix}.txt").write_text(_safe_inner_text(page.locator("body")), encoding="utf-8")
    (debug_dir / f"{prefix}.html").write_text(page.content(), encoding="utf-8")
    (debug_dir / f"{prefix}.url.txt").write_text(page.url, encoding="utf-8")
    return screenshot_path
