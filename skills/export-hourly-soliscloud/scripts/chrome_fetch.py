#!/usr/bin/env python3
"""
chrome_fetch.py — SolisCloud Chrome fallback bulk fetcher

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

Stdin JSON format (when providing fresh auth):
  {
    "cookie": "token=token_<uuid>",          # required; httpOnly session cookie
    "device_id": "<headerDeviceId>",         # optional; defaults to a known value
    "station_id": "1298491919450376600"      # optional; or pass body_template
  }
  (Legacy: a "body_template" object is still accepted and its "id" used as station.)

To obtain the cookie: in the browser, trigger one Operating-Data chart request,
call get_network_request on it, and copy the value after "token=" in its Cookie
header. Auth is cached to data/.soliscloud_auth.json for CACHE_TTL_HOURS.
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
from calendar import monthrange
from datetime import datetime, timezone

CACHE_PATH = 'data/.soliscloud_auth.json'
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
    os.makedirs('data', exist_ok=True)
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
    station_id = ctx.get('station_id') or body_template.get('id')
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


def fetch_day(auth, day):
    body_template = auth['body_template']
    body = {**body_template, 'time': day, 'localTime': int(time.time() * 1000)}
    data = json.dumps(body, separators=(',', ':')).encode()
    date_str = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
    content_md5, authorization = sign_request(data, date_str)
    req = urllib.request.Request(
        f'https://www.soliscloud.com{API_PATH}',
        data=data,
        method='POST',
    )
    headers = {
        'Content-MD5': content_md5,
        'Time': date_str,
        'Authorization': authorization,
        'Content-Type': REQUEST_CONTENT_TYPE,
        'x-cloud-platform': 'GLY',
        'version': '5.2.501',
        'platform': 'Web',
        'device-id': auth['device_id'],
        'language': body_template.get('language', '2'),
        'Accept': 'application/json, text/plain, */*',
        'Cookie': auth['cookie'],
    }
    for name, value in headers.items():
        req.add_header(name, value)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


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

def main():
    args = sys.argv[1:]
    use_cache = '--no-cache' not in args
    target_month = next((a for a in args if not a.startswith('--')), None)

    if not target_month:
        print('Usage: chrome_fetch.py YYYY-MM [--no-cache]', file=sys.stderr)
        sys.exit(1)

    auth = load_auth(use_cache)

    days = days_in_month(target_month)
    print(f'Fetching {len(days)} days for {target_month}...', flush=True)

    rows = [CSV_HEADER]
    total_pv_kwh = 0
    days_with_data = 0
    failed = []

    for i, day in enumerate(days):
        try:
            resp = fetch_day(auth, day)
            if resp.get('code') in ('401', '403') or resp.get('success') is False:
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

        if i < len(days) - 1:
            time.sleep(0.3)

        if (i + 1) % 5 == 0:
            print(f'  {i + 1}/{len(days)} done', flush=True)

    os.makedirs('data', exist_ok=True)
    out_path = f'data/solar_hourly_{target_month}.csv'
    with open(out_path, 'w') as f:
        f.write('\n'.join(rows) + '\n')

    print(f'\nDone: {days_with_data}/{len(days)} days, {len(rows)-1} rows, {round(total_pv_kwh, 1)} kWh PV')
    if failed:
        print(f'Failed: {failed}')
    print(f'Written: {out_path}')


if __name__ == '__main__':
    main()
