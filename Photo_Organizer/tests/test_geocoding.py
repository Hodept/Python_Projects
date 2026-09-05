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
    def test_city_labels_keep_region_country_and_drop_street(self):
        result = geocoding.normalize({'results':[{'city':'Example City','state':'Example Region','country':'Example Country','street':'Private street','housenumber':'123'}]})
        self.assertEqual(app.geocoded_label(result),'Example-City_Example-Region_Example-Country')
        self.assertNotIn('street',result)
        self.assertNotIn('housenumber',result)
        other = dict(result, country='Other Country')
        self.assertNotEqual(app.geocoded_label(result),app.geocoded_label(other))

    def test_cache_shares_rounded_points_and_separates_language_precision(self):
        with sqlite3.connect(':memory:') as db:
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
        def opener(request,timeout):
            raise HTTPError(request.full_url,429,'secret-key',{},None)
        client=geocoding.GeoapifyClient('secret-key',opener=opener)
        with self.assertRaises(ValueError) as caught:
            client.lookup((0,0))
        self.assertIn('429',str(caught.exception))
        self.assertNotIn('secret-key',str(caught.exception))
        self.assertNotIn('apiKey',str(caught.exception))

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
