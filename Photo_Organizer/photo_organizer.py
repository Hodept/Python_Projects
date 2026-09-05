#!/usr/bin/env python3
"""Plan-first photo organization with optional geocoding. Python 3.10+ and ExifTool."""
import argparse
import csv
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import uuid
from itertools import product
import zipfile
from datetime import datetime, timedelta
from functools import wraps
import geocoding

PHOTOS = set('.jpg .jpeg .heic .heif .png .tif .tiff .webp .avif .dng .cr2 .cr3 .nef .nrw .arw .orf .rw2 .raf .pef .srw .bmp .gif .jxl'.split())
ARCHIVES = ('.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz')
UNSUPPORTED = ('.7z', '.rar', '.gz', '.bz2', '.xz', '.zst')
FIELDS = ['source', 'destination', 'sha256', 'size', 'captured', 'date_source', 'latitude', 'longitude', 'event', 'place', 'duplicate_of', 'original_relative', 'grouping_basis', 'location_source', 'city', 'region', 'country']


def within(path, root):
    return path == root or root in path.parents


def slug(value):
    value = re.sub(r'[^\w .-]+', '-', str(value), flags=re.UNICODE)
    value = re.sub(r'[\s_.-]+', '-', value).strip('-')
    value = value.encode('utf-8')[:60].decode('utf-8', errors='ignore').rstrip('-') or 'Unnamed'
    if value.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        value = '_' + value
    return value


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def connect(state):
    state.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(state / 'inventory.sqlite3')
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE IF NOT EXISTS photos (
        source TEXT PRIMARY KEY, relative TEXT, size INTEGER, mtime INTEGER,
        sha256 TEXT, metadata TEXT, seen TEXT);
      CREATE TABLE IF NOT EXISTS issues (run TEXT, source TEXT, problem TEXT);
      CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE IF NOT EXISTS transfers (
        source TEXT, destination TEXT, sha256 TEXT, status TEXT,
        PRIMARY KEY (source, destination, sha256));
    ''')
    geocoding.prepare_cache(db)
    return db


def with_inventory(function):
    @wraps(function)
    def wrapped(args):
        db = connect(Path(args.state).resolve())
        try:
            args._db = db
            return function(args)
        finally:
            db.close()
    return wrapped


def issue(db, run, path, error):
    db.execute('INSERT INTO issues VALUES (?,?,?)', (run, str(path), str(error)))
    print(f'Issue: {path}: {error}', file=sys.stderr)


def walk(root, excluded, on_error):
    for base, dirs, files in os.walk(root, followlinks=False, onerror=on_error):
        base = Path(base)
        dirs[:] = sorted(d for d in dirs if not (base / d).is_symlink() and
                         not any(within((base / d).resolve(), e) for e in excluded))
        for name in sorted(files):
            p = base / name
            if not p.is_symlink() and not any(within(p.resolve(), e) for e in excluded):
                yield p


def archive_kind(path):
    return path.name.lower().endswith(ARCHIVES)


def safe_member(name):
    # Backslashes, drive letters and traversal are rejected on every platform.
    p = PurePosixPath(name)
    if '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError(f'unsafe archive member: {name!r}')
    return p


class Extractor:
    def __init__(self, state, max_bytes, max_members):
        self.root = state / 'extracted'
        self.root.mkdir(exist_ok=True)
        self.remaining = max_bytes
        self.members = max_members

    def extract(self, source):
        before = source.stat()
        key = hashlib.sha256(f'{source}\0{before.st_size}\0{before.st_mtime_ns}'.encode()).hexdigest()
        final = self.root / key
        marker = final / 'complete.json'
        if marker.is_file():
            saved = json.loads(marker.read_text())
            self.remaining -= saved['bytes']
            self.members -= saved['members']
            if self.remaining < 0 or self.members < 0:
                raise ValueError('archive expansion budget exceeded (including cache)')
            return final / 'content'
        temp = self.root / (key + '.partial-' + uuid.uuid4().hex)
        content = temp / 'content'
        content.mkdir(parents=True)
        used = count = 0
        try:
            is_zip = source.name.lower().endswith('.zip')
            with (zipfile.ZipFile(source) if is_zip else tarfile.open(source, 'r:*')) as arc:
                entries = arc.infolist() if is_zip else arc
                for member in entries:
                    count += 1
                    self.members -= 1
                    if self.members < 0:
                        raise ValueError('archive member budget exceeded')
                    name = member.filename if is_zip else member.name
                    is_directory = member.is_dir() if is_zip else member.isdir()
                    if name in ('.', './') and is_directory:
                        continue
                    rel = safe_member(name)
                    target = content.joinpath(*rel.parts)
                    mode = (member.external_attr >> 16) if is_zip else 0
                    if (is_zip and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)) or (not is_zip and not (member.isfile() or member.isdir())):
                        raise ValueError(f'archive links/special files are unsupported: {name}')
                    if is_directory:
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    size = member.file_size if is_zip else member.size
                    if size > self.remaining:
                        raise ValueError('archive expansion byte budget exceeded')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = arc.open(member) if is_zip else arc.extractfile(member)
                    with stream, target.open('xb') as out:
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            self.remaining -= len(block)
                            used += len(block)
                            if self.remaining < 0:
                                raise ValueError('archive expansion byte budget exceeded')
                            out.write(block)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('archive changed during extraction')
            (temp / 'complete.json').write_text(json.dumps({'source': str(source), 'bytes': used, 'members': count}))
            temp.rename(final)
            return final / 'content'
        except BaseException:
            shutil.rmtree(temp)
            raise


def metadata_batch(paths):
    command = ['exiftool', '-json', '-n', '-charset', 'filename=UTF8',
               '-DateTimeOriginal', '-SubSecDateTimeOriginal', '-CreateDate',
               '-OffsetTimeOriginal', '-GPSLatitude', '-GPSLongitude',
               '-Make', '-Model', '-Warning', '-Error', *map(str, paths)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    try:
        values = json.loads(result.stdout)
    except ValueError as e:
        raise RuntimeError('ExifTool failed: ' + result.stderr[:1000]) from e
    return {str(Path(v['SourceFile']).resolve()): v for v in values}


@with_inventory
def scan(args):
    source, state = Path(args.source).resolve(), Path(args.state).resolve()
    if not source.is_dir():
        raise ValueError('source must be an existing directory')
    if within(source, state):
        raise ValueError('source must not be inside the state directory')
    if not shutil.which('exiftool'):
        raise ValueError('ExifTool is required. Install it from https://exiftool.org/ and ensure exiftool is on PATH.')
    db = args._db
    previous = db.execute("SELECT value FROM settings WHERE key='source'").fetchone()
    if previous and previous[0] != str(source):
        raise ValueError('use a separate state directory for each source root')
    run = uuid.uuid4().hex
    excluded = [state, *[Path(p).resolve() for p in args.exclude]]
    if any(within(source, e) for e in excluded):
        raise ValueError('an exclusion contains the entire source')
    extractor = Extractor(state, int(args.max_expanded_gb * 1024**3), args.max_archive_members) if args.extract_archives else None
    pending = []
    counts = {'photos': 0, 'cached': 0, 'other_files': 0, 'archives': 0}
    limit = getattr(args, 'max_photos', None)
    if limit is not None and limit <= 0:
        raise ValueError('--max-photos must be positive')
    limited = False

    def flush():
        if not pending:
            return
        try:
            metadata = metadata_batch([p for p, _, _ in pending])
        except Exception as e:
            for p, _, _ in pending:
                issue(db, run, p, e)
            pending.clear()
            db.commit()
            return
        for p, rel, before in pending:
            try:
                meta = metadata.get(str(p))
                if not meta or meta.get('Error'):
                    raise ValueError((meta or {}).get('Error', 'no metadata result'))
                sha = digest(p)
                after = p.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('file changed while scanning; scan again')
                db.execute('INSERT OR REPLACE INTO photos VALUES (?,?,?,?,?,?,?)',
                           (str(p), rel, after.st_size, after.st_mtime_ns, sha, json.dumps(meta), run))
                if meta.get('Warning'):
                    issue(db, run, p, meta['Warning'])
            except Exception as e:
                issue(db, run, p, e)
        pending.clear()
        db.commit()
        print(f"Scanned {counts['photos']} photos ({counts['cached']} cached)", file=sys.stderr)

    def visit(root, prefix='', depth=0):
        nonlocal limited
        for p in walk(root, excluded if depth == 0 else [], lambda e: issue(db, run, root, e)):
            if limit is not None and counts['photos'] >= limit:
                limited = True
                return
            rel = prefix + p.relative_to(root).as_posix()
            try:
                if p.suffix.lower() in PHOTOS:
                    counts['photos'] += 1
                    s = p.stat()
                    old = db.execute('SELECT size,mtime FROM photos WHERE source=?', (str(p),)).fetchone()
                    if old and tuple(old) == (s.st_size, s.st_mtime_ns) and not args.refresh:
                        db.execute('UPDATE photos SET seen=? WHERE source=?', (run, str(p)))
                        counts['cached'] += 1
                    else:
                        pending.append((p, rel, s))
                        if len(pending) >= 100:
                            flush()
                elif archive_kind(p):
                    counts['archives'] += 1
                    if extractor and depth < args.max_archive_depth:
                        visit(extractor.extract(p), rel + '!/', depth + 1)
                    else:
                        issue(db, run, p, 'archive not expanded (disabled or depth limit)')
                else:
                    counts['other_files'] += 1
                    if p.name.lower().endswith(UNSUPPORTED):
                        issue(db, run, p, 'unsupported compressed format; unpack separately and rescan')
            except Exception as e:
                issue(db, run, p, e)
    visit(source)
    flush()
    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('source', str(source)))
    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('run', run))
    db.commit()
    problems = [dict(r) for r in db.execute('SELECT source,problem FROM issues WHERE run=?', (run,))]
    (state / 'scan-report.json').write_text(json.dumps({**counts, 'partial_scan': limited, 'max_photos': limit, 'issues': problems}, indent=2))
    print(json.dumps({**counts, 'issues': len(problems)}))
    return 1 if problems else 0


def captured(meta):
    for key in ('SubSecDateTimeOriginal', 'DateTimeOriginal', 'CreateDate'):
        value = str(meta.get(key, ''))
        value = re.sub(r'^(\d{4}):(\d{2}):(\d{2})', r'\1-\2-\3', value)
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if dt.year < 1800:
                continue
            if dt.tzinfo is None and key == 'DateTimeOriginal' and meta.get('OffsetTimeOriginal'):
                dt = datetime.fromisoformat(dt.isoformat() + str(meta['OffsetTimeOriginal']))
            return dt, key
        except ValueError:
            continue
    return None, 'missing'


def coordinates(meta):
    try:
        lat, lon = float(meta['GPSLatitude']), float(meta['GPSLongitude'])
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (KeyError, TypeError, ValueError):
        pass
    return None


def km(a, b):
    x, y = map(math.radians, (a[0], b[0]))
    d = math.radians(b[1] - a[1])
    h = math.sin((y-x)/2)**2 + math.cos(x)*math.cos(y)*math.sin(d/2)**2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def config_read(path):
    cfg = json.loads(Path(path).read_text()) if path else {}
    for place in cfg.get('places', []):
        if not coordinates({'GPSLatitude': place['latitude'], 'GPSLongitude': place['longitude']}) or place.get('radius_km', 10) <= 0:
            raise ValueError('invalid place coordinates or radius')
        place['name']
    for event in cfg.get('events', []):
        start, end = datetime.fromisoformat(event['start']), datetime.fromisoformat(event['end'])
        if start.tzinfo or end.tzinfo or end < start:
            raise ValueError('event ranges must be ordered local times without offsets')
        event['name']
    return cfg


def geocoded_label(result):
    if not result or result.get('status') != 'found':
        return None
    locality = result['locality']
    if result['level'] != 'city':
        locality = result['level'].title() + '-' + locality
    parts = [locality, result.get('region'), result.get('country')]
    # Bound UTF-8 lengths while retaining region/country disambiguation.
    labels = []
    for part in parts:
        if part:
            label = slug(part).encode('utf-8')[:40].decode('utf-8', errors='ignore')
            if label not in labels:
                labels.append(label)
    return '_'.join(labels)


@with_inventory
def geocode(args):
    db = args._db
    setting = db.execute("SELECT value FROM settings WHERE key='run'").fetchone()
    if not setting:
        raise ValueError('run scan first')
    points = {}
    for row in db.execute('SELECT metadata FROM photos WHERE seen=?', (setting[0],)):
        gps = coordinates(json.loads(row[0]))
        if gps:
            points.setdefault(geocoding.cache_key(gps, args.precision, args.language), gps)
    pending = [gps for gps in points.values() if geocoding.get_cached(db, gps, args.precision, args.language) is None]
    print(f'{len(points)} distinct rounded locations; {len(points)-len(pending)} cached; {len(pending)} API requests needed.')
    if not args.fetch:
        print('Preview only. Use --fetch to send rounded coordinates to Geoapify; no photos, paths, or timestamps are sent.')
        return 0
    if len(pending) > args.max_requests:
        raise ValueError('lookup count exceeds --max-requests; increase it explicitly or use a smaller pilot')
    if pending:
        key = os.environ.get('GEOAPIFY_API_KEY', '').strip()
        key_file = Path(args.state) / 'geoapify-key.txt'
        if not key and key_file.is_file():
            key = key_file.read_text().strip()
        client = geocoding.GeoapifyClient(key, args.request_interval)
        for i, gps in enumerate(pending, 1):
            result = client.lookup(gps, args.precision, args.language)
            geocoding.save_cached(db, gps, result, args.precision, args.language)
            if i % 10 == 0 or i == len(pending):
                print(f'Geocoded {i}/{len(pending)} locations', file=sys.stderr)
    found = sum(geocoding.get_cached(db, gps, args.precision, args.language)['status'] == 'found' for gps in points.values())
    report = {'provider': 'Geoapify', 'locations': len(points), 'resolved': found, 'not_found': len(points)-found,
              'precision': args.precision, 'language': args.language, 'attribution': geocoding.ATTRIBUTION}
    (Path(args.state) / 'geocoding-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


def source_album(relative, cfg):
    parents = PurePosixPath(relative).parts[:-1]
    for rule in cfg.get('source_albums', []):
        match = rule['source_folder'].casefold().strip('/')
        if match in (part.casefold() for part in parents) or '/'.join(parents).casefold().startswith(match + '/') or '/'.join(parents).casefold() == match:
            return slug(rule.get('name', rule['source_folder'].split('/')[-1]))
    ignored = {str(v).casefold() for v in cfg.get('ignore_source_folders', [])}
    for part in parents:
        lower = part.casefold().strip()
        if lower in ignored or part.endswith('!'):
            continue
        if re.fullmatch(r'(archive|photos?|pictures?|images?|albums?|trips?|dcim|downloads?|exports?|takeout|unknown|untitled|originals?)', lower):
            continue
        if re.match(r'^(photos? from |camera\b|\d{3}apple\b)', lower):
            continue
        if re.fullmatch(r'[\d\s._-]+', lower):
            continue
        if re.fullmatch(r'(january|february|march|april|may|june|july|august|september|october|november|december)([\s_-]+\d{4})?', lower):
            continue
        return slug(part)
    return None


def broad_groups(photos, cfg, radius, use_source_context):
    """Named albums first; remaining photos share year/location folders.

    Spatial bins find fixed-radius anchors without all-pairs GPS comparisons.
    Fixed anchors avoid chaining nearby points into an ever-growing region.
    """
    bins = {}
    cell = 2 * math.sin(min(radius / 6371, math.pi) / 2)

    def bin_key(gps):
        lat, lon = map(math.radians, gps)
        xyz = (math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat))
        return tuple(math.floor(v / cell) for v in xyz)

    for p in photos:
        p['album'] = source_album(p['row']['relative'], cfg) if use_source_context else None
    # Ignore timestamp order when establishing reproducible GPS anchors.
    candidates = sorted((p for p in photos if p['gps'] and not p['event'] and not p['album'] and p['place'].startswith('GPS-')),
                        key=lambda p: (*p['gps'], p['row']['source']))
    labels = set()
    for p in candidates:
        key = bin_key(p['gps'])
        nearby = []
        for delta in product((-1, 0, 1), repeat=3):
            for anchor, label in bins.get(tuple(a+b for a,b in zip(key, delta)), []):
                distance = km(p['gps'], anchor)
                if distance <= radius:
                    nearby.append((distance, label))
        if nearby:
            label = min(nearby)[1]
        else:
            base = f'Location-to-review-{len(labels)+1:05d}'
            label, n = base, 1
            while label in labels:
                n += 1
                label = f'{base}-{n}'
            labels.add(label)
            bins.setdefault(key, []).append((p['gps'], label))
        p['place'] = label
    for p in photos:
        year = str(p['dt'].year) if p['dt'] else 'Unknown-date'
        if p['event']:
            name, date, _ = p['event']
            folder = Path('Events') / (date + '_' + name)
            basis = 'named-event'
        elif p['album']:
            name = p['album']
            folder = Path('Albums') / name
            basis = 'source-album'
        else:
            name = 'Location-to-review' if p['place'].startswith('GPS-') else p['place']
            folder = Path(year) / name
            basis = 'year-location' if p['gps'] else 'year-only'
        p['broad_folder'], p['broad_name'], p['basis'] = folder, name, basis


@with_inventory
def plan(args):
    state = Path(args.state).resolve()
    db = args._db
    setting = db.execute("SELECT value FROM settings WHERE key='run'").fetchone()
    if not setting:
        raise ValueError('run scan first')
    library_root = Path(args.destination).resolve()
    source_root = Path(db.execute("SELECT value FROM settings WHERE key='source'").fetchone()[0])
    validate_library(library_root, source_root, state)
    cfg = config_read(args.config)
    # Metadata is kept on disk; only grouping fields are held in memory.
    photos = []
    for row in db.execute('SELECT * FROM photos WHERE seen=?', (setting[0],)):
        meta = json.loads(row['metadata'])
        dt, origin = captured(meta)
        gps = coordinates(meta)
        place = 'Unknown-location'
        location_source, resolved = 'missing', None
        if gps:
            place = f'GPS-{gps[0]:.2f}_{gps[1]:.2f}'
            location_source = 'gps'
            if getattr(args, 'use_geocoding', True):
                resolved = geocoding.get_cached(db, gps, getattr(args, 'geocode_precision', 2), getattr(args, 'language', 'en'))
                label = geocoded_label(resolved)
                if label:
                    place = slug(cfg.get('place_aliases', {}).get(label)) if label in cfg.get('place_aliases', {}) else label
                    location_source = 'geocoding-cache'
            for p in cfg.get('places', []):
                if km(gps, (p['latitude'], p['longitude'])) <= p.get('radius_km', 10):
                    place = slug(p['name'])
                    location_source = 'configured-place'
                    break
        local = dt.replace(tzinfo=None) if dt else None
        event = None
        if local:
            for e in cfg.get('events', []):
                if datetime.fromisoformat(e['start']) <= local <= datetime.fromisoformat(e['end']):
                    # Optional path/GPS constraints avoid mixing simultaneous collections.
                    if e.get('source_contains') and e['source_contains'].casefold() not in row['relative'].casefold():
                        continue
                    if e.get('place') and slug(e['place']) != place:
                        continue
                    event = (slug(e['name']), e['start'][:10], slug(e.get('place', place)))
                    break
        photos.append({'row': row, 'dt': dt, 'local': local, 'origin': origin, 'gps': gps, 'place': place, 'event': event,
                       'location_source': location_source, 'resolved': resolved or {}})
    photos.sort(key=lambda p: (p['local'] or datetime.max, p['row']['source']))
    grouping = getattr(args, 'grouping', 'broad')
    if grouping == 'broad':
        broad_groups(photos, cfg, getattr(args, 'location_radius_km', 20), not getattr(args, 'ignore_source_context', False))
    previous = anchor = last_gps = None
    group = 0
    group_info = {}
    for p in photos:
        if p['event'] or not p['local']:
            continue
        dt = p['local']
        split = previous is None or dt - previous > timedelta(hours=args.gap_hours) or dt - anchor >= timedelta(hours=args.max_event_hours)
        if p['gps'] and last_gps and km(p['gps'], last_gps) > args.distance_km:
            split = True
        if split:
            group += 1
            anchor = dt
            last_gps = None
            group_info[group] = (f'Event-{group:05d}', dt.date().isoformat(), p['place'])
        if p['gps']:
            last_gps = p['gps']
            if group_info[group][2] == 'Unknown-location':
                name, date, _ = group_info[group]
                group_info[group] = (name, date, p['place'])
        p['group'] = group
        previous = dt
    outpath = Path(args.output).resolve()
    outpath.parent.mkdir(parents=True, exist_ok=True)
    duplicates, events, used = {}, {}, set()
    with outpath.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for p in photos:
            row, dt = p['row'], p['dt']
            if grouping == 'broad':
                folder, name = p['broad_folder'], p['broad_name']
                base = (dt.strftime('%Y-%m-%d_%H-%M-%S') if dt else slug(Path(row['relative']).stem)) + '_' + slug(name)
            elif dt:
                name, date, folder_place = p['event'] or group_info[p['group']]
                if folder_place.startswith('GPS-'):
                    folder_place = 'Location-to-review'
                folder = Path(date[:4]) / (date + '_' + name + '_' + folder_place)
                base = dt.strftime('%Y-%m-%d_%H-%M-%S') + '_' + name + '_' + folder_place
            else:
                name, folder_place = 'Unknown-date', p['place']
                if folder_place.startswith('GPS-'):
                    folder_place = 'Location-to-review'
                folder = Path('Unknown-date') / folder_place
                base = slug(Path(row['relative']).stem)
            if args.preserve_folders:
                parent = row['relative'].split('/')[:-1]
                folder = folder.joinpath(*(slug(v) for v in parent))
            base += '_' + row['sha256'][:12]
            ext = Path(row['source']).suffix.lower()
            destination = (folder / (base + ext)).as_posix()
            suffix = 1
            while destination.casefold() in used:
                suffix += 1
                destination = (folder / (base + f'_{suffix}' + ext)).as_posix()
            used.add(destination.casefold())
            first = duplicates.setdefault(row['sha256'], destination)
            gps = p['gps'] or ('', '')
            writer.writerow(dict(source=row['source'], destination=destination, sha256=row['sha256'], size=row['size'],
                                 captured=dt.isoformat() if dt else '', date_source=p['origin'], latitude=gps[0], longitude=gps[1],
                                 event=name, place=p['place'], duplicate_of=first if first != destination else '', original_relative=row['relative'],
                                 grouping_basis=p.get('basis', 'time-event'), location_source=p['location_source'],
                                 city=p['resolved'].get('city', ''), region=p['resolved'].get('region', ''), country=p['resolved'].get('country', '')))
            events[str(folder)] = events.get(str(folder), 0) + 1
    outpath.with_suffix('.summary.json').write_text(json.dumps({'destination': str(library_root), 'grouping': grouping, 'photos': len(photos), 'exact_duplicates': len(photos)-len(duplicates), 'folders': events,
        'geocoding': {'enabled': getattr(args, 'use_geocoding', True), 'resolved_photos': sum(p['resolved'].get('status') == 'found' for p in photos), 'attribution': geocoding.ATTRIBUTION if getattr(args, 'use_geocoding', True) else None}}, indent=2))
    print(f'Wrote {len(photos)} photo operations to {outpath}. No source photos changed.')


def validate_library(destination, source, state):
    if within(destination, source) or within(source, destination) or within(destination, state) or within(state, destination):
        raise ValueError('destination must be separate from source and state trees')


@with_inventory
def apply(args):
    state = Path(args.state).resolve()
    db = args._db
    setting = db.execute("SELECT value FROM settings WHERE key='source'").fetchone()
    if not setting:
        raise ValueError('run scan first')
    source_root = Path(setting[0])
    target_root = Path(args.destination).resolve()
    validate_library(target_root, source_root, state)
    summary = json.loads(Path(args.plan).with_suffix('.summary.json').read_text())
    if summary.get('destination') != str(target_root):
        raise ValueError('destination differs from the plan; regenerate the plan for this destination')
    with Path(args.plan).open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    destinations = set()
    sources = set()
    # Validate the entire reviewed manifest before writing any photo.
    for row in rows:
        src = Path(row['source'])
        if not src.is_absolute() or src.is_symlink() or src.resolve() != src:
            raise ValueError(f'source path is not canonical or is a symlink: {src}')
        if not (within(src, source_root) or within(src, state / 'extracted')):
            raise ValueError(f'source is outside inventory roots: {src}')
        if str(src) in sources:
            raise ValueError(f'duplicate source in plan: {src}')
        sources.add(str(src))
        known = db.execute('SELECT sha256,size FROM photos WHERE source=?', (str(src),)).fetchone()
        if not known or known['sha256'] != row['sha256'] or str(known['size']) != row['size']:
            raise ValueError(f'plan does not match inventory: {src}')
        rel = safe_member(row['destination'])
        dst = target_root.joinpath(*rel.parts)
        if not within(dst.resolve(), target_root) or dst == target_root:
            raise ValueError('destination escapes library')
        if str(dst).casefold() in destinations:
            raise ValueError('duplicate destination paths in plan')
        destinations.add(str(dst).casefold())
    print(f'{args.mode.upper()}: {len(rows)} photos from {source_root} to {target_root}')
    if not args.approve:
        print('Preview only. Review the CSV, then repeat this command with --approve to execute it.')
        return 0
    target_root.mkdir(parents=True, exist_ok=True)
    copied = resumed = moved = failed = 0
    with (state / 'apply-log.jsonl').open('a', encoding='utf-8') as log:
        for row in rows:
            src, dst = Path(row['source']), target_root / row['destination']
            try:
                if not within(dst.resolve(), target_root) or dst.is_symlink():
                    raise ValueError('destination contains unsafe symlink')
                transfer_key = (str(src), str(dst), row['sha256'])
                prior = db.execute('SELECT status FROM transfers WHERE source=? AND destination=? AND sha256=?', transfer_key).fetchone()
                if not src.exists() and not src.is_symlink() and args.mode == 'move' and prior and prior['status'] in ('verified-for-move', 'moved'):
                    if not dst.is_file() or digest(dst) != row['sha256']:
                        raise ValueError('previously moved destination is missing or changed')
                    db.execute('UPDATE transfers SET status=? WHERE source=? AND destination=? AND sha256=?', ('moved', *transfer_key))
                    db.commit()
                    resumed += 1
                    log.write(json.dumps({'source': str(src), 'destination': str(dst), 'sha256': row['sha256'], 'status': 'already-moved'}) + '\n')
                    log.flush()
                    continue
                if not src.is_file() or src.is_symlink() or src.resolve() != src or digest(src) != row['sha256']:
                    raise ValueError('source changed or disappeared since scan')
                if dst.exists():
                    if not dst.is_file() or digest(dst) != row['sha256']:
                        raise ValueError('destination exists with different content; refusing overwrite')
                    status = 'already-copied'
                    resumed += 1
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    # Exclusive creation prevents accidental replacement. A crash can
                    # leave a partial destination; a later run refuses to overwrite it.
                    created = False
                    try:
                        with src.open('rb') as inp, dst.open('xb') as out:
                            created = True
                            shutil.copyfileobj(inp, out, 1024 * 1024)
                            out.flush()
                            os.fsync(out.fileno())
                        if digest(dst) != row['sha256']:
                            raise ValueError('copy checksum mismatch')
                        shutil.copystat(src, dst)
                    except BaseException:
                        if created:
                            dst.unlink()
                        raise
                    copied += 1
                    status = 'copied'
                # Retain extraction caches and original archives: an archive can
                # also contain documents or photos not included in this plan.
                if args.mode == 'move' and within(src, source_root) and not within(src, state / 'extracted'):
                    before = src.stat()
                    if src.is_symlink() or src.resolve() != src or digest(src) != row['sha256']:
                        raise ValueError('source changed before removal; retained source and copy')
                    if dst.is_symlink() or not within(dst.resolve(), target_root) or digest(dst) != row['sha256']:
                        raise ValueError('destination changed before removal; retained source')
                    with dst.open('rb') as verified:
                        os.fsync(verified.fileno())
                    db.execute('INSERT OR REPLACE INTO transfers VALUES (?,?,?,?)', (*transfer_key, 'verified-for-move'))
                    db.commit()
                    after = src.stat()
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError('source changed before removal; retained source and copy')
                    src.unlink()
                    db.execute('UPDATE transfers SET status=? WHERE source=? AND destination=? AND sha256=?', ('moved', *transfer_key))
                    db.commit()
                    moved += 1
                    status = 'moved'
                log.write(json.dumps({'source': str(src), 'destination': str(dst), 'sha256': row['sha256'], 'status': status}) + '\n')
                log.flush()
                if (copied + resumed) % 100 == 0:
                    print(f'Applied {copied + resumed}/{len(rows)}', file=sys.stderr)
            except Exception as e:
                failed += 1
                log.write(json.dumps({'source': str(src), 'destination': str(dst), 'status': 'error', 'error': str(e)}) + '\n')
                log.flush()
                print(f'Failed: {src}: {e}', file=sys.stderr)
    print(json.dumps({'copied': copied, 'moved': moved, 'already_completed': resumed, 'failed': failed}))
    return 1 if failed else 0


def positive(value):
    v = float(value)
    if not math.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError('must be a positive finite number')
    return v


@contextmanager
def state_lock(state):
    state.mkdir(parents=True, exist_ok=True)
    lock = state / 'organizer.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(f'another run may be active: {lock}; if it crashed, remove this lock only after confirming it has stopped')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(str(os.getpid()))
        yield
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('scan', help='inventory photos and optionally expand archives into staging')
    p.add_argument('source')
    p.add_argument('--state', required=True, help='local working directory, preferably not on NAS')
    p.add_argument('--exclude', action='append', default=[])
    p.add_argument('--extract-archives', action='store_true')
    p.add_argument('--max-expanded-gb', type=positive, default=20)
    p.add_argument('--max-archive-members', type=int, default=100000)
    p.add_argument('--max-archive-depth', type=int, default=3)
    p.add_argument('--refresh', action='store_true', help='reread metadata and checksums even for unchanged file stats')
    p.add_argument('--max-photos', type=int, help='limit the scan to this many photos for a pilot; naming rules are unchanged')
    p.set_defaults(func=scan)
    p = sub.add_parser('geocode', help='preview or fetch cached city names for inventoried GPS coordinates')
    p.add_argument('--state', required=True)
    p.add_argument('--fetch', action='store_true')
    p.add_argument('--precision', type=int, choices=(2, 3, 4), default=2)
    p.add_argument('--language', default='en')
    p.add_argument('--max-requests', type=int, default=200)
    p.add_argument('--request-interval', type=positive, default=1.1)
    p.set_defaults(func=geocode)
    p = sub.add_parser('plan', help='write a reviewable CSV; no media writes')
    p.add_argument('--state', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--destination', required=True, help='new library folder to create after approval')
    p.add_argument('--config')
    p.add_argument('--use-geocoding', action=argparse.BooleanOptionalAction, default=True, help='use cached city names by default; never makes network requests')
    p.add_argument('--geocode-precision', type=int, choices=(2, 3, 4), default=2)
    p.add_argument('--language', default='en')
    p.add_argument('--grouping', choices=('broad', 'events'), default='broad', help='broad source albums and year/location folders (default), or short time events')
    p.add_argument('--location-radius-km', type=positive, default=20, help='radius around each GPS area anchor in broad mode')
    p.add_argument('--ignore-source-context', action='store_true', help='do not use source folders as album context')
    p.add_argument('--gap-hours', type=positive, default=6)
    p.add_argument('--max-event-hours', type=positive, default=36)
    p.add_argument('--distance-km', type=positive, default=30)
    p.add_argument('--preserve-folders', action='store_true')
    p.set_defaults(func=plan)
    p = sub.add_parser('apply', help='preview or approve moving a reviewed plan into the new library')
    p.add_argument('--state', required=True)
    p.add_argument('--plan', required=True)
    p.add_argument('--destination', required=True)
    p.add_argument('--mode', choices=('move', 'copy'), default='move')
    p.add_argument('--approve', action='store_true', help='approve execution of the reviewed plan; otherwise preview only')
    p.set_defaults(func=apply)
    args = parser.parse_args()
    try:
        with state_lock(Path(args.state).resolve()):
            return args.func(args) or 0
    except (ValueError, OSError, sqlite3.Error, KeyError) as e:
        print(f'Error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
