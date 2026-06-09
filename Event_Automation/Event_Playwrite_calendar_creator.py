#!/usr/bin/env python3
"""Create Church calendar events from an ICS file with Playwright."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page, Playwright


CALENDAR_URL = (
    "https://www.churchofjesuschrist.org/calendar/month"
)
DEFAULT_ICS_FILE = Path(__file__).with_name("church_calendar.ics")
DEFAULT_SCREENSHOT_DIR = Path(__file__).with_name("playwright_failures")
DEFAULT_IMPORTED_EVENTS_FILE = Path(__file__).with_name("church_calendar_imported_events.json")
DEFAULT_CALENDAR_ID = "392790"
DEFAULT_TIMEOUT_MS = 90_000
RETRY_PAUSE_SECONDS = 4
DEFAULT_VENUE = {
    "name": "Church Building",
    "address": "22015 48th Ave W",
    "city": "Mountlake Terrace",
    "state": "WA",
    "postal_code": "98043",
    "country": "US",
}


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    summary: str
    description: str
    location: str
    start: datetime
    end: datetime

    @property
    def start_date(self) -> str:
        return self.start.strftime("%Y-%m-%d")

    @property
    def start_time(self) -> str:
        return self.start.strftime("%H:%M")

    @property
    def end_time(self) -> str:
        return self.end.strftime("%H:%M")

    @property
    def slug(self) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", self.summary).strip("-").lower()
        return slug[:60] or "event"

    @property
    def import_key(self) -> str:
        if self.uid:
            return self.uid
        fingerprint = "|".join(
            [
                self.start.isoformat(),
                self.end.isoformat(),
                self.summary.casefold(),
                self.location.casefold(),
            ]
        )
        return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()


def unfold_ics_lines(text: str) -> list[str]:
    unfolded: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            continue
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def unescape_ics_text(value: str) -> str:
    return (
        value.replace(r"\n", "\n")
        .replace(r"\N", "\n")
        .replace(r"\,", ",")
        .replace(r"\;", ";")
        .replace(r"\\", "\\")
    )


def parse_ics_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ")
    return datetime.strptime(value, "%Y%m%dT%H%M%S")


def parse_ics_events(ics_file: Path) -> list[CalendarEvent]:
    lines = unfold_ics_lines(ics_file.read_text(encoding="utf-8"))
    events: list[CalendarEvent] = []
    current: dict[str, str] | None = None

    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current:
                events.append(
                    CalendarEvent(
                        uid=current.get("UID", ""),
                        summary=unescape_ics_text(current.get("SUMMARY", "")),
                        description=unescape_ics_text(current.get("DESCRIPTION", "")),
                        location=unescape_ics_text(current.get("LOCATION", "")),
                        start=parse_ics_datetime(current["DTSTART"]),
                        end=parse_ics_datetime(current["DTEND"]),
                    )
                )
            current = None
            continue
        if current is None or ":" not in line:
            continue

        name, value = line.split(":", 1)
        key = name.split(";", 1)[0]
        current[key] = value

    return sorted(events, key=lambda event: (event.start, event.summary))


def load_imported_events(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"Could not parse imported-events state file {path}: {error}")

    if not isinstance(data, dict):
        raise SystemExit(f"Imported-events state file must contain a JSON object: {path}")
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, dict)
    }


def save_imported_events(path: Path, imported_events: dict[str, dict[str, str]]) -> None:
    path.write_text(
        json.dumps(imported_events, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def imported_event_record(event: CalendarEvent) -> dict[str, str]:
    return {
        "summary": event.summary,
        "location": event.location,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


def filter_new_events(
    events: list[CalendarEvent],
    imported_events: dict[str, dict[str, str]],
) -> tuple[list[CalendarEvent], list[CalendarEvent]]:
    skipped = [event for event in events if event.import_key in imported_events]
    pending = [event for event in events if event.import_key not in imported_events]
    return pending, skipped


def login(page: "Page") -> None:
    username = os.getenv("CHURCH_USERNAME")
    password = os.getenv("CHURCH_PASSWORD")

    page.goto(CALENDAR_URL)

    if username:
        page.get_by_role("textbox", name="Username").fill(username)
        page.get_by_role("button", name="Next").click()
    if password:
        page.get_by_role("textbox", name="Password").fill(password)
        page.get_by_role("button", name="Verify").click()

    if not username or not password:
        print("Sign in manually in the browser window, then press Enter here.")
        input()

    wait_for_calendar_ready(page)


def wait_for_calendar_ready(page: "Page") -> None:
    page.wait_for_load_state("domcontentloaded")
    page.get_by_test_id("AddEventModal-add-event-button").wait_for(
        state="visible",
        timeout=DEFAULT_TIMEOUT_MS,
    )


def click_when_ready(page: "Page", locator, label: str) -> None:
    print(f"  - {label}")
    locator.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    locator.click(timeout=DEFAULT_TIMEOUT_MS)


def fill_when_ready(page: "Page", locator, value: str, label: str) -> None:
    print(f"  - {label}")
    locator.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    locator.fill(value, timeout=DEFAULT_TIMEOUT_MS)


def check_when_ready(page: "Page", locator, label: str) -> None:
    print(f"  - {label}")
    locator.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    if not locator.is_checked(timeout=DEFAULT_TIMEOUT_MS):
        locator.check(timeout=DEFAULT_TIMEOUT_MS)


def save_failure_state(page: "Page", event: CalendarEvent, attempt: int, error: Exception) -> None:
    DEFAULT_SCREENSHOT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base_name = f"{timestamp}-attempt-{attempt}-{event.slug}"
    screenshot_path = DEFAULT_SCREENSHOT_DIR / f"{base_name}.png"
    html_path = DEFAULT_SCREENSHOT_DIR / f"{base_name}.html"

    try:
        page.screenshot(path=str(screenshot_path), full_page=True, timeout=15_000)
    except Exception as screenshot_error:
        print(f"  ! Could not save screenshot: {screenshot_error}")

    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception as html_error:
        print(f"  ! Could not save page HTML: {html_error}")

    print(f"  ! Attempt {attempt} failed: {type(error).__name__}: {error}")
    print(f"  ! Failure details saved under {DEFAULT_SCREENSHOT_DIR}")


def dismiss_open_modal(page: "Page") -> None:
    close_button = page.get_by_role("button", name="Close", exact=True)
    cancel_button = page.get_by_role("button", name=re.compile(r"Cancel|Discard|Close"))

    for locator in (close_button, cancel_button):
        try:
            if locator.first.is_visible(timeout=2_000):
                locator.first.click(timeout=5_000)
                page.wait_for_timeout(1_000)
                return
        except Exception:
            continue

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(1_000)
    except Exception:
        pass


def fill_description(page: "Page", description: str) -> None:
    editor = page.locator(".eden-editor-input")
    fill_when_ready(page, editor, description, "description")


def fill_address(page: "Page", event: CalendarEvent) -> None:
    venue_name = event.location or DEFAULT_VENUE["name"]

    click_when_ready(page, page.get_by_role("button", name="Address"), "location type")
    fill_when_ready(page, page.get_by_role("textbox", name="* Venue Name"), venue_name, "venue")
    fill_when_ready(
        page,
        page.get_by_role("textbox", name="* Address Line"),
        DEFAULT_VENUE["address"],
        "address",
    )
    fill_when_ready(page, page.get_by_role("textbox", name="* City"), DEFAULT_VENUE["city"], "city")
    fill_when_ready(
        page,
        page.get_by_role("textbox", name="* State/Province"),
        DEFAULT_VENUE["state"],
        "state",
    )
    fill_when_ready(
        page,
        page.get_by_role("textbox", name="* Postal Code"),
        DEFAULT_VENUE["postal_code"],
        "postal code",
    )
    page.get_by_test_id("AddEventModal-country-select").select_option(
        DEFAULT_VENUE["country"],
        timeout=DEFAULT_TIMEOUT_MS,
    )


def create_event(page: "Page", event: CalendarEvent, calendar_id: str) -> None:
    wait_for_calendar_ready(page)
    click_when_ready(
        page,
        page.get_by_test_id("AddEventModal-add-event-button"),
        "open add event modal",
    )
    page.get_by_test_id("AddEventModal-event-calendar-select").select_option(
        calendar_id,
        timeout=DEFAULT_TIMEOUT_MS,
    )
    check_when_ready(
        page,
        page.get_by_test_id("AddEventModal-isPublicEvent-checkbox"),
        "public event",
    )
    fill_when_ready(
        page,
        page.get_by_test_id("AddEventModal-event-name-input"),
        event.summary,
        "event name",
    )
    fill_description(page, event.description or event.summary)
    fill_when_ready(
        page,
        page.get_by_test_id("AddEventModal-startDate-input"),
        event.start_date,
        "start date",
    )
    fill_when_ready(
        page,
        page.get_by_test_id("AddEventModal-startTime-input"),
        event.start_time,
        "start time",
    )
    fill_when_ready(
        page,
        page.get_by_test_id("AddEventModal-endTime-input"),
        event.end_time,
        "end time",
    )
    fill_address(page, event)
    click_when_ready(page, page.get_by_text("Everyone"), "audience")
    check_when_ready(
        page,
        page.get_by_test_id("AddEventModal-termsOfUse-checkbox"),
        "terms of use",
    )
    click_when_ready(
        page,
        page.get_by_test_id("AddEventModal-preview-button"),
        "preview",
    )
    publish_button = page.get_by_role("button", name=re.compile(r"^Publish$"))
    print("  - waiting for publish button")
    publish_button.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    print("  - publish")
    publish_button.click(timeout=DEFAULT_TIMEOUT_MS)
    try:
        page.get_by_role("button", name="Close", description="Close", exact=True).click(
            timeout=30_000,
        )
        wait_for_calendar_ready(page)
    except Exception as error:
        print(f"  ! Published, but confirmation close was slow: {error}")
        dismiss_open_modal(page)
        page.goto(CALENDAR_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        wait_for_calendar_ready(page)


def create_event_with_retries(
    page: "Page",
    event: CalendarEvent,
    calendar_id: str,
    max_attempts: int,
) -> bool:
    for attempt in range(1, max_attempts + 1):
        try:
            print(
                f"Creating ({attempt}/{max_attempts}): "
                f"{event.start_date} {event.start_time} - {event.summary}"
            )
            create_event(page, event, calendar_id)
            print("  - published")
            return True
        except Exception as error:
            save_failure_state(page, event, attempt, error)
            dismiss_open_modal(page)
            if attempt < max_attempts:
                print(f"  - retrying after {RETRY_PAUSE_SECONDS} seconds")
                time.sleep(RETRY_PAUSE_SECONDS)
                page.goto(CALENDAR_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
                wait_for_calendar_ready(page)

    return False


def run(
    playwright: "Playwright",
    events: list[CalendarEvent],
    calendar_id: str,
    max_attempts: int,
    imported_events_path: Path,
    imported_events: dict[str, dict[str, str]],
) -> None:
    browser = playwright.chromium.launch(channel="chrome", headless=False)
    context = browser.new_context()
    context.set_default_timeout(DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
    page = context.new_page()

    try:
        login(page)
        failures: list[CalendarEvent] = []
        for event in events:
            if create_event_with_retries(page, event, calendar_id, max_attempts):
                imported_events[event.import_key] = imported_event_record(event)
                save_imported_events(imported_events_path, imported_events)
            else:
                failures.append(event)

        if failures:
            print("\nThe following event(s) could not be published:")
            for event in failures:
                print(f"- {event.start_date} {event.start_time}: {event.summary}")
            raise SystemExit(1)
    finally:
        context.close()
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import events from an ICS file into the Church calendar."
    )
    parser.add_argument(
        "--ics",
        type=Path,
        default=DEFAULT_ICS_FILE,
        help=f"ICS file to import. Default: {DEFAULT_ICS_FILE}",
    )
    parser.add_argument(
        "--calendar-id",
        default=DEFAULT_CALENDAR_ID,
        help=f"Church calendar select option value. Default: {DEFAULT_CALENDAR_ID}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parsed ICS events without opening Playwright.",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=3,
        help="Number of attempts per event before moving on. Default: 3",
    )
    parser.add_argument(
        "--imported-events-file",
        type=Path,
        default=DEFAULT_IMPORTED_EVENTS_FILE,
        help=(
            "JSON state file used to skip events already published from ICS. "
            f"Default: {DEFAULT_IMPORTED_EVENTS_FILE}"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the imported-events state file and try all ICS events.",
    )
    parser.add_argument(
        "--mark-imported",
        action="store_true",
        help="Mark the parsed ICS events as imported without opening Playwright.",
    )
    args = parser.parse_args()

    events = parse_ics_events(args.ics)
    if not events:
        raise SystemExit(f"No VEVENT entries found in {args.ics}")

    imported_events = load_imported_events(args.imported_events_file)

    if args.mark_imported:
        for event in events:
            imported_events[event.import_key] = imported_event_record(event)
        save_imported_events(args.imported_events_file, imported_events)
        print(f"Marked {len(events)} event(s) imported in {args.imported_events_file}")
        return 0

    skipped: list[CalendarEvent] = []
    if not args.force:
        events, skipped = filter_new_events(events, imported_events)

    if args.dry_run:
        if skipped:
            print(f"Skipping {len(skipped)} already imported event(s):")
            for event in skipped:
                print(f"- {event.start_date} {event.start_time}: {event.summary}")
        if events:
            print(f"Pending {len(events)} event(s):")
        for event in events:
            location = f" at {event.location}" if event.location else ""
            print(
                f"{event.start_date} {event.start_time}-{event.end_time}: "
                f"{event.summary}{location}"
            )
        if not events:
            print("No new events to import.")
        return 0

    if skipped:
        print(f"Skipping {len(skipped)} already imported event(s).")
    if not events:
        print("No new events to import.")
        return 0

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Playwright is not installed for this Python environment. "
            "Install it with: python3 -m pip install playwright"
        ) from exc

    with sync_playwright() as playwright:
        run(
            playwright,
            events,
            args.calendar_id,
            args.attempts,
            args.imported_events_file,
            imported_events,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
