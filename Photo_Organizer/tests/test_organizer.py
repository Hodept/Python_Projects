import argparse
import contextlib
import csv
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import photo_organizer as app


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.state = self.root / 'state'
        self.state.mkdir()
        self.output = self.root / 'plan.csv'
        self.dest = self.root / 'library'

    def scan(self, **overrides):
        args = dict(source=str(self.source), state=str(self.state), exclude=[],
                    extract_archives=True, max_expanded_gb=1,
                    max_archive_members=100, max_archive_depth=3, refresh=False,
                    geocoding=False)
        args.update(overrides)
        def metadata(paths):
            return {str(p): {'DateTimeOriginal': '2020:06:20 15:00:00',
                             'GPSLatitude': 49.28, 'GPSLongitude': -123.12} for p in paths}
        with patch.object(app.shutil, 'which', return_value='/mock/exiftool'), patch.object(app, 'metadata_batch', side_effect=metadata):
            app.scan(argparse.Namespace(**args))

    def plan(self, config=None, grouping='events', **options):
        app.plan(argparse.Namespace(state=str(self.state), output=str(self.output),
                                   destination=str(self.dest),
                                   config=config, gap_hours=6, max_event_hours=36,
                                   distance_km=30, preserve_folders=False, grouping=grouping, **options))
        with self.output.open() as f:
            return list(csv.DictReader(f))

    def apply(self, mode='copy', approve=True):
        return app.apply(argparse.Namespace(state=str(self.state), plan=str(self.output), destination=str(self.dest), mode=mode, approve=approve))

    def test_nested_archives_and_incremental_scan(self):
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, 'w') as z:
            z.writestr('nested.jpg', b'photo-two')
        with zipfile.ZipFile(self.source / 'photos.zip', 'w') as z:
            z.writestr('album/one.jpg', b'photo-one')
            z.writestr('nested.zip', nested.getvalue())
            z.writestr('note.txt', b'keep other files staged')
        self.scan()
        rows = self.plan()
        self.assertEqual(len(rows), 2)
        self.assertTrue(any('!/' in r['original_relative'] for r in rows))
        self.scan()
        report = json.loads((self.state / 'scan-report.json').read_text())
        self.assertEqual(report['cached'], 2)
        self.assertEqual(self.apply(), 0)
        self.assertEqual(len(list(self.dest.rglob('*.jpg'))), 2)
        self.assertTrue((self.source / 'photos.zip').exists())

    def test_tar_root_entry_and_symlink_rejection(self):
        tar = self.source / 'ok.tar.gz'
        with tarfile.open(tar, 'w:gz') as arc:
            d = tarfile.TarInfo('./')
            d.type = tarfile.DIRTYPE
            arc.addfile(d)
            p = tarfile.TarInfo('./photo.jpg')
            p.size = 3
            arc.addfile(p, io.BytesIO(b'abc'))
        extract = app.Extractor(self.state, 10000, 100)
        self.assertEqual((extract.extract(tar) / 'photo.jpg').read_bytes(), b'abc')
        bad = self.source / 'bad.tar'
        with tarfile.open(bad, 'w') as arc:
            p = tarfile.TarInfo('link')
            p.type = tarfile.SYMTYPE
            p.linkname = '/tmp'
            arc.addfile(p)
        with self.assertRaises(ValueError):
            extract.extract(bad)

    def test_archive_traversal_and_expansion_limits(self):
        for index, name in enumerate(('../escape.jpg', '/absolute.jpg', 'C:/evil.jpg', '..\\evil.jpg')):
            archive = self.source / f'bad{index}.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr(name, b'photo')
            with self.assertRaises(ValueError):
                app.Extractor(self.state, 1000, 10).extract(archive)
        archive = self.source / 'large.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('large.jpg', b'0' * 10000)
        with self.assertRaises(ValueError):
            app.Extractor(self.state, 100, 10).extract(archive)
        self.assertFalse(list((self.state / 'extracted').glob('*.partial-*')))

    def test_apply_resume_duplicates_and_changed_source(self):
        (self.source / 'a.jpg').write_bytes(b'same-content')
        (self.source / 'b.jpg').write_bytes(b'same-content')
        self.scan()
        console = io.StringIO()
        with contextlib.redirect_stdout(console):
            rows = self.plan()
        self.assertIn('Plan summary: 1 destination file, 1 exact duplicate excluded', console.getvalue())
        self.assertEqual(len(rows), 1)
        with self.output.with_name('plan.duplicates.csv').open() as handle:
            duplicate_rows = list(csv.DictReader(handle))
        self.assertEqual(len(duplicate_rows), 1)
        self.assertEqual(duplicate_rows[0]['kept_source'], str(self.source / 'a.jpg'))
        self.assertEqual(duplicate_rows[0]['source'], str(self.source / 'b.jpg'))
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.apply(), 0)
        self.assertEqual(len(list(self.dest.rglob('*.jpg'))), 1)
        (self.source / 'a.jpg').write_bytes(b'changed')
        self.assertEqual(self.apply(), 1)
        self.assertEqual((self.dest / rows[0]['destination']).read_bytes(), b'same-content')

    def test_refuse_existing_different_file(self):
        (self.source / 'a.jpg').write_bytes(b'original')
        self.scan()
        row = self.plan()[0]
        target = self.dest / row['destination']
        target.parent.mkdir(parents=True)
        target.write_bytes(b'unrelated')
        self.assertEqual(self.apply(), 1)
        self.assertEqual(target.read_bytes(), b'unrelated')

    def test_apply_rejects_legacy_plan_containing_duplicate_content(self):
        first, second = self.source / 'a.jpg', self.source / 'b.jpg'
        first.write_bytes(b'same-content')
        second.write_bytes(b'same-content')
        self.scan()
        row = self.plan()[0]
        legacy_duplicate = dict(row)
        legacy_duplicate['source'] = str(second)
        legacy_duplicate['original_relative'] = second.name
        legacy_duplicate['destination'] = 'legacy/second.jpg'
        with self.output.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=app.FIELDS)
            writer.writeheader()
            writer.writerows((row, legacy_duplicate))
        with self.assertRaisesRegex(ValueError, 'exact duplicate content'):
            self.apply()
        self.assertFalse(self.dest.exists())

    def test_manifest_traversal_rejected_before_copy(self):
        (self.source / 'a.jpg').write_bytes(b'original')
        self.scan()
        rows = self.plan()
        rows[0]['destination'] = '../escape.jpg'
        with self.output.open('w') as f:
            w = csv.DictWriter(f, fieldnames=app.FIELDS)
            w.writeheader()
            w.writerows(rows)
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse((self.root / 'escape.jpg').exists())

    def test_missing_date_and_explicit_event(self):
        (self.source / 'a.jpg').write_bytes(b'a')
        (self.source / 'b.jpg').write_bytes(b'b')
        self.scan()
        db = app.connect(self.state)
        db.execute('UPDATE photos SET metadata=? WHERE source=?', ('{}', str(self.source / 'b.jpg')))
        db.commit()
        db.close()
        config = self.root / 'config.json'
        config.write_text(json.dumps({'places': [{'name': 'Vancouver', 'latitude': 49.28, 'longitude': -123.12}],
                                      'events': [{'name': 'Our wedding', 'start': '2020-06-20T00:00:00', 'end': '2020-06-21T23:59:59'}]}))
        rows = self.plan(str(config))
        self.assertIn('Our-wedding_Vancouver', rows[0]['destination'])
        self.assertTrue(rows[1]['destination'].startswith('Unknown-date/'))
        self.assertEqual(rows[1]['date_source'], 'missing')

    def test_time_and_distance_split_events(self):
        for name in 'abcd':
            (self.source / f'{name}.jpg').write_bytes(name.encode())
        self.scan()
        db = app.connect(self.state)
        for name, time, gps in [('a', '01:00:00', (49, -123)), ('b', '02:00:00', (49, -123)),
                                ('c', '03:00:00', (40, -74)), ('d', '20:00:00', (40, -74))]:
            meta = {'DateTimeOriginal': '2020:01:01 ' + time, 'GPSLatitude': gps[0], 'GPSLongitude': gps[1]}
            db.execute('UPDATE photos SET metadata=? WHERE source=?', (json.dumps(meta), str(self.source / f'{name}.jpg')))
        db.commit()
        db.close()
        rows = self.plan()
        self.assertEqual(rows[0]['event'], rows[1]['event'])
        self.assertNotEqual(rows[1]['event'], rows[2]['event'])
        self.assertNotEqual(rows[2]['event'], rows[3]['event'])

    def test_capture_offsets_and_invalid_dates(self):
        dt, origin = app.captured({'DateTimeOriginal': '2020:06:20 15:00:00', 'OffsetTimeOriginal': '-07:00'})
        self.assertEqual(dt.isoformat(), '2020-06-20T15:00:00-07:00')
        self.assertEqual(app.captured({'DateTimeOriginal': '0000:00:00 00:00:00'}), (None, 'missing'))
        self.assertIsNone(app.coordinates({'GPSLatitude': float('nan'), 'GPSLongitude': 0}))

    def test_state_lock(self):
        with app.state_lock(self.state):
            with self.assertRaises(ValueError):
                with app.state_lock(self.state):
                    pass
        self.assertFalse((self.state / 'organizer.lock').exists())

    def test_pilot_scan_limit_saves_reviewable_inventory(self):
        for name in 'abc':
            (self.source / f'{name}.jpg').write_bytes(name.encode())
        self.scan(max_photos=2)
        self.assertEqual(len(self.plan()), 2)
        report = json.loads((self.state / 'scan-report.json').read_text())
        self.assertTrue(report['partial_scan'])
        self.assertEqual(report['max_photos'], 2)

    def test_prepare_creates_default_plan_and_apply_uses_saved_settings(self):
        photo = self.source / 'a.jpg'
        photo.write_bytes(b'original')
        args = argparse.Namespace(
            source=str(self.source), state=str(self.state), destination=str(self.dest), output=None,
            exclude=[], extract_archives=False, max_expanded_gb=1, max_archive_members=100,
            max_archive_depth=1, refresh=False, max_photos=None, geocoding=False,
            geocode_precision=2, language='en', max_geocode_requests=10,
            geocode_request_interval=0.01, config=None, use_geocoding=True,
            grouping='broad', location_radius_km=20, ignore_source_context=False,
            gap_hours=6, max_event_hours=36, distance_km=30, preserve_folders=False)
        metadata = {str(photo): {'DateTimeOriginal': '2020:01:01 10:00:00'}}
        with patch.object(app.shutil, 'which', return_value='/mock/exiftool'), \
             patch.object(app, 'metadata_batch', return_value=metadata):
            self.assertEqual(app.prepare(args), 0)
        plan_path = self.state / 'plan.csv'
        self.assertTrue(plan_path.exists())
        self.assertTrue(plan_path.with_suffix('.summary.json').exists())
        apply_args = argparse.Namespace(state=str(self.state), plan=None, destination=None,
                                        mode='copy', approve=False)
        self.assertEqual(app.apply(apply_args), 0)
        self.assertFalse(self.dest.exists())
        apply_args.approve = True
        self.assertEqual(app.apply(apply_args), 0)
        self.assertEqual(len(list(self.dest.rglob('*.jpg'))), 1)

    def test_prepare_writes_plan_for_successful_photos_when_another_photo_fails(self):
        good, bad = self.source / 'good.jpg', self.source / 'bad.jpg'
        good.write_bytes(b'good')
        bad.write_bytes(b'bad')
        args = argparse.Namespace(
            source=str(self.source), state=str(self.state), destination=str(self.dest), output=None,
            exclude=[], extract_archives=False, max_expanded_gb=1, max_archive_members=100,
            max_archive_depth=1, refresh=False, max_photos=None, geocoding=False,
            geocode_precision=2, language='en', max_geocode_requests=10,
            geocode_request_interval=0.01, config=None, use_geocoding=True,
            grouping='broad', location_radius_km=20, ignore_source_context=False,
            gap_hours=6, max_event_hours=36, distance_km=30, preserve_folders=False)
        metadata = {
            str(good): {'DateTimeOriginal': '2020:01:01 10:00:00'},
            str(bad): {'Error': 'unreadable photo'},
        }
        with patch.object(app.shutil, 'which', return_value='/mock/exiftool'), \
             patch.object(app, 'metadata_batch', return_value=metadata):
            self.assertEqual(app.prepare(args), 1)
        with (self.state / 'plan.csv').open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row['source'] for row in rows], [str(good)])

    def test_minor_metadata_warning_is_recorded_without_console_noise(self):
        photo = self.source / 'a.jpg'
        photo.write_bytes(b'a')
        metadata = {str(photo): {'DateTimeOriginal': '2020:01:01 10:00:00',
                                 'Warning': '[minor] Example camera metadata warning'}}
        args = argparse.Namespace(source=str(self.source), state=str(self.state), exclude=[],
                                  extract_archives=False, max_expanded_gb=1,
                                  max_archive_members=100, max_archive_depth=1,
                                  refresh=False, max_photos=None, geocoding=False)
        console = io.StringIO()
        with patch.object(app.shutil, 'which', return_value='/mock/exiftool'), \
             patch.object(app, 'metadata_batch', return_value=metadata), \
             contextlib.redirect_stderr(console):
            self.assertEqual(app.scan(args), 0)
        self.assertNotIn('Example camera metadata warning', console.getvalue())
        report = json.loads((self.state / 'scan-report.json').read_text())
        self.assertEqual(len(report['warnings']), 1)
        self.assertEqual(report['errors'], [])

    def test_existing_issue_table_is_migrated_with_severity(self):
        database = self.state / 'inventory.sqlite3'
        with contextlib.closing(sqlite3.connect(database)) as db:
            db.execute('CREATE TABLE issues (run TEXT, source TEXT, problem TEXT)')
            db.execute("INSERT INTO issues VALUES ('old','photo.jpg','old issue')")
            db.commit()
        db = app.connect(self.state)
        try:
            columns = {row[1] for row in db.execute('PRAGMA table_info(issues)')}
            self.assertIn('severity', columns)
            self.assertEqual(db.execute('SELECT severity FROM issues').fetchone()[0], 'error')
        finally:
            db.close()

    def test_failed_copy_cleanup_and_resume(self):
        (self.source / 'a.jpg').write_bytes(b'original')
        self.scan()
        row = self.plan()[0]
        def interrupted(inp, out, length):
            out.write(b'partial')
            raise OSError('simulated disconnection')
        with patch.object(app.shutil, 'copyfileobj', side_effect=interrupted):
            self.assertEqual(self.apply(), 1)
        self.assertFalse((self.dest / row['destination']).exists())
        self.assertEqual((self.source / 'a.jpg').read_bytes(), b'original')
        self.assertEqual(self.apply(), 0)

    def test_unicode_label_byte_length(self):
        self.assertLessEqual(len(app.slug('\u5bb6' * 100).encode('utf-8')), 60)

    def test_move_requires_approval_then_resumes(self):
        source = self.source / 'a.jpg'
        source.write_bytes(b'original')
        self.scan()
        row = self.plan()[0]
        self.assertEqual(self.apply(mode='move', approve=False), 0)
        self.assertTrue(source.exists())
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.apply(mode='move'), 0)
        self.assertFalse(source.exists())
        self.assertEqual((self.dest / row['destination']).read_bytes(), b'original')
        self.assertEqual(self.apply(mode='move'), 0)

    def test_move_preserves_source_on_copy_failure(self):
        source = self.source / 'a.jpg'
        source.write_bytes(b'original')
        self.scan()
        self.plan()
        with patch.object(app.shutil, 'copyfileobj', side_effect=OSError('disconnected')):
            self.assertEqual(self.apply(mode='move'), 1)
        self.assertEqual(source.read_bytes(), b'original')

    def test_move_retains_archives_and_cache(self):
        archive = self.source / 'album.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('a.jpg', b'original')
        self.scan()
        row = self.plan()[0]
        self.assertEqual(self.apply(mode='move'), 0)
        self.assertTrue(archive.exists())
        self.assertTrue(Path(row['source']).exists())
        self.assertEqual((self.dest / row['destination']).read_bytes(), b'original')

    def test_move_resume_after_removal_before_final_journal_update(self):
        source = self.source / 'a.jpg'
        source.write_bytes(b'original')
        self.scan()
        row = self.plan()[0]
        self.apply()
        db = app.connect(self.state)
        db.execute('INSERT INTO transfers VALUES (?,?,?,?)',
                   (str(source), str(self.dest / row['destination']), row['sha256'], 'verified-for-move'))
        db.commit()
        db.close()
        source.unlink()
        self.assertEqual(self.apply(mode='move'), 0)

    def test_destination_must_match_reviewed_plan(self):
        (self.source / 'a.jpg').write_bytes(b'original')
        self.scan()
        self.plan()
        self.dest = self.root / 'different-library'
        with self.assertRaises(ValueError):
            self.apply(mode='move')
        self.assertTrue((self.source / 'a.jpg').exists())

    def test_broad_album_spans_dates_cities_and_missing_metadata(self):
        album = self.source / 'Two-city trip'
        album.mkdir()
        for name in 'abc':
            (album / f'{name}.jpg').write_bytes(name.encode())
        self.scan()
        with app.connect(self.state) as db:
            db.execute('UPDATE photos SET metadata=? WHERE source=?', (json.dumps({'DateTimeOriginal': '2021:01:02 10:00:00', 'GPSLatitude': 40.7, 'GPSLongitude': -74}), str(album / 'b.jpg')))
            db.execute('UPDATE photos SET metadata=? WHERE source=?', ('{}', str(album / 'c.jpg')))
        db.close()
        rows = self.plan(grouping='broad')
        self.assertEqual({str(Path(r['destination']).parent) for r in rows}, {'Albums/Two-city-trip'})
        self.assertEqual({r['grouping_basis'] for r in rows}, {'source-album'})

    def test_photos_without_gps_use_calendar_month_and_missing_dates_keep_context(self):
        album = self.source / 'Family archive'
        album.mkdir()
        for name in 'abc':
            (album / f'{name}.jpg').write_bytes(name.encode())
        self.scan()
        with contextlib.closing(app.connect(self.state)) as db:
            db.execute('UPDATE photos SET metadata=? WHERE source=?',
                       (json.dumps({'DateTimeOriginal': '2020:01:02 10:00:00'}), str(album / 'a.jpg')))
            db.execute('UPDATE photos SET metadata=? WHERE source=?',
                       (json.dumps({'DateTimeOriginal': '2020:02:03 10:00:00'}), str(album / 'b.jpg')))
            db.execute('UPDATE photos SET metadata=? WHERE source=?', ('{}', str(album / 'c.jpg')))
            db.commit()
        rows = self.plan(grouping='broad')
        by_name = {Path(row['source']).name: row for row in rows}
        self.assertEqual(Path(by_name['a.jpg']['destination']).parent, Path('2020/01-January'))
        self.assertEqual(Path(by_name['b.jpg']['destination']).parent, Path('2020/02-February'))
        self.assertEqual(by_name['a.jpg']['grouping_basis'], 'calendar-month')
        self.assertEqual(Path(by_name['c.jpg']['destination']).parent, Path('Albums/Family-archive'))

    def test_broad_nearby_gps_merges_despite_time_gaps(self):
        archive = self.source / 'Archive'
        archive.mkdir()
        for name in 'abc':
            (archive / f'{name}.jpg').write_bytes(name.encode())
        self.scan()
        with app.connect(self.state) as db:
            for name, date, lat in [('a', '2020:01:01', 49.0), ('b', '2020:08:01', 49.08), ('c', '2020:08:02', 51.0)]:
                meta = {'DateTimeOriginal': date+' 10:00:00', 'GPSLatitude': lat, 'GPSLongitude': -123}
                db.execute('UPDATE photos SET metadata=? WHERE source=?', (json.dumps(meta), str(archive / f'{name}.jpg')))
        db.close()
        rows = self.plan(grouping='broad')
        self.assertEqual(Path(rows[0]['destination']).parent, Path(rows[1]['destination']).parent)
        self.assertNotEqual(Path(rows[1]['destination']).parent, Path(rows[2]['destination']).parent)
        self.assertTrue(all('Location-to-review-' in r['destination'] for r in rows))
        self.assertTrue(all('GPS-' not in r['destination'] and 'Area-' not in r['destination'] for r in rows))

    def test_source_context_rules(self):
        self.assertIsNone(app.source_album('Archive/Photos from 2013/a.jpg', {}))
        self.assertEqual(app.source_album('Trips/Two-city trip/2013/a.jpg', {}), 'Two-city-trip')
        self.assertIsNone(app.source_album('Camera, by someone/a.jpg', {}))
        self.assertIsNone(app.source_album('Unsorted/a.jpg', {'ignore_source_folders':['Unsorted']}))
        self.assertEqual(app.source_album('Archive/a.jpg', {'source_albums':[{'source_folder':'Archive','name':'Family history'}]}), 'Family-history')

    def test_broad_fixed_anchor_does_not_chain_and_handles_dateline(self):
        def photo(lat, lon, name):
            return {'gps': (lat,lon), 'place': 'GPS-test', 'event': None, 'dt': None, 'row': {'relative': name+'.jpg', 'source': name}}
        photos = [photo(0, 0, 'a'),photo(0, .15, 'b'),photo(0, .30, 'c')]
        app.broad_groups(photos, {}, 20, False)
        self.assertEqual(photos[0]['place'],photos[1]['place'])
        self.assertNotEqual(photos[1]['place'],photos[2]['place'])
        photos = [photo(0, 179.95, 'a'),photo(0, -179.95, 'b')]
        app.broad_groups(photos, {}, 20, False)
        self.assertEqual(photos[0]['place'],photos[1]['place'])

    def test_cached_cities_group_and_source_album_stays_intact(self):
        import geocoding
        album = self.source / 'Two-city trip'
        album.mkdir()
        for path, data in [(self.source/'a.jpg',b'a'),(self.source/'b.jpg',b'b'),(album/'c.jpg',b'c')]:
            path.write_bytes(data)
        self.scan()
        db=app.connect(self.state)
        geocoding.save_cached(db,(49.28,-123.12),geocoding.normalize({'results':[{'city':'Example City','state':'Example Region','country':'Example Country'}]}))
        db.close()
        rows=self.plan(grouping='broad')
        root_rows=[r for r in rows if r['grouping_basis']!='source-album']
        self.assertEqual(len({str(Path(r['destination']).parent) for r in root_rows}),1)
        self.assertTrue(all(r['city']=='Example City' for r in rows))
        self.assertTrue(all('Example-City' in r['destination'] for r in root_rows))
        self.assertTrue(any(r['destination'].startswith('Albums/Two-city-trip/') for r in rows))


if __name__ == '__main__':
    unittest.main()
