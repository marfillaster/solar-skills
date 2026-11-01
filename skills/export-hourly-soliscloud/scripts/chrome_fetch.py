#!/usr/bin/env python3
"""
chrome_fetch.py — SolisCloud browser fallback bulk fetcher

The SolisCloud web UI signs every request with a per-request HMAC-SHA1 over the
request body's Content-MD5 (canonical string: METHOD\nContent-MD5\nContent-Type\n
Date\n/chart/station/day/v2). The signing key + keyId are static constants baked
into the web JS bundle, so this script re-signs each day itself rather than
replaying a captured signature (replay fails because every day's body — and thus
its MD5 and signature — differs). The only session-bound secret is the login
cookie, which is httpOnly and must be captured once from the browser.

Usage:
  # Fresh auth — pipe the cookie (+ optional device-id/station) once after login:
  echo '<json>' | python3 chrome_fetch.py 2026-01

  # Cached auth — reuses data/.soliscloud_auth.json if still fresh:
  python3 chrome_fetch.py 2026-01

  # Force re-capture even if cache exists:
  python3 chrome_fetch.py 2026-01 --no-cache

  # Browser-relay mode, for a browser that cannot read the httpOnly cookie
  # (Claude in Chrome). Run in the background, then have the logged-in page
  # GET /signed, send each request itself, and POST the responses to /raw:
  python3 chrome_fetch.py 2026-01 --serve [--station-id ID] [--port 8765]

Stdin JSON format (when providing fresh auth):
  {
    "cookie": "token=token_<uuid>",          # required; httpOnly session cookie
    "device_id": "<headerDeviceId>",         # optional; defaults to a known value
    "station_id": "1298491919450376600"      # optional once cached; or pass body_template
  }
  (Legacy: a "body_template" object is still accepted and its "id" used as station.)

To obtain the cookie, from a browser logged into SolisCloud:
  - cmux agent browser (preferred): read it straight from the cookie store,
    `cmux --json browser <surface> cookies get --all` → the cookie named "token".
    (cmux's WKWebView has no network-request inspection, so cookie store it is.)
  - Chrome DevTools MCP: trigger one Operating-Data chart request, call
    get_network_request on it, and copy the value after "token=" in its Cookie
    header.
  - Claude in Chrome: the cookie is out of reach (httpOnly, and the extension
    does not expose request headers), so use --serve instead: this script
    signs, the page sends the requests with its own cookie, and the raw
    responses come back over 127.0.0.1.
Auth is cached to data/.soliscloud_auth.json for CACHE_TTL_HOURS.
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from calendar import monthrange
from datetime import datetime, timezone

def solar_data_dir() -> str:
    """Data directory for exports and the auth cache.

    Override with the ``SOLAR_DATA_DIR`` environment variable (absolute, or
    relative to the current working directory); defaults to ``data``.
    """
    override = os.environ.get("SOLAR_DATA_DIR", "").strip()
    return os.path.expanduser(override) if override else 'data'


CACHE_PATH = os.path.join(solar_data_dir(), '.soliscloud_auth.json')
CACHE_TTL_HOURS = 4

API_PATH = '/api/chart/station/day/v2'
SIGN_PATH = '/chart/station/day/v2'
CONTENT_TYPE = 'application/json'
REQUEST_CONTENT_TYPE = 'application/json;charset=UTF-8'
DEFAULT_DEVICE_ID = 'vKAihGyggoe9PKuRYrWvaS4vo69FSE6JEV2HIszKhSaYx5XQ1deqpbHsdKTeOfGP'

# Static signing material extracted from the SolisCloud web bundle. The bundle
# stores these as bit strings with a complement transform.
WEB_KEY_ID_BITS = '011010000111'
WEB_SECRET_PARTS = (
    '0101100111111011000001111101110001000100011',
    '01010111010001000110101100110111110000010100',
    '00111111101001100101010101110011',
)

CSV_HEADER = (
    'Date,Hour,Readings,Avg_PV_W,PV_Energy_kWh,'
    'Avg_Battery_W,Battery_Energy_kWh,'
    'Avg_Grid_W,Grid_Energy_kWh,'
    'Avg_GridLoad_W,GridLoad_Energy_kWh,'
    'Avg_BackupLoad_W,BackupLoad_Energy_kWh,'
    'Avg_SOC_Pct,Min_SOC_Pct,Max_SOC_Pct'
)


# --- Auth cache ---

def bit_complement(value):
    return ''.join('1' if c == '0' else '0' for c in value)


def web_key_id():
    return str(int(bit_complement(WEB_KEY_ID_BITS), 2))


def web_secret():
    first, second, third = WEB_SECRET_PARTS
    return (
        str(int(bit_complement(first), 2)) +
        format(int(bit_complement(second), 2), 'x') +
        format(int(bit_complement(third), 2), 'x')
    )


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return None
    with open(CACHE_PATH) as f:
        cache = json.load(f)
    age_hours = (time.time() - cache.get('captured_at', 0)) / 3600
    if age_hours > CACHE_TTL_HOURS:
        print(f'Auth cache expired ({age_hours:.1f}h old, TTL={CACHE_TTL_HOURS}h).', flush=True)
        return None
    print(f'Using cached auth ({age_hours:.1f}h old).', flush=True)
    return cache


def save_cache(auth):
    os.makedirs(solar_data_dir(), exist_ok=True)
    with open(CACHE_PATH, 'w') as f:
        data = {'captured_at': time.time(), **auth}
        json.dump(data, f, indent=2)
    print(f'Auth cached to {CACHE_PATH}.', flush=True)


def header_value(headers, name):
    if not headers:
        return None
    wanted = name.lower()
    if isinstance(headers, dict):
        for key, value in headers.items():
            if key.lower() == wanted:
                return value
    elif isinstance(headers, list):
        for header in headers:
            key = (header.get('name') or header.get('key') or '').lower()
            if key == wanted:
                return header.get('value')
    return None


def normalize_cookie(cookie):
    if not cookie:
        return None
    cookie = cookie.strip()
    if not cookie:
        return None
    if cookie.startswith('token='):
        return cookie
    if cookie.startswith('token_'):
        return f'token={cookie}'
    if 'token=token_' in cookie:
        parts = [p.strip() for p in cookie.split(';')]
        token_part = next((p for p in parts if p.startswith('token=token_')), None)
        if token_part:
            return token_part
    return cookie


def body_template_from_context(ctx):
    body_template = dict(ctx.get('body_template') or {})
    station_id = ctx.get('station_id') or body_template.get('id') or cached_station_id()
    if not station_id:
        raise ValueError('Fresh auth requires station_id or body_template.id')

    body_template.update({
        'id': str(station_id),
        'money': body_template.get('money', ctx.get('money', 'PHP')),
        'timeZone': int(body_template.get('timeZone', ctx.get('time_zone', 8))),
        'version': int(body_template.get('version', 1)),
        'localTimeZone': int(body_template.get('localTimeZone', ctx.get('local_time_zone', 8))),
        'language': str(body_template.get('language', ctx.get('language', '2'))),
    })
    body_template.pop('date', None)
    body_template.pop('localTime', None)
    body_template.pop('time', None)
    return body_template


def normalize_auth(ctx):
    headers = ctx.get('headers') or {}
    cookie = normalize_cookie(
        ctx.get('cookie') or
        header_value(headers, 'cookie')
    )
    if not cookie:
        raise ValueError('Fresh auth requires a Cookie header containing token=token_<uuid>')

    return {
        'cookie': cookie,
        'device_id': ctx.get('device_id') or header_value(headers, 'device-id') or DEFAULT_DEVICE_ID,
        'body_template': body_template_from_context(ctx),
    }


def load_auth(use_cache):
    """Return normalized auth. Reads stdin if provided, else cache."""
    stdin_data = None
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
        if raw.strip():
            stdin_data = raw

    if stdin_data is not None:
        try:
            auth = normalize_auth(json.loads(stdin_data))
        except (json.JSONDecodeError, ValueError) as e:
            print(f'Invalid auth input: {e}', file=sys.stderr)
            sys.exit(1)
        save_cache(auth)
        return auth

    if use_cache:
        cache = load_cache()
        if cache:
            try:
                return normalize_auth(cache)
            except ValueError as e:
                print(f'Cached auth is not usable: {e}', file=sys.stderr)

    print('No auth provided and no valid cache found.', file=sys.stderr)
    print('Pipe a captured Cookie header + station_id to this script, or re-run the browser capture.', file=sys.stderr)
    sys.exit(1)


# --- HTTP ---

def sign_request(body_bytes, date_str):
    content_md5 = base64.b64encode(hashlib.md5(body_bytes).digest()).decode()
    canonical = f'POST\n{content_md5}\n{CONTENT_TYPE}\n{date_str}\n{SIGN_PATH}'
    signature = hmac.new(
        web_secret().encode(),
        canonical.encode(),
        hashlib.sha1,
    ).digest()
    return content_md5, f'WEB {web_key_id()}:{base64.b64encode(signature).decode()}'


def signed_request(body_template, device_id, day):
    """Return (body bytes, headers) for one day's chart request, minus the cookie."""
    body = {**body_template, 'time': day, 'localTime': int(time.time() * 1000)}
    data = json.dumps(body, separators=(',', ':')).encode()
    date_str = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
    content_md5, authorization = sign_request(data, date_str)
    headers = {
        'Content-MD5': content_md5,
        'Time': date_str,
        'Authorization': authorization,
        'Content-Type': REQUEST_CONTENT_TYPE,
        'x-cloud-platform': 'GLY',
        'version': '5.2.501',
        'platform': 'Web',
        'device-id': device_id,
        'language': body_template.get('language', '2'),
        'Accept': 'application/json, text/plain, */*',
    }
    return data, headers


