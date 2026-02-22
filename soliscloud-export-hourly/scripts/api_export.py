#!/usr/bin/env python3
"""Export SolisCloud solar data via the official API.

Pure Python (stdlib only). Reads credentials from environment variables,
fetches 5-minute interval data for every day in a given month, aggregates
into hourly buckets, and writes a CSV identical in format to the Chrome/XLS
export path.

Usage:
    python3 api_export.py YYYY-MM
"""

import base64
import calendar
import csv
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime

API_HOST = "https://www.soliscloud.com:13333"
API_PATH = "/v1/api/inverterDay"

REQUIRED_ENV_VARS = {
    "SOLISCLOUD_API_KEY": "API Key ID (numeric string from API management page)",
    "SOLISCLOUD_API_SECRET": "API Secret (used for HMAC signing)",
    "SOLISCLOUD_STATION_ID": "Station/plant ID (19-digit string from SolisCloud URL)",
    "SOLISCLOUD_INVERTER_SN": "Inverter serial number",
}


def get_credentials():
    """Read and validate credentials from environment variables."""
    creds = {}
    missing = []
    for var, desc in REQUIRED_ENV_VARS.items():
        val = os.environ.get(var, "").strip()
        if not val:
            missing.append(f"  {var} — {desc}")
        else:
            creds[var] = val

    if missing:
        print("Error: Missing required environment variables:\n")
        print("\n".join(missing))
        print("\nSet these in your shell profile or .env file.")
        print("See README.md for how to obtain credentials from SolisCloud.")
        sys.exit(1)

    return creds


def get_timezone_offset():
    """Get timezone offset as integer hours from UTC."""
    tz_env = os.environ.get("SOLISCLOUD_TIMEZONE", "").strip()
    if tz_env:
        try:
            return int(tz_env)
        except ValueError:
            print(f"Warning: Invalid SOLISCLOUD_TIMEZONE '{tz_env}', using system timezone")

    # Infer from system: time.timezone is seconds west of UTC (positive = west)
    offset = -time.timezone // 3600
    if time.daylight and time.localtime().tm_isdst:
        offset = -time.altzone // 3600
    return offset


