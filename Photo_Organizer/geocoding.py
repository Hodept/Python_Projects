"""Optional reverse geocoding with a persistent, credential-free result cache."""
import json
import math
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROVIDER = 'geoapify'
ENDPOINT = 'https://api.geoapify.com/v1/geocode/reverse'
ATTRIBUTION = 'Powered by Geoapify (https://www.geoapify.com/); © OpenStreetMap contributors (https://www.openstreetmap.org/copyright)'


def cache_key(gps, precision=2, language='en'):
    if precision not in (2, 3, 4):
        raise ValueError('coordinate precision must be 2, 3, or 4 decimal places')
    rounded = tuple(round(v, precision) + 0.0 for v in gps)
    return f'{PROVIDER}:city:v1:{language}:{precision}:{rounded[0]:.{precision}f},{rounded[1]:.{precision}f}'


def prepare_cache(db):
    db.execute('CREATE TABLE IF NOT EXISTS geocodes (cache_key TEXT PRIMARY KEY, result TEXT, fetched_at TEXT)')


def get_cached(db, gps, precision=2, language='en'):
    row = db.execute('SELECT result FROM geocodes WHERE cache_key=?', (cache_key(gps, precision, language),)).fetchone()
    return json.loads(row[0]) if row else None


def normalize(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
        raise ValueError('unexpected geocoding response format')
    if not payload['results']:
        return {'status': 'not-found', 'attribution': ATTRIBUTION}
    result = payload['results'][0]
    if not isinstance(result, dict):
        raise ValueError('unexpected geocoding result')
    def field(name):
        return str(result.get(name) or '').strip()
    city = field('city') or field('town') or field('village') or field('municipality')
    county, region, country = field('county'), field('state'), field('country')
    locality = city or county or region or country
    level = 'city' if city else 'county' if county else 'region' if region else 'country'
    return {'status': 'found' if locality else 'not-found', 'locality': locality, 'level': level,
            'city': city, 'region': region, 'country': country, 'country_code': field('country_code'),
            'attribution': ATTRIBUTION}


class GeoapifyClient:
    def __init__(self, api_key, delay=1.1, opener=urlopen, sleeper=time.sleep, clock=time.monotonic):
        if not api_key or not api_key.strip():
            raise ValueError('a Geoapify API key is required for uncached lookups')
        if not math.isfinite(delay) or delay < 1:
            raise ValueError('geocoding request interval must be at least one second')
        self.api_key = api_key.strip()
        self.delay, self.opener, self.sleeper, self.clock = delay, opener, sleeper, clock
        self.last_request = None

    def lookup(self, gps, precision=2, language='en'):
        if self.last_request is not None:
            remaining = self.delay - (self.clock() - self.last_request)
            if remaining > 0:
                self.sleeper(remaining)
        self.last_request = self.clock()
        params = {'lat': round(gps[0], precision), 'lon': round(gps[1], precision),
                  'type': 'city', 'format': 'json', 'limit': 1, 'lang': language, 'apiKey': self.api_key}
        request = Request(ENDPOINT + '?' + urlencode(params), headers={'User-Agent': 'PhotoOrganizer/1.0', 'Accept': 'application/json'})
        try:
            with self.opener(request, timeout=30) as response:
                payload = json.load(response)
            return normalize(payload)
        except HTTPError as e:
            # Never print request URLs, response bodies, or keys in exceptions.
            raise ValueError(f'Geoapify returned HTTP {e.code}; stopped without retrying. Check credentials or quota, then rerun to resume.') from None
        except (URLError, OSError):
            raise ValueError('Geocoding network request failed; check connectivity and rerun to resume.') from None
        except (ValueError, TypeError):
            raise ValueError('Geocoding returned an invalid response; no result was cached.') from None


def save_cached(db, gps, result, precision=2, language='en'):
    db.execute('INSERT OR REPLACE INTO geocodes VALUES (?,?,?)',
               (cache_key(gps, precision, language), json.dumps(result), datetime.now(timezone.utc).isoformat()))
    db.commit()