def fetch_day(auth, day):
    data, headers = signed_request(auth['body_template'], auth['device_id'], day)
    req = urllib.request.Request(
        f'https://www.soliscloud.com{API_PATH}',
        data=data,
        method='POST',
    )
    for name, value in {**headers, 'Cookie': auth['cookie']}.items():
        req.add_header(name, value)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# --- Browser relay (--serve) ---

RELAY_ORIGIN = 'https://www.soliscloud.com'
RELAY_TIMEOUT_SECONDS = 600


def cached_station_id():
    """Station ID from the auth cache, whatever its age (the ID never expires)."""
    if not os.path.exists(CACHE_PATH):
        return None
    with open(CACHE_PATH) as f:
        cache = json.load(f)
    return cache.get('station_id') or (cache.get('body_template') or {}).get('id')


def serve_relay(target_month, station_id, port):
    """Sign requests for a logged-in page and collect its responses.

    GET /signed returns {"days": {day: {"body", "headers"}}}, signed at request
    time so the Time headers are fresh. POST /raw takes
    {"responses": {day: <chart response>}, "failed": [...]}. Returns the
    responses once posted, or exits 3 if nothing arrives in time.
    """
    body_template = body_template_from_context({'station_id': station_id})
    device_id = DEFAULT_DEVICE_ID
    days = days_in_month(target_month)
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def cors(self):
            self.send_header('Access-Control-Allow-Origin', RELAY_ORIGIN)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            # Chrome's Private Network Access preflight for public site -> 127.0.0.1.
            self.send_header('Access-Control-Allow-Private-Network', 'true')

        def reply(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.cors()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            self.send_response(204)
            self.cors()
            self.end_headers()

        def do_GET(self):
            if self.path != '/signed':
                return self.reply(404, {'error': 'not found'})
            signed = {}
            for day in days:
                data, headers = signed_request(body_template, device_id, day)
                signed[day] = {'body': data.decode(), 'headers': headers}
            print(f'Signed {len(signed)} requests for the page.', flush=True)
            self.reply(200, {'days': signed})

        def do_POST(self):
            if self.path != '/raw':
                return self.reply(404, {'error': 'not found'})
            length = int(self.headers.get('Content-Length', 0))
            try:
                received.update(json.loads(self.rfile.read(length)))
            except json.JSONDecodeError as e:
                return self.reply(400, {'error': str(e)})
            self.reply(200, {'ok': True})

    server = HTTPServer(('127.0.0.1', port), Handler)
    server.timeout = 5
    deadline = time.time() + RELAY_TIMEOUT_SECONDS
    print(f'Relay listening on http://127.0.0.1:{port} for {target_month} '
          f'(station {station_id}).', flush=True)
    while not received and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if not received:
        print(f'No responses posted within {RELAY_TIMEOUT_SECONDS}s.', file=sys.stderr)
        sys.exit(3)
    if received.get('error'):
        print(f'Page reported an error: {received["error"]}', file=sys.stderr)
    return received


# --- Data processing ---

def days_in_month(target_month):
    year, month = map(int, target_month.split('-'))
    _, n = monthrange(year, month)
    return [f'{target_month}-{d:02d}' for d in range(1, n + 1)]


def parse_hour(t):
    if isinstance(t, str):
        return int(t.split(':')[0])
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t / 1000).hour
    return None


