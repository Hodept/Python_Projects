import argparse
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
                    max_archive_members=100, max_archive_depth=3, refresh=False)
        args.update(overrides)
        def metadata(paths):
            return {str(p): {'DateTimeOriginal': '2020:06:20 15:00:00',
                             'GPSLatitude': 49.28, 'GPSLongitude': -123.12} for p in paths}
        with patch.object(app.shutil, 'which', return_value='/mock/exiftool'), patch.object(app, 'metadata_batch', side_effect=metadata):
            app.scan(argparse.Namespace(**args))

    def plan(self, config=None):
        app.plan(argparse.Namespace(state=str(self.state), output=str(self.output),
                                   destination=str(self.dest),
                                   config=config, gap_hours=6, max_event_hours=36,
                                   distance_km=30, preserve_folders=False))
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
        rows = self.plan()
        self.assertEqual(sum(bool(r['duplicate_of']) for r in rows), 1)
        self.assertNotEqual(rows[0]['destination'], rows[1]['destination'])
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.apply(), 0)
        self.assertEqual(len(list(self.dest.rglob('*.jpg'))), 2)
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


if __name__ == '__main__':
    unittest.main()
