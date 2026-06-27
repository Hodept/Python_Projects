#!/usr/bin/env python3
"""Extract church newsletter calendar items from PDFs and write an ICS file."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_OUTPUT_FILE = Path(__file__).with_name("church_calendar.ics")
DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_DURATION_MINUTES = 60
DEFAULT_TBD_START_HOUR = 9
HYPE_PREFIX = "Hype statement:"

SECTION_RE = re.compile(r"^Calendar (?:Events|Items)\s*$", re.IGNORECASE)
NEXT_SECTION_RE = re.compile(r"^[A-Z][A-Za-z& ]{2,}$")
DATE_RE = re.compile(
    r"^(?:[●○]\s*)?(?:(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*)?"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:,\s*(\d{4}))?$",
    re.IGNORECASE,
)
TIME_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|am|pm)\b",
    re.IGNORECASE,
)
TBD_TIME_RE = re.compile(r"\btime\s*[,:-]?\s*TBD\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20\d{2})\b")


@dataclass(frozen=True)
class CalendarEvent:
    summary: str
    start: datetime
    end: datetime
    location: str = ""
    description: str = ""
    source_pdf: str = ""


def pdf_to_text(pdf_path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def infer_year(pdf_path: Path, text: str) -> int:
    years = [int(match.group(1)) for match in YEAR_RE.finditer(pdf_path.name)]
    if not years:
        years = [int(match.group(1)) for match in YEAR_RE.finditer(text)]
    return years[0] if years else datetime.now().year


def clean_line(line: str) -> str:
    line = line.replace("\u200b", "")
    return re.sub(r"\s+", " ", line).strip()


def strip_bullet(line: str) -> str:
    return re.sub(r"^[●○]\s*", "", line).strip()


def calendar_section_lines(text: str) -> list[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if SECTION_RE.match(clean_line(line)):
            section: list[str] = []
            for next_line in lines[index + 1 :]:
                cleaned = clean_line(next_line)
                if cleaned and NEXT_SECTION_RE.match(strip_bullet(cleaned)):
                    break
                section.append(cleaned)
            return section
    return []


def parse_date(line: str, default_year: int, timezone: ZoneInfo) -> datetime | None:
    match = DATE_RE.match(strip_bullet(line))
    if not match:
        return None

    month_name = match.group(2)
    day = int(match.group(3))
    year = int(match.group(4) or default_year)
    month = datetime.strptime(month_name[:3], "%b").month
    return datetime(year, month, day, tzinfo=timezone)


def parse_time(text: str) -> tuple[int, int] | None:
    match = TIME_RE.search(text)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    marker = match.group(3).lower().replace(".", "")
    if marker == "pm" and hour != 12:
        hour += 12
    elif marker == "am" and hour == 12:
        hour = 0
    return hour, minute


def split_event_text(text: str) -> tuple[str, str]:
    time_match = TIME_RE.search(text)
    tbd_time_match = TBD_TIME_RE.search(text)
    without_time = TBD_TIME_RE.sub("", TIME_RE.sub("", text)).strip(" ,-")
    parts = [part.strip(" ,") for part in without_time.split(",") if part.strip(" ,")]

    if len(parts) >= 2:
        return ", ".join(parts[:-1]), parts[-1]
    if time_match or tbd_time_match:
        return without_time, ""
    return text.strip(), ""


def event_subject(event: CalendarEvent) -> str:
    return event.summary.strip(" .")


def build_hype_statement(event: CalendarEvent) -> str:
    subject = event_subject(event)
    if not subject:
        return ""

    location = f" at {event.location}" if event.location else ""
    return f"Come be part of {subject}{location} - a meaningful opportunity to gather, connect, and be uplifted."


def description_with_hype(event: CalendarEvent, extra_details: str = "") -> str:
    parts = [
        f"{HYPE_PREFIX} {build_hype_statement(event)}",
        "",
        "Event details:",
        event.description,
    ]
    if extra_details.strip():
        parts.extend(["", "Additional details:", extra_details.strip()])
    return "\n".join(part for part in parts if part is not None)


def prompt_multiline_details() -> str:
    print("Add any extra details for this event. Press Enter on a blank line to keep moving.")
    lines: list[str] = []
    while True:
        line = input("> ")
        if not line:
            break
        lines.append(line)
    return "\n".join(lines)


def enrich_event_descriptions(events: list[CalendarEvent], interactive: bool) -> list[CalendarEvent]:
    enriched: list[CalendarEvent] = []
    sorted_events = sorted(events, key=lambda item: (item.start, item.summary))
    for index, event in enumerate(sorted_events, start=1):
        extra_details = ""
        if interactive:
            print()
            print(f"Event {index} of {len(sorted_events)}")
            print(f"Subject: {event_subject(event)}")
            print(f"When: {event.start:%Y-%m-%d %I:%M %p}")
            if event.location:
                print(f"Where: {event.location}")
            print(f"Suggested hype: {build_hype_statement(event)}")
            extra_details = prompt_multiline_details()
        enriched.append(
            replace(event, description=description_with_hype(event, extra_details))
        )
    return enriched


def parse_calendar_events(pdf_path: Path, timezone_name: str) -> list[CalendarEvent]:
    timezone = ZoneInfo(timezone_name)
    text = pdf_to_text(pdf_path)
    default_year = infer_year(pdf_path, text)
    lines = [line for line in calendar_section_lines(text) if line]

    events: list[CalendarEvent] = []
    current_date: datetime | None = None
    current_event_parts: list[str] = []

    def flush_event() -> None:
        nonlocal current_event_parts
        if current_date is None or not current_event_parts:
            current_event_parts = []
            return

        raw_event = " ".join(current_event_parts)
        parsed_time = parse_time(raw_event)
        has_tbd_time = bool(TBD_TIME_RE.search(raw_event))
        if not parsed_time and not has_tbd_time:
            current_event_parts = []
            return

        hour, minute = parsed_time or (DEFAULT_TBD_START_HOUR, 0)
        start = current_date.replace(hour=hour, minute=minute)
        summary, location = split_event_text(raw_event)
        events.append(
            CalendarEvent(
                summary=summary,
                start=start,
                end=start + timedelta(minutes=DEFAULT_DURATION_MINUTES),
                location=location,
                description=raw_event,
                source_pdf=pdf_path.name,
            )
        )
        current_event_parts = []

    for line in lines:
        parsed_date = parse_date(line, default_year, timezone)
        if parsed_date:
            flush_event()
            current_date = parsed_date
        elif current_date:
            if line.startswith("○"):
                flush_event()
            current_event_parts.append(strip_bullet(line))

    flush_event()
    return events


def escape_ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\n", r"\n")
    )


def fold_ics_line(line: str) -> str:
    chunks = [line[i : i + 74] for i in range(0, len(line), 74)]
    return "\r\n ".join(chunks)


def format_ics_datetime(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def event_uid(event: CalendarEvent) -> str:
    fingerprint = f"{event.start.isoformat()}|{event.summary}|{event.location}"
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"{digest}@church-newsletter"


def build_ics(events: list[CalendarEvent], timezone_name: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Church Newsletter Calendar Extractor//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for event in sorted(events, key=lambda item: (item.start, item.summary)):
        description = event.description
        if event.source_pdf:
            description = f"{description}\nSource: {event.source_pdf}"

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event_uid(event)}",
                f"DTSTAMP:{timestamp}",
                f"DTSTART;TZID={timezone_name}:{format_ics_datetime(event.start)}",
                f"DTEND;TZID={timezone_name}:{format_ics_datetime(event.end)}",
                f"SUMMARY:{escape_ics_text(event.summary)}",
            ]
        )
        if event.location:
            lines.append(f"LOCATION:{escape_ics_text(event.location)}")
        if description:
            lines.append(f"DESCRIPTION:{escape_ics_text(description)}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def event_to_dict(event: CalendarEvent) -> dict[str, str]:
    return {
        "summary": event.summary,
        "description": event.description,
        "location": event.location,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "source_pdf": event.source_pdf,
    }


def dedupe_events(events: list[CalendarEvent]) -> list[CalendarEvent]:
    unique: dict[tuple[datetime, str, str], CalendarEvent] = {}
    for event in events:
        key = (event.start, event.summary.casefold(), event.location.casefold())
        unique.setdefault(key, event)
    return list(unique.values())


def selected_input_dir(positional_dir: Path | None, option_dir: Path | None) -> Path:
    input_dir = option_dir or positional_dir or Path.cwd()
    return input_dir.expanduser()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract Calendar Events/Items sections from newsletter PDFs to ICS."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        type=Path,
        help="Directory containing newsletter PDFs. Default: current working directory.",
    )
    parser.add_argument(
        "-i",
        "--input-dir",
        dest="input_dir_option",
        type=Path,
        help="Directory containing newsletter PDFs. Overrides the positional folder.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"ICS file to create. Default: {DEFAULT_OUTPUT_FILE}",
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"IANA timezone for event times. Default: {DEFAULT_TIMEZONE}",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional JSON file with the same parsed event details.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "Pause at each parsed event before writing the ICS so you can add extra "
            "description details."
        ),
    )
    args = parser.parse_args()

    input_dir = selected_input_dir(args.input_dir, args.input_dir_option)
    if not input_dir.is_dir():
        raise SystemExit(f"Input path is not a directory: {input_dir}")

    pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No PDF files found in {input_dir}")

    events: list[CalendarEvent] = []
    for pdf_path in pdfs:
        events.extend(parse_calendar_events(pdf_path, args.timezone))

    events = enrich_event_descriptions(dedupe_events(events), args.interactive)
    args.output.write_text(build_ics(events, args.timezone), encoding="utf-8")
    if args.json_output:
        payload = [event_to_dict(event) for event in sorted(events, key=lambda item: item.start)]
        args.json_output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(events)} event(s) to {args.output}")
    for event in sorted(events, key=lambda item: item.start):
        location = f" at {event.location}" if event.location else ""
        print(f"- {event.start:%Y-%m-%d %I:%M %p}: {event.summary}{location}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
