# Photo Organizer

A photo-first organizer that runs offline by default for local folders and mounted network storage on Windows, macOS, or Linux. It recursively inventories photos, optionally expands archives into local staging, reads capture times and GPS, proposes contextual album folders, broad location folders, and descriptive filenames, then moves photos into a new library only after explicit approval. Each move copies and verifies the destination before deleting the source photo. Archives remain intact.

## Requirements

- Python 3.10 or newer; no Python packages are required. The examples use `python`; use `python3` or `py` if that is how your installation is configured.
- [ExifTool](https://exiftool.org/) installed for your operating system and available as `exiftool` on your PATH. Check with `exiftool -ver`.
- Space for a new photo library on the destination and expanded archives in the state directory. Keep the state directory on a local disk for responsive SQLite access.

## First run on a small representative folder

Run commands from the project directory. `SOURCE_DIRECTORY` and `DESTINATION_DIRECTORY` below are placeholders: replace them with your own source and destination paths. The source can be a local folder, mounted network share, or mapped drive accessible to the current user. The destination must be outside the source tree.

The example `.photo-organizer-state` is a private working directory relative to the current directory; you can choose a different local directory using `--state`. Use a separate state directory for a pilot and the full collection. Add `--max-photos 600` to `scan` for a quick pilot; it saves a partial inventory in traversal order and leaves naming unchanged. The scan report records the limit. This is not a random sample; omit the option to scan the full source. Commands are on single lines so they can be used in common shells without platform-specific line continuations.

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

The default `--grouping broad` uses this priority:

1. Explicitly named events from configuration stay together.
2. Meaningful source albums stay together across dates and cities, including photos without dates or GPS.
3. Other photos share year/location folders. Time gaps do not split these folders.

```text
OrganizedPhotos/
  Albums/
    Summer-trip/
      2020-06-20_15-04-22_Summer-trip_a94d9f620231.jpg
  Events/
    2020-06-20_Our-wedding/
  2024/
    Vancouver/
    Seattle_Washington_United-States/
    Unknown-location/
  Unknown-date/
    Unknown-location/
```

Generic source folders such as `Archive`, `Photos from 2013`, date-only names, camera folders, and month/year folders do not become albums automatically. The first meaningful parent beneath the scanned source root supplies album context, so nested date folders do not break up a trip. This is a name-based heuristic, not semantic understanding. Matching album labels merge even if they occur under different branches; configure distinct names if those are separate collections.

For remaining photos, configured named places take priority, followed by cached city/region/country names (enabled by default). If a name has not been resolved, photos are placed in clearly labelled `Location-to-review-00001` groups instead of coordinate-named folders. These unresolved groups use a 20 km radius around a fixed anchor, adjustable with `--location-radius-km`. A point must be within that radius of the anchor: chains of nearby points do not keep expanding the area. This approximates local areas rather than administrative city boundaries. Photos in one area share a folder within each year; albums can span multiple years. Missing GPS falls back to the year’s `Unknown-location`, and missing dates to `Unknown-date`, unless album or named-event context supplies a group.

The CSV records the grouping reason in `grouping_basis`, original path, full checksum, timestamp source, GPS, label, and destination. Exact GPS remains in the CSV even when the folder represents a broad area or album. `duplicate_of` flags byte-identical photos; duplicates are retained as separate destination files. Edited images and RAW/JPEG pairs are separate files. Timestamps order filenames, labels add context, and short content hashes plus collision suffixes distinguish files.

## Naming events and places

Copy `config.example.json` to `config.local.json` and replace the sample values. Example dates and coordinates are illustrative, not inferred from your collection. Then regenerate:

```text
python photo_organizer.py plan --state ".photo-organizer-state" --config "config.local.json" --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/plan.csv"
```

- `places`: name, latitude, longitude, and optional `radius_km` (default 10). The first matching place wins. Without a match, cached API city names are used when enabled, otherwise location-review labels are used. No coordinates leave the computer unless you explicitly run the geocoding fetch step.
- `events`: name, inclusive local `start` and `end`. A named range can group a wedding across several days. Optional `source_contains` limits it to matching original paths; remove it to match all photos in that time range. Optional `place` restricts matching to that configured place. First matching event wins.
- `source_albums`: optional `source_folder` and `name` mappings to explicitly keep a source album together or rename it. Match a folder component or relative parent path; first match wins.
- `ignore_source_folders`: names to treat as generic containers rather than albums. Explicit `source_albums` rules take priority.
- `--ignore-source-context` disables source-album grouping for a location-only pass.
- `--grouping events` restores the earlier fine-grained time grouping: split after a six-hour gap, 36 hours from the group start, or a GPS change over 30 km. Adjust with `--gap-hours`, `--max-event-hours`, and `--distance-km`. These three options affect event mode only; broad mode uses `--location-radius-km` instead.
- `--preserve-folders` includes the original parent hierarchy underneath each proposed folder. This can increase folder count; leave it off for broad organization.
- For specific adjustments, edit **only destination paths** in the CSV before applying; paths must be relative to the library, unique, and free of traversal. Use a CSV editor that preserves the other fields. You can remove rows to apply a subset. Changing the `event` column alone does not rename destinations.

Group suggestions are not semantic recognition. Review source album choices and add ignore rules for generic folders with unusual names. In event mode, a long wedding with no photos overnight can split, and unrelated cameras used at the same time can merge when GPS is absent. In broad mode, repeated visits to the same area within a year are intentionally combined. Album context does not invent missing GPS or timestamps.

Keep the reviewed plan when resuming: area anchors and event numbers can change when photos or rules change. Applying a different plan into an existing library can create additional copies; this is not a synchronization engine.

## Optional city names from a reverse-geocoding API

Geoapify can resolve coordinates into city, region, and country fields. The separate `geocode` step caches results in the local inventory. Scanning, planning, and applying never make network requests.

1. Obtain a key from [Geoapify](https://www.geoapify.com/).
2. Set `GEOAPIFY_API_KEY` in the environment of the process running the script, or save only the key in `geoapify-key.txt` inside your chosen state directory. The program does not load `.env` files automatically. Keep credentials out of the public repository.
3. Preview the number of API calls, then fetch and regenerate a plan:

```text
python photo_organizer.py geocode --state ".photo-organizer-state"
python photo_organizer.py geocode --state ".photo-organizer-state" --fetch
python photo_organizer.py plan --state ".photo-organizer-state" --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/plan-city.csv"
```

Only rounded coordinates and request options are sent to Geoapify; photos, paths, filenames, and timestamps remain local. The [city-level reverse-geocoding endpoint](https://apidocs.geoapify.com/docs/geocoding/reverse-geocoding/) returns location information without requiring street-level folder names. A two-decimal coordinate cache is the default (approximately 1.1 km in latitude; longitude distance varies). Nearby coordinates in the same rounded cell reuse a lookup. Cells near municipal boundaries can receive an adjacent city's label; use `--precision 3` or `4` when fetching and the matching `--geocode-precision` when planning if greater precision is needed. This increases the number of requests. `--language` defaults to `en` and must match in both commands.

The default cap is 200 uncached lookups per invocation, with at least one second between requests. `--max-requests` and `--request-interval` can be adjusted for your provider plan. The cap is checked before requests begin. Authentication, quota, and network failures stop the run; completed results remain cached for the next run. Empty results are cached too. Repeating a completed fetch needs no API calls. Results and attribution are saved locally; request URLs and API keys are not logged. Provider quotas or charges depend on your account.

Planning automatically uses cached city/region/country names and groups matching labels within each year; no extra flag is needed. `--no-use-geocoding` explicitly disables cache use. Named events and source albums still take priority, so a two-city trip album stays together. Missing cache entries or unresolved locations use `Location-to-review` groups; photos without GPS still use source context or year-only grouping. The CSV includes `location_source`, `city`, `region`, and `country` for review. County, region, or country fallbacks are explicitly labelled rather than presented as cities.

Optional `place_aliases` in your configuration map the complete generated place label to a preferred metro-area label. For example, mapping both `Example-City_Example-Region_Example-Country` and `Nearby-Town_Example-Region_Example-Country` to `Example metro area` combines them. Copy the exact labels from the CSV after the first city-name plan. Configured radius-based `places` override API labels. Text similarity alone is not used to merge places, since different places may share names.

Attribution: Powered by [Geoapify](https://www.geoapify.com/), with data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright). Attribution is also included in the geocoding report and plan summary when enabled. This implementation does not use the public Nominatim service.

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

The included `.gitignore` excludes the default state directory, Python caches, SQLite inventories, generated plan/report/log filenames, , `config.local.json`, credential files, and environment files. Keep custom-named runtime output outside the repository or add it to your own ignore rules. Git ignore rules do not remove files that were already tracked or erase Git history; inspect the files staged for publication. The checked-in example configuration contains fictional event information and public example coordinates.

## Validation and extension

```text
python -m unittest discover -s tests -v
```

Tests cover geocoding response parsing, coordinate caching, throttling, credential-safe errors, offline previews, city grouping, broad GPS grouping, fixed-radius boundaries, source-album context across cities and dates, configuration overrides, approval, verified moves, transfer recovery, destination matching, nested archive scanning, traversal and link rejection, expansion bounds, incremental inventory, metadata dates, event splitting, explicit labels, missing metadata, duplicates, checksum failures, collision refusal, and resume behavior. Metadata extraction is mocked in automated tests; validate the installed ExifTool against a pilot containing actual camera/phone formats before the full NAS run.

The stages are separate so later document/video organizers can reuse the inventory, review-plan, and verified-copy approach with the same explicit approval and transfer verification workflow.
