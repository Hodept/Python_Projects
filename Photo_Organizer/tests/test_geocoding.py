import argparse
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geocoding
import photo_organizer as app


class GeocodingTests(unittest.TestCase):
    def scan_args(self, source, state, geocoding_enabled=True):
        return argparse.Namespace(source=str(source), state=str(state), exclude=[], extract_archives=False,
                                  max_expanded_gb=1, max_archive_members=100, max_archive_depth=1,
                                  refresh=False, max_photos=None, geocoding=geocoding_enabled,
                                  geocode_precision=2, language='en', max_geocode_requests=200,
                                  geocode_request_interval=1.1)

    def test_city_labels_keep_region_country_and_drop_street(self):
        result = geocoding.normalize({'results':[{'city':'Example City','state':'Example Region','country':'Example Country','street':'Private street','housenumber':'123'}]})
        self.assertEqual(app.geocoded_label(result),'Example-City_Example-Region_Example-Country')
        self.assertNotIn('street',result)
        self.assertNotIn('housenumber',result)
        other = dict(result, country='Other Country')
        self.assertNotEqual(app.geocoded_label(result),app.geocoded_label(other))

    def test_cache_shares_rounded_points_and_separates_language_precision(self):
        with contextlib.closing(sqlite3.connect(':memory:')) as db:
            geocoding.prepare_cache(db)
            value = geocoding.normalize({'results':[{'city':'Example City'}]})
            geocoding.save_cached(db,(49.2801,-123.1201),value)
            self.assertEqual(geocoding.get_cached(db,(49.2802,-123.1202)),value)
            self.assertIsNone(geocoding.get_cached(db,(49.2802,-123.1202),language='fr'))
            self.assertIsNone(geocoding.get_cached(db,(49.2802,-123.1202),precision=3))
            geocoding.save_cached(db,(0,0),geocoding.normalize({'results':[]}))
            self.assertEqual(geocoding.get_cached(db,(0,0))['status'],'not-found')

    def test_requests_round_coordinates_and_throttle(self):
        requests, sleeps = [], []
        def opener(request, timeout):
            requests.append(request)
            return io.StringIO(json.dumps({'results':[{'city':'Example'}]}))
        client = geocoding.GeoapifyClient('test-key',opener=opener,sleeper=sleeps.append,clock=lambda:10)
        client.lookup((49.281234,-123.123456))
        client.lookup((50.1,-123.1))
        query = parse_qs(urlsplit(requests[0].full_url).query)
        self.assertEqual(query['lat'],['49.28'])
        self.assertEqual(query['type'],['city'])
        self.assertEqual(set(query),{'lat','lon','type','format','limit','lang','apiKey'})
        self.assertEqual(sleeps,[1.1])

    def test_http_error_does_not_leak_credentials_and_does_not_retry(self):
        error = HTTPError('https://example.invalid',429,'secret-key',{},None)
        def opener(request,timeout):
            raise error
        client=geocoding.GeoapifyClient('secret-key',opener=opener)
        try:
            with self.assertRaises(ValueError) as caught:
                client.lookup((0,0))
            self.assertIn('429',str(caught.exception))
            self.assertNotIn('secret-key',str(caught.exception))
            self.assertNotIn('apiKey',str(caught.exception))
        finally:
            error.close()

    def test_region_fallback_is_not_mislabeled_city(self):
        result=geocoding.normalize({'results':[{'county':'Example County','country':'Example Country'}]})
        self.assertTrue(app.geocoded_label(result).startswith('County-'))
        self.assertEqual(result['city'],'')
        self.assertIsNone(app.geocoded_label(geocoding.normalize({'results':[]})))

    def test_geocode_preview_and_resume_do_not_call_network(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)
            db=app.connect(state)
            db.execute('INSERT INTO settings VALUES (?,?)',('run','sample'))
            meta=json.dumps({'GPSLatitude':49.28,'GPSLongitude':-123.12})
            db.execute('INSERT INTO photos VALUES (?,?,?,?,?,?,?)',('sample','sample',1,1,'hash',meta,'sample'))
            db.commit()
            args=argparse.Namespace(state=temp,precision=2,language='en',fetch=False,max_requests=200,request_interval=1.1)
            with patch.object(geocoding,'GeoapifyClient') as client:
                app.geocode(args)
                client.assert_not_called()
                geocoding.save_cached(db,(49.28,-123.12),geocoding.normalize({'results':[{'city':'Example'}]}))
                args.fetch=True
                app.geocode(args)
                client.assert_not_called()
            db.close()

    def test_scan_geocodes_by_default_and_records_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, state = root/'source', root/'state'
            source.mkdir()
            photo = source/'photo.jpg'
            photo.write_bytes(b'photo')
            metadata = {str(photo.resolve()): {'DateTimeOriginal':'2020:01:01 10:00:00',
                                               'GPSLatitude':49.28, 'GPSLongitude':-123.12}}
            with patch.object(app.shutil, 'which', return_value='/mock/exiftool'), \
                 patch.object(app, 'metadata_batch', return_value=metadata), \
                 patch.object(geocoding, 'GeoapifyClient') as client:
                client.return_value.lookup.return_value = geocoding.normalize({'results':[{'city':'Example City'}]})
                self.assertEqual(app.scan(self.scan_args(source, state)), 0)
                client.return_value.lookup.assert_called_once()
            report = json.loads((state/'scan-report.json').read_text())
            self.assertEqual(report['geocoding']['status'], 'complete')
            self.assertEqual(report['geocoding']['resolved'], 1)

    def test_scan_keeps_inventory_when_default_geocoding_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, state = root/'source', root/'state'
            source.mkdir()
            photo = source/'photo.jpg'
            photo.write_bytes(b'photo')
            metadata = {str(photo.resolve()): {'GPSLatitude':49.28, 'GPSLongitude':-123.12}}
            with patch.object(app.shutil, 'which', return_value='/mock/exiftool'), \
                 patch.object(app, 'metadata_batch', return_value=metadata), \
                 patch.object(geocoding, 'GeoapifyClient') as client:
                client.return_value.lookup.side_effect = ValueError('simulated API failure')
                self.assertEqual(app.scan(self.scan_args(source, state)), 2)
            report = json.loads((state/'scan-report.json').read_text())
            self.assertEqual(report['photos'], 1)
            self.assertEqual(report['geocoding']['status'], 'error')
            with contextlib.closing(sqlite3.connect(state/'inventory.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM photos').fetchone()[0], 1)
