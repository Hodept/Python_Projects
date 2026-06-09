# Church Calendar Automation

This folder contains two scripts that work together:

- `extract_church_calendar.py` reads newsletter PDFs, finds the `Calendar Events` or `Calendar Items` section, and creates an `.ics` calendar file.
- `Event_Playwrite_calendar_creator.py` reads the `.ics` file and uses Playwright to add those events to the Church calendar website.

The normal workflow is:

```bash
cd /home/hodept/.git/Python_Projects/Event_Automation
python3 extract_church_calendar.py --json-output church_calendar_events.json
python3 Event_Playwrite_calendar_creator.py --dry-run
python3 Event_Playwrite_calendar_creator.py
```

## Folder Layout

```text
Event_Automation/
├── Church Newsletters/
│   └── *.pdf
├── extract_church_calendar.py
├── Event_Playwrite_calendar_creator.py
├── church_calendar.ics
├── church_calendar_events.json
├── church_calendar_imported_events.json
└── playwright_failures/
```

Important files:

- `Church Newsletters/`: Put weekly newsletter PDFs here.
- `church_calendar.ics`: The generated calendar file.
- `church_calendar_events.json`: Optional readable event details for review/debugging.
- `church_calendar_imported_events.json`: Tracks events already added to the Church site so duplicates are skipped.
- `playwright_failures/`: Screenshots and HTML saved when Playwright has trouble.

## Script 1: Extract Newsletter Events

Run the extractor from the `Event_Automation` folder:

```bash
python3 extract_church_calendar.py
```

This reads PDFs from the current folder and writes:

```text
church_calendar.ics
```

To read PDFs from another folder, pass the folder path:

```bash
python3 extract_church_calendar.py --input-dir "/path/to/newsletter pdf folder"
```

### Generate ICS and JSON

```bash
python3 extract_church_calendar.py --json-output church_calendar_events.json
```

Use this most of the time. The `.ics` file is used by the Playwright importer, and the `.json` file is easier to inspect by hand.

The extractor also adds a short subject-focused hype statement to each event description before writing the `.ics` file.

### Pause and Add Event Details

Use interactive mode when you want to review each event and add extra description details before the `.ics` file is written:

```bash
python3 extract_church_calendar.py --interactive --json-output church_calendar_events.json
```

For each event, the script shows the subject, time, location, and suggested hype statement. Type any extra details you want included, then press Enter on a blank line to continue to the next event.

### Use a Different PDF Folder

```bash
python3 extract_church_calendar.py --input-dir "/path/to/newsletter pdf folder"
```

The positional form also works:

```bash
python3 extract_church_calendar.py "/path/to/newsletter pdf folder"
```

### Write to a Different ICS File

```bash
python3 extract_church_calendar.py -o my_calendar.ics
```

### Set the Timezone

```bash
python3 extract_church_calendar.py --timezone America/Los_Angeles
```

The default is `America/Los_Angeles`.

### Extractor Help

```bash
python3 extract_church_calendar.py --help
```

## Script 2: Add Events to Church Calendar

Before using the importer, generate the `.ics` file first:

```bash
python3 extract_church_calendar.py --json-output church_calendar_events.json
```

Then review what the importer will add:

```bash
python3 Event_Playwrite_calendar_creator.py --dry-run
```

If the dry run looks right, run the browser automation:

```bash
python3 Event_Playwrite_calendar_creator.py
```

The script opens Chrome. If `CHURCH_USERNAME` and `CHURCH_PASSWORD` are not set, it pauses so you can sign in manually. After signing in, return to the terminal and press Enter.

## Duplicate Protection

The importer avoids adding the same ICS event more than once.

After an event is successfully published, it records that event in:

```text
church_calendar_imported_events.json
```

Future runs skip anything already recorded there.

### Check What Will Be Skipped

```bash
python3 Event_Playwrite_calendar_creator.py --dry-run
```

The dry run shows skipped events and pending events.

### Mark Current ICS Events as Already Imported

Use this if you already added the current `.ics` events manually or with an earlier version of the script:

```bash
python3 Event_Playwrite_calendar_creator.py --mark-imported
```

This does not open Playwright. It only updates `church_calendar_imported_events.json`.

### Force Re-import Everything

Use this only when you intentionally want to ignore duplicate tracking:

```bash
python3 Event_Playwrite_calendar_creator.py --force
```

You can combine it with dry-run:

```bash
python3 Event_Playwrite_calendar_creator.py --dry-run --force
```

## Retry and Slow Site Handling

The Church calendar site can be slow. The importer waits longer than Playwright's default and retries each event.

Default retry count:

```text
3 attempts per event
```

Use more attempts:

```bash
python3 Event_Playwrite_calendar_creator.py --attempts 5
```

If an attempt fails, the script saves a screenshot and page HTML under:

```text
playwright_failures/
```

Failed events are not marked as imported, so they can be retried later.

## Use a Different ICS File

```bash
python3 Event_Playwrite_calendar_creator.py --ics my_calendar.ics
```

## Use a Different Church Calendar ID

The current default calendar select value is `392790`.

To use a different calendar:

```bash
python3 Event_Playwrite_calendar_creator.py --calendar-id 123456
```

## Use a Different Duplicate Tracking File

```bash
python3 Event_Playwrite_calendar_creator.py --imported-events-file my_imported_events.json
```

This is useful for testing.

## Optional Login Environment Variables

You can avoid manual username/password entry by setting:

```bash
export CHURCH_USERNAME="your_username"
export CHURCH_PASSWORD="your_password"
python3 Event_Playwrite_calendar_creator.py
```

If those variables are not set, the script will ask you to sign in manually in the browser.

## Recommended Weekly Workflow

1. Add the new newsletter PDF to `Church Newsletters/`.
2. Generate the ICS and JSON:

```bash
python3 extract_church_calendar.py --json-output church_calendar_events.json
```

3. Review pending and skipped events:

```bash
python3 Event_Playwrite_calendar_creator.py --dry-run
```

4. Import only new events:

```bash
python3 Event_Playwrite_calendar_creator.py
```

5. If the site is slow or flaky, rerun with more attempts:

```bash
python3 Event_Playwrite_calendar_creator.py --attempts 5
```

## Troubleshooting

If no events are found:

- Make sure the PDF has a section titled `Calendar Events` or `Calendar Items`.
- Run the extractor again and check `church_calendar_events.json`.

If Playwright fails during import:

- Check `playwright_failures/` for the screenshot and HTML from the failed step.
- Re-run with more attempts:

```bash
python3 Event_Playwrite_calendar_creator.py --attempts 5
```

If events were already added but are still pending:

```bash
python3 Event_Playwrite_calendar_creator.py --mark-imported
```

If you need to intentionally add an event again:

```bash
python3 Event_Playwrite_calendar_creator.py --force
```