def aggregate_day(data):
    time_arr = data.get('timeStr') or data.get('time') or []
    if not time_arr:
        return None

    power     = data.get('power',                  [])
    battery   = data.get('batteryPower',           [])
    grid      = data.get('psum',                   [])
    grid_load = data.get('familyLoadPowerList',    [])
    backup    = data.get('bypassLoadPowerList',    [])
    soc       = data.get('batteryCapacitySocList', [])

    hourly = {}
    for i, t in enumerate(time_arr):
        h = parse_hour(t)
        if h is None:
            continue
        if h not in hourly:
            hourly[h] = dict(pv=0, bat=0, grid=0, gl=0, bl=0,
                             soc=0, soc_min=float('inf'), soc_max=float('-inf'), n=0)
        b = hourly[h]
        b['pv']   += power[i]     if i < len(power)     else 0
        b['bat']  += battery[i]   if i < len(battery)   else 0
        b['grid'] += grid[i]      if i < len(grid)       else 0
        b['gl']   += grid_load[i] if i < len(grid_load)  else 0
        b['bl']   += backup[i]    if i < len(backup)     else 0
        s = soc[i] if i < len(soc) else 0
        b['soc'] += s
        b['soc_min'] = min(b['soc_min'], s)
        b['soc_max'] = max(b['soc_max'], s)
        b['n'] += 1

    return hourly