def sign_request(api_secret, content_md5, content_type, date_str, path):
    """Generate HMAC-SHA1 signature for SolisCloud API.

    Canonical string: POST\n{Content-MD5}\n{Content-Type}\n{Date}\n{path}
    """
    canonical = f"POST\n{content_md5}\n{content_type}\n{date_str}\n{path}"
    signature = hmac.new(
        api_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(signature).decode("utf-8")


def make_api_request(api_key, api_secret, path, body_dict):
    """Make a signed POST request to the SolisCloud API."""
    body_bytes = json.dumps(body_dict).encode("utf-8")
    content_type = "application/json"
    content_md5 = base64.b64encode(
        hashlib.md5(body_bytes).digest()
    ).decode("utf-8")
    date_str = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")

    signature = sign_request(api_secret, content_md5, content_type, date_str, path)

    url = f"{API_HOST}{path}"
    req = urllib.request.Request(url, data=body_bytes, method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("Content-MD5", content_md5)
    req.add_header("Date", date_str)
    req.add_header("Authorization", f"API {api_key}:{signature}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 408:
            print(f"  HTTP 408: Clock out of sync with SolisCloud server (must be within 15 min of UTC)")
        else:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            print(f"  HTTP {e.code}: {e.reason} — {body}")
        raise
    except urllib.error.URLError as e:
        print(f"  Network error: {e.reason}")
        raise


def fetch_day(api_key, api_secret, inverter_sn, date_str, tz_offset):
    """Fetch 5-minute interval data for a single day."""
    body = {
        "sn": inverter_sn,
        "money": "USD",
        "time": date_str,
        "timeZone": tz_offset,
    }
    resp = make_api_request(api_key, api_secret, API_PATH, body)

    if not resp or not isinstance(resp, dict):
        return []

    # API returns {"success": true, "code": "0", "data": [...]}
    if resp.get("code") != "0" and resp.get("success") is not True:
        msg = resp.get("msg", resp.get("message", "Unknown error"))
        print(f"  API error: {msg}")
        return []

    data = resp.get("data", [])
    if data is None:
        data = []
    return data


def map_api_record(record):
    """Map API response fields to match the XLS/CSV column schema.

    API fields are in kW; CSV expects watts. Sign conventions match.
    """
    def kw_to_w(val):
        try:
            return float(val) * 1000
        except (TypeError, ValueError):
            return 0.0

    def to_float(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    # Time: API provides "dataTimestamp" (epoch ms) or "timeStr"
    time_str = record.get("timeStr", "")
    if not time_str and record.get("dataTimestamp"):
        ts = int(record["dataTimestamp"]) / 1000
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M")
    elif time_str and " " in time_str:
        # timeStr may be "YYYY-MM-DD HH:MM:SS" — extract time part
        time_str = time_str.split(" ")[-1]
        if len(time_str) > 5:
            time_str = time_str[:5]  # HH:MM

    return {
        "Time": time_str,
        "PV(W)": kw_to_w(record.get("pac", 0)),
        "Battery(W)": kw_to_w(record.get("batteryPower", 0)),
        "Grid(W)": kw_to_w(record.get("pSum", 0)),
        "Grid Load(W)": kw_to_w(record.get("familyLoadPower", 0)),
        "Backup Load(W)": kw_to_w(record.get("bypassLoadPower", 0)),
        "SOC(%)": to_float(record.get("batteryCapacitySoc", 0)),
    }


def to_float(val):
    """Convert a value to float, returning 0.0 on failure."""
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def process_hourly(rows, date_str):
    """Group 5-minute records into hourly buckets. Same logic as process_month.py."""
    hourly = defaultdict(list)

    for row in rows:
        time_str = str(row.get("Time", "")).strip()
        if not time_str or ":" not in time_str:
            continue
        hour = time_str.split(":")[0].zfill(2)
        hourly[hour].append(row)

    power_cols = ["PV(W)", "Battery(W)", "Grid(W)", "Grid Load(W)", "Backup Load(W)"]
    results = []

    for hour in sorted(hourly.keys()):
        readings = hourly[hour]
        n = len(readings)

        entry = {
            "Date": date_str,
            "Hour": f"{hour}:00",
            "Readings": n,
        }

        for col in power_cols:
            values = [to_float(r.get(col, 0)) for r in readings]
            avg = sum(values) / n if n else 0
            col_key = col.replace("(W)", "").replace(" ", "")
            entry[f"Avg_{col_key}_W"] = round(avg, 1)
            entry[f"{col_key}_Energy_kWh"] = round(avg * n * 5 / 60 / 1000, 3)

        soc_values = [to_float(r.get("SOC(%)", 0)) for r in readings]
        entry["Avg_SOC_Pct"] = round(sum(soc_values) / n, 1) if n else 0
        entry["Min_SOC_Pct"] = int(min(soc_values)) if soc_values else 0
        entry["Max_SOC_Pct"] = int(max(soc_values)) if soc_values else 0

        results.append(entry)

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 api_export.py YYYY-MM")
        print("\nExports SolisCloud inverter data for a full month via the API.")
        sys.exit(1)

    target_month = sys.argv[1]

    # Parse and validate month
    try:
        parts = target_month.split("-")
        year = int(parts[0])
        month = int(parts[1])
        if month < 1 or month > 12:
            raise ValueError
    except (IndexError, ValueError):
        print(f"Error: Invalid month format '{target_month}'. Expected YYYY-MM.")
        sys.exit(1)

    # Get credentials
    creds = get_credentials()
    api_key = creds["SOLISCLOUD_API_KEY"]
    api_secret = creds["SOLISCLOUD_API_SECRET"]
    inverter_sn = creds["SOLISCLOUD_INVERTER_SN"]
    tz_offset = get_timezone_offset()

    num_days = calendar.monthrange(year, month)[1]
    output_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
        "data",
    )
    os.makedirs(output_dir, exist_ok=True)

    print(f"Exporting SolisCloud data for {target_month} ({num_days} days)")
    print(f"Inverter: {inverter_sn}, Timezone: UTC{tz_offset:+d}")
    print()

    all_hourly_rows = []
    failed_days = []
    empty_days = []
    total_readings = 0
    total_pv_kwh = 0.0
    days_processed = 0

    for day in range(1, num_days + 1):
        date_str = f"{target_month}-{day:02d}"
        sys.stdout.write(f"  {date_str} ... ")
        sys.stdout.flush()

        try:
            raw_records = fetch_day(api_key, api_secret, inverter_sn, date_str, tz_offset)
        except Exception:
            failed_days.append(date_str)
            print("FAILED")
            # Delay before next request even on failure
            if day < num_days:
                time.sleep(0.5)
            continue

        if not raw_records:
            empty_days.append(date_str)
            print("no data")
            if day < num_days:
                time.sleep(0.5)
            continue

        # Map API fields to CSV schema
        mapped = [map_api_record(r) for r in raw_records]
        total_readings += len(mapped)

        # Aggregate into hourly buckets
        hourly = process_hourly(mapped, date_str)
        days_processed += 1

        day_pv = sum(e.get("PV_Energy_kWh", 0) for e in hourly)
        total_pv_kwh += day_pv
        all_hourly_rows.extend(hourly)

        print(f"{len(mapped)} readings, {day_pv:.1f} kWh")

        # Rate limit: 500ms between requests
        if day < num_days:
            time.sleep(0.5)

    print()

    if not all_hourly_rows:
        print(f"Error: No data retrieved for {target_month}.")
        if failed_days:
            print(f"All {len(failed_days)} days failed. Check credentials and network.")
        sys.exit(1)

    # Write CSV with identical column order to process_month.py
    fieldnames = [
        "Date", "Hour", "Readings",
        "Avg_PV_W", "PV_Energy_kWh",
        "Avg_Battery_W", "Battery_Energy_kWh",
        "Avg_Grid_W", "Grid_Energy_kWh",
        "Avg_GridLoad_W", "GridLoad_Energy_kWh",
        "Avg_BackupLoad_W", "BackupLoad_Energy_kWh",
        "Avg_SOC_Pct", "Min_SOC_Pct", "Max_SOC_Pct",
    ]

    output_file = os.path.join(output_dir, f"solar_hourly_{target_month}.csv")

    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_hourly_rows)

    # Print summary
    print(f"=== Solar Monthly Summary: {target_month} ===")
    print(f"Days processed: {days_processed}/{num_days}")
    print(f"Total readings: {total_readings}")
    print(f"Total hourly rows: {len(all_hourly_rows)}")
    print(f"Total PV generation: {total_pv_kwh:.1f} kWh")
    if failed_days:
        print(f"Failed days ({len(failed_days)}): {', '.join(failed_days)}")
    if empty_days:
        print(f"Empty days ({len(empty_days)}): {', '.join(empty_days)}")
    print(f"Output: {output_file}")


if __name__ == "__main__":
    main()
