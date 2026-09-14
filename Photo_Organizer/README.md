# Photo Organizer

Photo Organizer uses a two-stage workflow. `prepare` inventories a photo collection and creates a reviewable organization plan. `apply` validates that plan and transfers files only when you include `--approve`.

The tool works with local folders, mounted network storage, and mapped drives on Windows, macOS, and Linux. It uses existing folder names, capture dates, and GPS metadata. During preparation, Geoapify converts new GPS coordinates into city, region, and country names so the first plan already contains readable locations.

## Before you start

Install:

- Python 3.10 or newer. The commands below use `python`; substitute `python3` or `py` if needed.
- [ExifTool](https://exiftool.org/) and confirm that `exiftool -ver` works in your terminal.
- A [Geoapify](https://www.geoapify.com/) API key for readable location names. Store it in the `GEOAPIFY_API_KEY` environment variable or create the state directory before preparation and place the key in its `geoapify-key.txt` file.
- Enough free space for the organized library. Archive extraction also needs working space on the source volume by default.

Choose three paths:

| Placeholder | Purpose |
| --- | --- |
| `SOURCE_DIRECTORY` | Existing photo collection to scan |
| `DESTINATION_DIRECTORY` | New organized library; it must be outside the source tree |
| `STATE_DIRECTORY` | Private working directory for inventory, plans, logs, and cached metadata |

## What `--state` means

`--state` is a required command-line option. The text immediately after it is a directory path that **you choose**. It is not an environment variable and the script does not replace it with a predefined location.

This guide uses `.photo-organizer-state` as an example state-directory name:

```text
--state ".photo-organizer-state"
```

In that example:

- `--state` tells the program that the next value identifies its working directory.
- `.photo-organizer-state` is the user-supplied directory path.
- The leading dot is simply part of the example name. It makes the directory hidden by default on many Unix-like systems, but it has no special meaning to Photo Organizer.
- A relative path such as `.photo-organizer-state` is created below the directory where the command is run. You may supply another relative path or a full path instead.

The program creates the state directory when preparation begins. If you want to store the Geoapify key in that directory, create it first, add `geoapify-key.txt`, and then run `prepare`. If the key is supplied through `GEOAPIFY_API_KEY`, no manual directory creation is needed.

The state directory contains working data such as:

```text
.photo-organizer-state/
  inventory.sqlite3       Local photo inventory, hashes, and metadata
  scan-report.json        Scan counts, warnings, and geocoding status
  geocoding-report.json   Location lookup results summary
  plan.csv                Proposed operation list created by prepare
  plan.duplicates.csv     Exact duplicate photos excluded from the plan
  plan.summary.json       Summary associated with the plan
  apply-log.jsonl         Transfer history after an approved run
  geoapify-key.txt        Optional API-key file supplied by the user
```

Use the **same state-directory path** for the prepare and apply stages belonging to one collection. The database connects those stages and supports resuming interrupted work. Use a different state directory for each source collection.

Keep it on a local disk when scanning network storage. Do not place it inside the source or destination collection, publish it, or delete it until the transfer has finished and been verified.

Keep API credentials and the state directory out of source control. The included `.gitignore` covers the standard names used in this guide.

Run all commands from the project directory. Paths containing spaces must be quoted.

Here is how to read the first example command:

| Command element | Meaning |
| --- | --- |
| `python` | Starts Python |
| `photo_organizer.py` | Runs this program |
| `prepare` | Scans, resolves locations, and creates the review plan |
| `"SOURCE_DIRECTORY"` | Your source-folder path |
| `--state ".photo-organizer-state"` | Uses your chosen private working directory |
| `--destination "DESTINATION_DIRECTORY"` | Records where the organized library will be created after approval |
| `--no-extract-archives` | Optional switch that disables the default archive extraction |
| `--max-photos 600` | Stops after 600 photos for a pilot |

## 1. Start with a pilot

Test the workflow on a limited number of photos before scanning the full collection:

```text
python photo_organizer.py prepare "SOURCE_DIRECTORY" --state ".photo-organizer-state" --destination "DESTINATION_DIRECTORY" --max-photos 600
```

This command:

- walks the source folder and its subfolders;
- reads photo metadata and calculates a full SHA-256 hash;
- resolves new GPS locations into readable place names and caches the results;
- expands supported archives into hidden staging on the source volume, including archives nested up to 10 levels deep;
- stops after 600 photos and records that the inventory is partial;
- creates `.photo-organizer-state/plan.csv` and its summary automatically;
- leaves every source file unchanged.

The pilot follows normal folder traversal order; it is not a random sample.
The destination is recorded in the plan summary, but the destination folder is not created during preparation.
At the end of preparation, the terminal reports the number of planned destination files, exact duplicates excluded, and proposed folders. Exact duplicates are identified by matching SHA-256 hashes. Only the first source path for each hash is included in the transfer plan; excluded source files are listed in `plan.duplicates.csv` and remain unchanged in the original collection.

## 2. Review the pilot

Open these files in the state directory:

| File | What to review |
| --- | --- |
| `scan-report.json` | Photo count, archive count, partial-scan status, and metadata warnings |
| `plan.csv` | Original path, proposed destination, date source, location, and grouping reason |
| `plan.duplicates.csv` | Exact duplicate source paths, the source copy retained in the plan, and their matching SHA-256 hash |
| `plan.summary.json` | Folder count, photos per folder, duplicate count, destination, and geocoding status |

Useful `plan.csv` columns include:

- `destination`: the proposed relative folder and filename. This is the main field to review.
- `grouping_basis`: why the folder was chosen, such as `source-album`, `year-location`, or `named-event`.
- `date_source`: which metadata field supplied the capture time. `missing` means no reliable embedded date was found.
- `location_source`: whether the place came from configured rules, cached geocoding, raw GPS, or no location.
- `duplicate_of`: retained for CSV compatibility and left blank because duplicate rows are excluded from new plans.

Minor ExifTool warnings are written to `scan-report.json` without printing one terminal line per photo. Progress messages continue normally, and the final preparation summary reports the warning count. Warnings alone do not make preparation fail. Errors that prevent a file from being inventoried are displayed immediately and listed separately in the report. Preparation still creates a plan from every photo that was inventoried successfully.

You may remove rows to process only part of the plan. You may also edit values in `destination`, provided every path remains relative and unique. Changing descriptive columns such as `event` does not automatically rewrite the destination.

## 3. Check the location results

Geolocation is part of `prepare` by default. After the local inventory is safely committed, preparation sends rounded coordinates to Geoapify and caches the returned city, region, and country. Photos, filenames, paths, and timestamps remain local.

The preparation output and `scan-report.json` show whether location lookup completed. A network, credential, or quota error does not discard the photo inventory or prevent the initial plan from being written. Fix the cause and resume only the lookup step:

```text
python photo_organizer.py geocode --state ".photo-organizer-state" --fetch --max-requests 100
```

To see how many lookups remain without making an API call, omit `--fetch`:

```text
python photo_organizer.py geocode --state ".photo-organizer-state"
```

`--max-requests 100` processes at most 100 uncached locations during that run. If more remain, the command reports `partial` and records `requests_remaining`. Run the same command again later; every successful result is committed immediately, and only uncached locations are requested. Once the preview reports zero requests needed, all available lookup work is complete.

Results are cached, so repeated preparation and lookup retries do not request locations that are already stored. Planning uses cached names automatically and never performs network requests.

Regenerate the plan after geocoding without rescanning the NAS:

```text
python photo_organizer.py plan --state ".photo-organizer-state" --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/plan.csv"
```

Folders use readable labels such as `Seattle_Washington_United-States`. An unresolved coordinate receives a `Location-to-review-00001` label rather than a coordinate-based folder name.

The default two-decimal cache groups coordinates into cells roughly 1.1 km high; east-west distance varies by latitude. To make preparation more precise:

```text
python photo_organizer.py prepare "SOURCE_DIRECTORY" --state ".photo-organizer-state" --destination "DESTINATION_DIRECTORY" --max-photos 600 --geocode-precision 3
```

Greater precision creates more API requests. Preparation processes up to 200 new requests by default and writes a usable plan with the results available at that point. Use `--max-geocode-requests` to select a different preparation batch size. The standalone `geocode` command uses `--max-requests` and can be rerun over time.

For completely offline preparation, add `--no-geocoding`. The plan will use previously cached names when available and `Location-to-review` labels for unresolved GPS areas. Add `--no-use-geocoding` if cached names should also be ignored.

Attribution: Powered by [Geoapify](https://www.geoapify.com/), with data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).

## 4. Preview the transfer

Validate the complete reviewed plan without writing to the destination:

```text
python photo_organizer.py apply --state ".photo-organizer-state"
```

Without `--approve`, this command only validates the plan and reports the source, destination, transfer mode, and photo count.

## 5. Apply the approved plan

Choose copy mode for the first real run if destination capacity permits. It keeps the source collection intact:

```text
python photo_organizer.py apply --state ".photo-organizer-state" --mode copy --approve
```

Move mode removes each ordinary source photo only after its destination copy has been written and verified:

```text
python photo_organizer.py apply --state ".photo-organizer-state" --mode move --approve
```

By default, `apply` reads `plan.csv` and its recorded destination from the state directory. Use `--plan` to select a different reviewed CSV. You may supply `--destination` as an additional safety check; it must match the destination recorded when the plan was created.

For every transfer, the program verifies the source SHA-256, copies the file, flushes and verifies the destination, and records the result. In move mode it rechecks the source before deleting it. A checksum conflict or transfer failure leaves the source photo in place and never overwrites a different destination file.

Photos extracted from archives are copied from staging even in move mode. Their staged copies and original archives remain intact because an archive may contain other files. Empty source folders are not removed.

## 6. Prepare the complete collection

Once the pilot looks right, use a new subdirectory inside the private state directory and omit `--max-photos`:

```text
python photo_organizer.py prepare "SOURCE_DIRECTORY" --state ".photo-organizer-state/full" --destination "DESTINATION_DIRECTORY"
```

Repeat the review and preview steps before running an approved transfer.

The first preparation reads every photo for metadata and SHA-256 hashing. On a slow NAS this can take hours. Repeating `prepare` with the same state directory reuses entries whose size and modification time have not changed, then replaces the review plan. Add `--refresh` to force all metadata and hashes to be read again.

## Reuse an inventory without rescanning

The normal workflow uses `prepare` and `apply`. Separate `scan` and `plan` commands remain available when you want to change grouping rules or regenerate several plans without rereading a slow NAS.

Refresh only the inventory and location cache:

```text
python photo_organizer.py scan "SOURCE_DIRECTORY" --state ".photo-organizer-state"
```

Create or replace a plan from that saved inventory:

```text
python photo_organizer.py plan --state ".photo-organizer-state" --destination "DESTINATION_DIRECTORY" --output ".photo-organizer-state/plan.csv"
```

These advanced commands produce the same inventory and plan used by the two-stage workflow. `plan` does not call Geoapify or read the photo files again.

## How folders are selected
The default `--grouping broad` applies this priority:

1. A configured event keeps matching photos together.
2. A photo without GPS but with a capture date goes into a readable calendar folder such as `2020/01-January`.
3. A geotagged photo in a meaningful source folder becomes part of that album.
4. Remaining geotagged photos are grouped by year and readable location.
5. A photo without GPS or a reliable date keeps meaningful source-folder context when available; otherwise it uses `Unknown-date/Unknown-location`.

Example output:

```text
OrganizedPhotos/
  Albums/
    London-et-Paris/
      2018-05-14_09-24-10_London-et-Paris_7c4a8d031e92.jpg
  Events/
    2020-06-20_Our-wedding/
  2024/
    01-January/
    Seattle_Washington_United-States/
  Unknown-date/
    Unknown-location/
```

Generic names such as `Archive`, `Photos from 2013`, `DCIM`, camera folders, date-only folders, and month/year folders are ignored as album context. The first meaningful parent folder becomes the album name. Review this choice because it is based on folder names rather than image content.

Matching album names from different branches are merged. Define explicit rules if identically named folders represent different collections.

To disable source-folder context and group primarily by location, add `--ignore-source-context` to `prepare` or `plan`.

To use smaller time-based events instead of broad albums and places, add `--grouping events`. Event mode splits groups after a six-hour gap, after 36 hours from the group start, or after a GPS change greater than 30 km. Its thresholds can be adjusted with `--gap-hours`, `--max-event-hours`, and `--distance-km`.

## Customize albums, events, and places

Copy `config.example.json` to `config.local.json`, then edit the local file. The included values are examples.

Use the configuration file during preparation:

```text
python photo_organizer.py prepare "SOURCE_DIRECTORY" --state ".photo-organizer-state" --destination "DESTINATION_DIRECTORY" --config "config.local.json"
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

ZIP and TAR archives—including gzip, bzip2, and xz-compressed TAR files—are expanded by default. Nested archives are supported to 10 levels. Extracted content is stored in `.photo-organizer-extracted` inside the source directory, which keeps the large staging cache on the same drive as the source. The scanner excludes this hidden working directory from ordinary traversal.

Safety limits default to 250 GiB of expanded content and 100,000 archive members:

```text
python photo_organizer.py prepare "SOURCE_DIRECTORY" --state ".photo-organizer-state" --destination "DESTINATION_DIRECTORY" --max-expanded-gb 500 --max-archive-members 200000 --max-archive-depth 10
```

Use `--no-extract-archives` when archives should be skipped. Unsafe paths, links, special files, corrupt archives, and limit violations are reported without extracting incomplete content. Password-protected archives, RAR, 7z, and standalone compressed streams must be unpacked separately.

Use `--extraction-directory "STAGING_DIRECTORY"` to put staging somewhere else. The selected directory must be separate from the state directory and cannot be the source directory itself. It should be on a drive with enough capacity for the value supplied through `--max-expanded-gb`.

Videos, documents, external XMP/AAE sidecars, Google Takeout JSON, and Live Photo video companions are not organized in this photo phase. In move mode, a still photo may move while its external companion remains in the source tree. Use copy mode when those relationships need to remain intact.

## Resume and recover

- Rerun the same approved `apply` command after an interruption. Verified transfers are recorded in SQLite and matching destination files are skipped.
- Keep the reviewed CSV, its companion summary, and the state directory until the entire transfer is complete.
- Do not run two processes with the same state directory or run different state directories into the same destination simultaneously.
- A normal failed copy is cleaned up. A power loss may leave a partial destination file; the next run refuses to overwrite it. Move that partial file out of the destination and rerun.
- A forced termination may leave `organizer.lock`. Remove it only after confirming that no organizer process is running.
- Use `--exclude "DIRECTORY_TO_EXCLUDE"` more than once to omit multiple source folders. Symlinks are not followed.
- Exit code `1` means one or more photos could not be inventoried or transferred. Metadata warnings alone still return success. Exit code `2` means setup, geocoding, or plan validation was incomplete. When preparation reaches the planning step, it writes a plan for the successfully inventoried photos even if the final exit code reports an issue.

Metadata warnings about proprietary camera MakerNotes do not necessarily mean a photo failed. Check `scan-report.json` and confirm that the affected file is present in `plan.csv`. Photos ExifTool cannot read are reported and omitted from the plan.

## Publish safely

Publish the source code, tests, documentation, and `config.example.json`. Do not publish runtime data: inventories and plans contain absolute paths and may contain filenames, timestamps, and GPS coordinates.

The included `.gitignore` excludes the standard state directories, SQLite inventory files, extraction staging, plans, reports, logs, local configuration, environment files, and API credentials. Custom output names should be kept outside the repository or added to your ignore rules. Ignore rules do not remove files that are already tracked or erase Git history, so inspect staged files before publishing.

## Run the automated tests

```text
python -m unittest discover -s tests -v
```

The tests cover scanning, archives, metadata selection, duplicates, broad and event grouping, geocoding, transfer approval, checksum verification, interruption recovery, and destination safety. Metadata extraction is mocked in the automated suite, so also test representative files from the actual cameras and phones in your collection.