def hourly_to_rows(day, hourly):
    rows = []
    for h in sorted(hourly):
        b = hourly[h]
        n = b['n']
        if n == 0:
            continue
        avg_pv   = b['pv']   / n
        avg_bat  = b['bat']  / n
        avg_grid = b['grid'] / n
        avg_gl   = b['gl']   / n
        avg_bl   = b['bl']   / n
        avg_soc  = b['soc']  / n
        ef      = n * 5 / 60 / 1000
        soc_min = 0 if b['soc_min'] == float('inf')  else round(b['soc_min'])
        soc_max = 0 if b['soc_max'] == float('-inf') else round(b['soc_max'])
        rows.append(','.join(str(x) for x in [
            day, f'{h:02d}:00', n,
            round(avg_pv,   1), round(avg_pv   * ef, 3),
            round(avg_bat,  1), round(avg_bat  * ef, 3),
            round(avg_grid, 1), round(avg_grid * ef, 3),
            round(avg_gl,   1), round(avg_gl   * ef, 3),
            round(avg_bl,   1), round(avg_bl   * ef, 3),
            round(avg_soc,  1), soc_min, soc_max,
        ]))
    return rows


# --- Main ---

USAGE = 'Usage: chrome_fetch.py YYYY-MM [--no-cache] [--serve [--station-id ID] [--port N]]'


