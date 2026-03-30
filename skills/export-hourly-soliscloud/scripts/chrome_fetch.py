#!/usr/bin/env python3
"""
chrome_fetch.py — SolisCloud Chrome fallback bulk fetcher

Usage:
  # Fresh auth from browser — pipe get_network_request output, saves to cache:
  echo '<json>' | python3 chrome_fetch.py 2026-01

  # Cached auth — reuses data/.soliscloud_auth.json if still fresh:
  python3 chrome_fetch.py 2026-01

  # Force re-capture even if cache exists:
  python3 chrome_fetch.py 2026-01 --no-cache

Stdin JSON format (when providing fresh auth):
  {
    "headers": { "authorization": "...", "token": "...", ... },
    "body_template": { "id": "...", "money": "PHP", "timeZone": 8, ... }
  }

Auth is cached to data/.soliscloud_auth.json. Cached auth is reused for up to
CACHE_TTL_HOURS without needing to open the browser again.

Auth headers (Content-MD5, Authorization, Time) are reused as-is — the server
does not re-validate them per-body, as confirmed by the existing XHR fallback.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from calendar import monthrange
from datetime import datetime

CACHE_PATH = 'data/.soliscloud_auth.json'
CACHE_TTL_HOURS = 4

# Headers to forward. Skip browser-fingerprinting and connection headers
# that urllib handles itself or that would confuse the server.
SKIP_HEADERS = {
    ':authority', ':method', ':path', ':scheme',
    'accept-encoding', 'content-length', 'origin',
    'priority', 'referer', 'sec-ch-ua', 'sec-ch-ua-mobile',
    'sec-ch-ua-platform', 'sec-fetch-dest', 'sec-fetch-mode',
    'sec-fetch-site', 'user-agent', 'cookie',
    'accept-language',
}

CSV_HEADER = (
    'Date,Hour,Readings,Avg_PV_W,PV_Energy_kWh,'
    'Avg_Battery_W,Battery_Energy_kWh,'
    'Avg_Grid_W,Grid_Energy_kWh,'
    'Avg_GridLoad_W,GridLoad_Energy_kWh,'
    'Avg_BackupLoad_W,BackupLoad_Energy_kWh,'
    'Avg_SOC_Pct,Min_SOC_Pct,Max_SOC_Pct'
)


# --- Auth cache ---

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


def save_cache(headers, body_template):
    os.makedirs('data', exist_ok=True)
    with open(CACHE_PATH, 'w') as f:
        json.dump({'captured_at': time.time(), 'headers': headers, 'body_template': body_template}, f, indent=2)
    print(f'Auth cached to {CACHE_PATH}.', flush=True)


def load_auth(use_cache):
    """Return (headers, body_template). Reads stdin if provided, else cache."""
    stdin_data = None
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
        if raw.strip():
            stdin_data = raw

    if stdin_data is not None:
        ctx = json.loads(stdin_data)
        headers       = ctx['headers']
        body_template = ctx['body_template']
        save_cache(headers, body_template)
        return headers, body_template

    if use_cache:
        cache = load_cache()
        if cache:
            return cache['headers'], cache['body_template']

    print('No auth provided and no valid cache found.', file=sys.stderr)
    print('Pipe get_network_request output to this script, or re-run the browser capture.', file=sys.stderr)
    sys.exit(1)


# --- HTTP ---

def fetch_day(session_headers, body_template, day):
    body = {**body_template, 'date': day, 'time': day, 'localTime': int(time.time() * 1000)}
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        'https://www.soliscloud.com/api/chart/station/day/v2',
        data=data,
        method='POST',
    )
    for name, value in session_headers.items():
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

    raw_headers, body_template = load_auth(use_cache)
    session_headers = {k: v for k, v in raw_headers.items() if k.lower() not in SKIP_HEADERS}

    days = days_in_month(target_month)
    print(f'Fetching {len(days)} days for {target_month}...', flush=True)

    rows = [CSV_HEADER]
    total_pv_kwh = 0
    days_with_data = 0
    failed = []

    for i, day in enumerate(days):
        try:
            resp = fetch_day(session_headers, body_template, day)
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
