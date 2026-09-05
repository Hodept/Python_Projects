# Photo Organizer

Photo Organizer inventories a photo collection, proposes readable folders and filenames, and produces a CSV plan for review. Nothing is transferred until you run the final command with `--approve`.

The tool works with local folders, mounted network storage, and mapped drives on Windows, macOS, and Linux. It can use existing folder names, capture dates, and GPS metadata. An optional Geoapify lookup converts GPS coordinates into city, region, and country names.

## Before you start

Install:

- Python 3.10 or newer. The commands below use `python`; substitute `python3` or `py` if needed.
- [ExifTool](https://exiftool.org/) and confirm that `exiftool -ver` works in your terminal.
- Enough free space for the organized library. Archive extraction also needs local working space.

Choose three paths:

| Placeholder | Purpose |
| --- | --- |
| `SOURCE_DIRECTORY` | Existing photo collection to scan |
| `DESTINATION_DIRECTORY` | New organized library; it must be outside the source tree |
| `.photo-organizer-state` | Private working directory for inventory, plans, logs, and cached metadata |

Keep the state directory on a local disk when scanning network storage. Use a separate state directory for each source collection.

Run all commands from the project directory. Paths containing spaces must be quoted.

## 1. Start with a pilot

Test the workflow on a limited number of photos before scanning the full collection:

```text
python photo_organizer.py scan "SOURCE_DIRECTORY" --state ".photo-organizer-state" --extract-archives --max-photos 600
```

This command:

- walks the source folder and its subfolders;
- reads photo metadata and calculates a full SHA-256 hash;
- expands supported archives into private staging when `--extract-archives` is present;
- stops after 600 photos and records that the inventory is partial;
- leaves every source file unchanged.

The pilot follows normal folder traversal order; it is not a random sample.

Create a proposed organization plan:

```text
python photo_organizer.py plan --state ".photo-organizer-state" --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/plan.csv"
```

The destination is recorded in the plan summary, but the destination folder is not created yet.

## 2. Review the pilot

Open these files in the state directory:

| File | What to review |
| --- | --- |
| `scan-report.json` | Photo count, archive count, partial-scan status, and metadata warnings |
| `plan.csv` | Original path, proposed destination, date source, location, grouping reason, and duplicate status |
| `plan.summary.json` | Folder count, photos per folder, duplicate count, destination, and geocoding status |

Useful `plan.csv` columns include:

- `destination`: the proposed relative folder and filename. This is the main field to review.
- `grouping_basis`: why the folder was chosen, such as `source-album`, `year-location`, or `named-event`.
- `date_source`: which metadata field supplied the capture time. `missing` means no reliable embedded date was found.
- `location_source`: whether the place came from configured rules, cached geocoding, raw GPS, or no location.
- `duplicate_of`: another planned destination with the same SHA-256 hash. These files are byte-for-byte identical. Duplicates are flagged but retained.

You may remove rows to process only part of the plan. You may also edit values in `destination`, provided every path remains relative and unique. Changing descriptive columns such as `event` does not automatically rewrite the destination.

## 3. Add readable GPS names

This step is optional. Planning uses any cached location names automatically and never performs network requests by itself.

Create a [Geoapify](https://www.geoapify.com/) API key. Store it in either:

- the `GEOAPIFY_API_KEY` environment variable; or
- a file named `geoapify-key.txt` inside the state directory.

Keep the key out of source control. The program does not load `.env` files automatically.

Preview the number of lookups:

```text
python photo_organizer.py geocode --state ".photo-organizer-state"
```

Fetch and cache the place names:

```text
python photo_organizer.py geocode --state ".photo-organizer-state" --fetch
```

Only rounded coordinates and lookup options are sent. Photos, filenames, paths, and timestamps remain local. Results are cached, so rerunning the command only requests locations that are not already stored.

Regenerate the plan after geocoding:

```text
python photo_organizer.py plan --state ".photo-organizer-state" --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/plan.csv"
```

Folders use readable labels such as `Seattle_Washington_United-States`. An unresolved coordinate receives a `Location-to-review-00001` label rather than a coordinate-based folder name.

The default two-decimal cache groups coordinates into cells roughly 1.1 km high; east-west distance varies by latitude. For more precise lookups, use matching precision on both commands:

```text
python photo_organizer.py geocode --state ".photo-organizer-state" --precision 3 --fetch
python photo_organizer.py plan --state ".photo-organizer-state" --geocode-precision 3 --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/plan.csv"
```

Greater precision creates more API requests. The default safety cap is 200 uncached requests per run. Increase it explicitly with `--max-requests` when appropriate for your provider plan.

Attribution: Powered by [Geoapify](https://www.geoapify.com/), with data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).

## 4. Preview the transfer

Validate the complete reviewed plan without writing to the destination:

```text
python photo_organizer.py apply --state ".photo-organizer-state" --plan ".photo-organizer-state/plan.csv" --destination "DESTINATION_DIRECTORY"
```

Without `--approve`, this command only validates the plan and reports the source, destination, transfer mode, and photo count.

## 5. Apply the approved plan

Choose copy mode for the first real run if destination capacity permits. It keeps the source collection intact:

```text
python photo_organizer.py apply --state ".photo-organizer-state" --plan ".photo-organizer-state/plan.csv" --destination "DESTINATION_DIRECTORY" --mode copy --approve
```

Move mode removes each ordinary source photo only after its destination copy has been written and verified:

```text
python photo_organizer.py apply --state ".photo-organizer-state" --plan ".photo-organizer-state/plan.csv" --destination "DESTINATION_DIRECTORY" --mode move --approve
```

The destination supplied to `apply` must match the destination recorded when the plan was created.

For every transfer, the program verifies the source SHA-256, copies the file, flushes and verifies the destination, and records the result. In move mode it rechecks the source before deleting it. A checksum conflict or transfer failure leaves the source photo in place and never overwrites a different destination file.

Photos extracted from archives are copied from staging even in move mode. Original archives remain intact because they may contain other files. Empty source folders are not removed.

## 6. Scan the complete collection

Once the pilot looks right, use a new subdirectory inside the private state directory and omit `--max-photos`:

```text
python photo_organizer.py scan "SOURCE_DIRECTORY" --state ".photo-organizer-state/full" --extract-archives
python photo_organizer.py geocode --state ".photo-organizer-state/full"
python photo_organizer.py geocode --state ".photo-organizer-state/full" --fetch
python photo_organizer.py plan --state ".photo-organizer-state/full" --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/full/plan.csv"
```

Repeat the review and preview steps before running an approved transfer.

The first scan reads every photo for metadata and SHA-256 hashing. On a slow NAS this can take hours. A repeated scan reuses entries whose size and modification time have not changed. Add `--refresh` to force all metadata and hashes to be read again.

## How folders are selected

The default `--grouping broad` applies this priority:

1. A configured event keeps matching photos together.
2. A meaningful source folder becomes an album and stays together across dates and locations.
3. Remaining photos are grouped by year and readable location.
4. Photos without useful location metadata use `Unknown-location`; photos without dates use `Unknown-date` unless album or event context already groups them.

Example output:

```text
OrganizedPhotos/
  Albums/
    London-et-Paris/
      2018-05-14_09-24-10_London-et-Paris_7c4a8d031e92.jpg
  Events/
    2020-06-20_Our-wedding/
  2024/
    Seattle_Washington_United-States/
    Unknown-location/
  Unknown-date/
    Unknown-location/
```

Generic names such as `Archive`, `Photos from 2013`, `DCIM`, camera folders, date-only folders, and month/year folders are ignored as album context. The first meaningful parent folder becomes the album name. Review this choice because it is based on folder names rather than image content.

Matching album names from different branches are merged. Define explicit rules if identically named folders represent different collections.

To disable source-folder context and group primarily by location, add `--ignore-source-context` to `plan`.

To use smaller time-based events instead of broad albums and places, add `--grouping events`. Event mode splits groups after a six-hour gap, after 36 hours from the group start, or after a GPS change greater than 30 km. Its thresholds can be adjusted with `--gap-hours`, `--max-event-hours`, and `--distance-km`.

## Customize albums, events, and places

Copy `config.example.json` to `config.local.json`, then edit the local file. The included values are examples.

Use the configuration file when planning:

```text
python photo_organizer.py plan --state ".photo-organizer-state" --config "config.local.json" --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/plan.csv"
```

Configuration sections:

- `source_albums`: explicitly preserve or rename a source folder as an album. The first matching rule wins.
- `ignore_source_folders`: treat listed names as generic containers rather than albums.
- `events`: group an inclusive local date range under an event name. Optional `source_contains` and `place` fields narrow a rule.
- `places`: assign a preferred name to photos within a radius of a latitude and longitude. Configured places override API names.
- `place_aliases`: combine API labels into one preferred area, such as several nearby municipalities into one metro area. Copy exact labels from the geocoded CSV.

`--preserve-folders` appends the original parent hierarchy below each proposed folder. This usually creates many more folders and is best used only when preserving the old hierarchy is important.

## Archives and supported photos

Supported photos include JPEG, HEIC/HEIF, PNG, TIFF, WebP, AVIF, GIF, BMP, JXL, DNG, and common camera RAW formats.

ZIP and TAR archives—including gzip, bzip2, and xz-compressed TAR files—can be expanded with `--extract-archives`. Nested archives are supported to three levels by default. Safety limits default to 20 GiB of expanded content and 100,000 archive members:

```text
python photo_organizer.py scan "SOURCE_DIRECTORY" --state ".photo-organizer-state" --extract-archives --max-expanded-gb 50 --max-archive-members 200000 --max-archive-depth 4
```

Unsafe paths, links, special files, corrupt archives, and limit violations are reported without extracting incomplete content. Password-protected archives, RAR, 7z, and standalone compressed streams must be unpacked separately.

Videos, documents, external XMP/AAE sidecars, Google Takeout JSON, and Live Photo video companions are not organized in this photo phase. In move mode, a still photo may move while its external companion remains in the source tree. Use copy mode when those relationships need to remain intact.

## Resume and recover

- Rerun the same approved `apply` command after an interruption. Verified transfers are recorded in SQLite and matching destination files are skipped.
- Keep the reviewed CSV, its companion summary, and the state directory until the entire transfer is complete.
- Do not run two processes with the same state directory or run different state directories into the same destination simultaneously.
- A normal failed copy is cleaned up. A power loss may leave a partial destination file; the next run refuses to overwrite it. Move that partial file out of the destination and rerun.
- A forced termination may leave `organizer.lock`. Remove it only after confirming that no organizer process is running.
- Use `--exclude "DIRECTORY_TO_EXCLUDE"` more than once to omit multiple source folders. Symlinks are not followed.
- Exit code `1` means the operation completed with reported file issues. Exit code `2` means setup or plan validation failed.

Metadata warnings about proprietary camera MakerNotes do not necessarily mean a photo failed. Check `scan-report.json` and confirm that the affected file is present in `plan.csv`. Photos ExifTool cannot read are reported and omitted from the plan.

## Publish safely

Publish the source code, tests, documentation, and `config.example.json`. Do not publish runtime data: inventories and plans contain absolute paths and may contain filenames, timestamps, and GPS coordinates.

The included `.gitignore` excludes the standard state directories, SQLite inventory files, extraction staging, plans, reports, logs, local configuration, environment files, and API credentials. Custom output names should be kept outside the repository or added to your ignore rules. Ignore rules do not remove files that are already tracked or erase Git history, so inspect staged files before publishing.

## Run the automated tests

```text
python -m unittest discover -s tests -v
```

The tests cover scanning, archives, metadata selection, duplicates, broad and event grouping, geocoding, transfer approval, checksum verification, interruption recovery, and destination safety. Metadata extraction is mocked in the automated suite, so also test representative files from the actual cameras and phones in your collection.