def parse_args(argv):
    opts = {'target_month': None, 'use_cache': True, 'serve': False,
            'station_id': None, 'port': 8765}
    it = iter(argv)
    for a in it:
        if a == '--no-cache':
            opts['use_cache'] = False
        elif a == '--serve':
            opts['serve'] = True
        elif a in ('--station-id', '--port'):
            value = next(it, None)
            if value is None:
                print(f'{a} needs a value.\n{USAGE}', file=sys.stderr)
                sys.exit(1)
            opts[a[2:].replace('-', '_')] = int(value) if a == '--port' else value
        elif not a.startswith('--') and opts['target_month'] is None:
            opts['target_month'] = a
        else:
            print(f'Unknown argument: {a}\n{USAGE}', file=sys.stderr)
            sys.exit(1)
    if not opts['target_month']:
        print(USAGE, file=sys.stderr)
        sys.exit(1)
    return opts


def main():
    opts = parse_args(sys.argv[1:])
    target_month = opts['target_month']
    days = days_in_month(target_month)

    if opts['serve']:
        station_id = opts['station_id'] or cached_station_id()
        if not station_id:
            print('No station ID: pass --station-id (from the plant details URL).', file=sys.stderr)
            sys.exit(1)
        relayed = serve_relay(target_month, station_id, opts['port'])
        responses = relayed.get('responses') or {}
        relay_failed = {f[0] if isinstance(f, list) else f: f for f in relayed.get('failed') or []}

        def get_response(day):
            if day in responses:
                return responses[day]
            raise RuntimeError(f'not fetched by the page ({relay_failed.get(day, "missing")})')
        print(f'Received {len(responses)} days from the page.', flush=True)
    else:
        auth = load_auth(opts['use_cache'])
        print(f'Fetching {len(days)} days for {target_month}...', flush=True)

        def get_response(day):
            resp = fetch_day(auth, day)
            time.sleep(0.3)
            return resp

    rows = [CSV_HEADER]
    total_pv_kwh = 0
    days_with_data = 0
    failed = []

    for i, day in enumerate(days):
        try:
            resp = get_response(day)
            if resp.get('code') in ('401', '403') or resp.get('success') is False:
                if opts['serve']:
                    print('\nAuth rejected by server: the page is not logged into SolisCloud.', file=sys.stderr)
                else:
                    print(f'\nAuth rejected by server. Delete {CACHE_PATH} and re-run with fresh browser capture.', file=sys.stderr)
                sys.exit(2)
            data = resp.get('data') or {}
            hourly = aggregate_day(data)
            if hourly:
                day_rows = hourly_to_rows(day, hourly)
                rows.extend(day_rows)
                total_pv_kwh += sum(float(r.split(',')[4]) for r in day_rows)
                days_with_data += 1
            else:
                print(f'  {day}: no data', flush=True)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                print(f'\nHTTP {e.code} — auth expired. Delete {CACHE_PATH} and re-run with fresh browser capture.', file=sys.stderr)
                sys.exit(2)
            print(f'  {day}: FAILED — HTTP {e.code}', flush=True)
            failed.append(day)
        except Exception as e:
            print(f'  {day}: FAILED — {e}', flush=True)
            failed.append(day)

        if (i + 1) % 5 == 0 and not opts['serve']:
            print(f'  {i + 1}/{len(days)} done', flush=True)

    data_dir = solar_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    out_path = os.path.join(data_dir, f'solar_hourly_{target_month}.csv')
    with open(out_path, 'w') as f:
        f.write('\n'.join(rows) + '\n')

    print(f'\nDone: {days_with_data}/{len(days)} days, {len(rows)-1} rows, {round(total_pv_kwh, 1)} kWh PV')
    if failed:
        print(f'Failed: {failed}')
    print(f'Written: {out_path}')


if __name__ == '__main__':
    main()
