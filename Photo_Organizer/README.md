# Photo Organizer

A photo-first, offline organizer for local folders and mounted network storage on Windows, macOS, or Linux. It recursively inventories photos, optionally expands archives into local staging, reads capture times and GPS, proposes event folders and descriptive filenames, then moves photos into a new library only after explicit approval. Each move copies and verifies the destination before deleting the source photo. Archives remain intact.

## Requirements

- Python 3.10 or newer; no Python packages are required. The examples use `python`; use `python3` or `py` if that is how your installation is configured.
- [ExifTool](https://exiftool.org/) installed for your operating system and available as `exiftool` on your PATH. Check with `exiftool -ver`.
- Space for a new photo library on the destination and expanded archives in the state directory. Keep the state directory on a local disk for responsive SQLite access.

## First run on a small representative folder

Run commands from the project directory. `SOURCE_DIRECTORY` and `DESTINATION_DIRECTORY` below are placeholders: replace them with your own source and destination paths. The source can be a local folder, mounted network share, or mapped drive accessible to the current user. The destination must be outside the source tree.

The example `.photo-organizer-state` is a private working directory relative to the current directory; you can choose a different local directory using `--state`. Use a separate state directory for a pilot and the full collection. Commands are on single lines so they can be used in common shells without platform-specific line continuations.

```text
python photo_organizer.py scan "SOURCE_DIRECTORY" --state ".photo-organizer-state" --extract-archives
python photo_organizer.py plan --state ".photo-organizer-state" --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/plan.csv"
```

Read `scan-report.json`, `plan.csv`, and `plan.summary.json` in the state directory. A scan with issues exits with code 1 but still saves successfully scanned photos. Missing dates are retained; files ExifTool cannot read are reported and omitted from the plan. Permission failures, unsupported compression, and expansion limits are also reported.

Preview the planned operation (this does not create the destination or transfer photos):

```text
python photo_organizer.py apply --state ".photo-organizer-state" --plan ".photo-organizer-state/plan.csv" --destination "DESTINATION_DIRECTORY"
```

After reviewing the CSV and preview, explicitly approve:

```text
python photo_organizer.py apply --state ".photo-organizer-state" --plan ".photo-organizer-state/plan.csv" --destination "DESTINATION_DIRECTORY" --approve
```

`apply` defaults to move mode. Without `--approve`, it only validates the plan and displays the source, destination, mode, and photo count. With approval, it creates the destination folder and transfers the planned photos using the new names and structure. The destination must match the one recorded when generating the plan. Keep the companion `plan.summary.json` alongside the CSV.

For each ordinary source photo, the script verifies the source checksum, copies the file, verifies and flushes the destination, records the verified transfer in SQLite, rechecks the source, and only then removes the original. Existing matching destination files are verified before source removal. Conflicts and verification failures retain the source. Empty original folders remain. Use `--mode copy --approve` to retain all originals instead.

Photos extracted from archives are copied from staging; both the extraction cache and original archive remain intact. Other file types are not moved in this photo-first phase. Repeating the same approved command resumes using the transfer journal and checksums, including a crash after source removal. An operation log is appended to `apply-log.jsonl`.

## Folder and filename design

With a named event and known place:

```text
OrganizedPhotos/
  2020/
    2020-06-20_Our-wedding_Vancouver/
      2020-06-20_15-04-22_Our-wedding_Vancouver_a94d9f620231.jpg
  2024/
    2024-08-14_Event-00012_GPS-48.43_-123.37/
      2024-08-14_09-30-00_Event-00012_GPS-48.43_-123.37_41a7081dd038.heic
  Unknown-date/
    Unknown-location/
      old-family-photo_2bd810610116.jpg
```

A year-first tree stays predictable across decades; event folders include their date and place. The timestamp orders photos within an event, the label adds context, and a short content hash prevents same-second camera collisions. Additional suffixes preserve repeated copies. The CSV records the original path, full checksum, timestamp source, GPS, event, and destination. `duplicate_of` flags byte-identical photos; duplicates are not deleted or collapsed. Edited images and RAW/JPEG pairs are separate files.

## Naming events and places

Copy `config.example.json` to `config.local.json` and replace the sample values. Example dates and coordinates are illustrative, not inferred from your collection. Then regenerate:

```text
python photo_organizer.py plan --state ".photo-organizer-state" --config "config.local.json" --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/plan.csv"
```

- `places`: name, latitude, longitude, and optional `radius_km` (default 10). The first matching place wins. Without a match, GPS is shown as coordinates; no coordinates are sent to a geocoding service.
- `events`: name, inclusive local `start` and `end`. A named range can group a wedding across several days. Optional `source_contains` limits it to matching original paths; remove it to match all photos in that time range. Optional `place` restricts matching to that configured place. First matching event wins.
- Automatic groups split after a six-hour gap, after 36 hours from the group start, or when consecutive known GPS positions differ by over 30 km. Adjust with `--gap-hours`, `--max-event-hours`, and `--distance-km` on `plan`.
- `--preserve-folders` on `plan` includes the original parent hierarchy underneath each event folder.
- For specific adjustments, edit **only destination paths** in the CSV before applying; paths must be relative to the library, unique, and free of traversal. Use a CSV editor that preserves the other fields. You can remove rows to apply a subset. Changing the `event` column alone does not rename destinations.

Automatic groups are suggestions, not semantic recognition. A long wedding with no photos overnight can split; unrelated cameras used at the same time can merge when GPS is absent. Photos without GPS may share an event's location in their proposed folder, but the CSV retains their missing GPS and `Unknown-location` value. Group numbers can change when photos or rules change, so keep the reviewed plan when resuming. Applying a different plan into an existing library can create additional copies; it is not a synchronization engine.

## Metadata and formats

Capture time preference: `SubSecDateTimeOriginal`, `DateTimeOriginal`, then `CreateDate`. The chosen field appears in `date_source`, so the weaker CreateDate fallback is visible. Available timezone offsets are retained in the CSV. Grouping uses the camera's local wall-clock time; it does not invent missing timezones or correct camera clocks. Filesystem modification time and filename dates are deliberately not treated as capture times. Photos lacking metadata dates go to `Unknown-date`.

Photo extensions include JPEG, HEIC/HEIF, PNG, TIFF, WebP, AVIF, GIF, BMP, JXL, DNG, and common camera RAW formats. Metadata extraction uses ExifTool's [JSON and numeric output](https://exiftool.org/exiftool_pod.html). The program does not inspect visual content or identify people.

ZIP and TAR (including gzip, bzip2, and xz-compressed TAR) are expanded only with `--extract-archives`. Nested archives are supported up to three levels. Defaults cap expanded content at 20 GiB and 100,000 members per scan, including reused extraction caches. Increase with `--max-expanded-gb`, `--max-archive-members`, and `--max-archive-depth` as appropriate for available local space. Member paths, links, and special files are checked; an unsafe or corrupt archive is reported and its incomplete extraction removed. Archives are left intact. Password-protected archives, RAR, 7z, standalone compressed streams, and other unsupported formats are reported for separate extraction.

Other documents and videos are counted but not organized. Archive staging retains their regular files. External XMP/AAE sidecars, Google Takeout JSON, and Live Photo videos are not paired or copied into the new library in this version. Embedded metadata stays intact because photos are copied byte-for-byte. Use copy mode if you need the existing photo/sidecar or Live Photo relationships preserved: move mode relocates the still photo but leaves companions at their original paths.

## Large NAS collections and recovery

The initial scan reads photo bytes once for SHA-256 and reads metadata in batches of 100. Repeat scans reuse photos with unchanged size and modification time; use `scan --refresh` to reread all metadata and hashes. Extraction caches also use the archive path, size, and modification time; rebuilding the state directory forces full re-extraction. Avoid modifying the source collection during a run.

Apply reads each source to check that it still matches the inventory, copies it, then rereads the destination to verify it. Move mode also rechecks both files before removing the source. This is intentionally I/O-heavy on a slow NAS. A copy between two network folders passes through the computer running the script; this script does not request a server-side copy. Repeated apply runs still verify available source and destination content. Completed moves are recognized from the journal when the source is absent. No throughput estimate is possible without a sample run.

- Keep the reviewed CSV and state directory until the organization is complete.
- Only one process can use a given state directory at a time. Do not run different state directories into the same destination concurrently.
- Normal copy errors clean up the file created by that operation. A power loss or forced kill can leave a partial destination. The next run refuses to overwrite it: inspect the logged conflict, move that partial file outside the destination path, and rerun. The source for an interrupted, incomplete copy remains intact; earlier successfully moved photos are already at their destinations.
- A forced kill can also leave `organizer.lock`; remove it only after confirming that no organizer process is running. Incomplete extraction directories are ignored and can be removed after the process stops.
- Paths are checked for symlinks and traversal. Use a destination you control; simultaneous external renames or symlink changes during execution are not supported.
- `--exclude "DIRECTORY_TO_EXCLUDE"` can be repeated on scan to omit folders. Symlinks are not followed. Deleted or failed-to-read files are omitted from the latest completed scan's plan, while historical inventory rows remain in SQLite.
- An apply failure exits with code 1. Setup or invalid-plan errors exit with code 2.

## Publishing the code

Publish the source, tests, documentation, and example configuration. Runtime inventories, plans, reports, logs, and personal configuration are private: they contain absolute file paths and may contain filenames, photo timestamps, and GPS coordinates. Absolute paths are needed locally for reliable transfers; they are not hardcoded in the program.

The included `.gitignore` excludes the default state directory, Python caches, SQLite inventories, generated plan/report/log filenames, and `config.local.json`. Keep custom-named runtime output outside the repository or add it to your own ignore rules. Git ignore rules do not remove files that were already tracked or erase Git history; inspect the files staged for publication. The checked-in example configuration contains fictional event information and public example coordinates.

## Validation and extension

```text
python -m unittest discover -s tests -v
```

Tests cover approval, verified moves, transfer recovery, destination matching, nested archive scanning, traversal and link rejection, expansion bounds, incremental inventory, metadata dates, event splitting, explicit labels, missing metadata, duplicates, checksum failures, collision refusal, and resume behavior. Metadata extraction is mocked in automated tests; validate the installed ExifTool against a pilot containing actual camera/phone formats before the full NAS run.

The stages are separate so later document/video organizers can reuse the inventory, review-plan, and verified-copy approach with the same explicit approval and transfer verification workflow.
