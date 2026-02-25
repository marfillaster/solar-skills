#!/usr/bin/env python3
"""Export Deye/Solarman solar data via the Solarman Open API.

Pure Python (stdlib only). Reads credentials from environment variables,
authenticates via bearer token, fetches 5-minute interval station data
for every day in a given month, aggregates into hourly buckets, and
writes a CSV identical in format to the SolisCloud export path.

Usage:
    python3 api_export.py YYYY-MM
"""

import calendar
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime

API_HOST = "https://globalapi.solarmanpv.com"

REQUIRED_ENV_VARS = {
    "SOLARMAN_APP_ID": "Application ID (from Solarman API access request)",
    "SOLARMAN_APP_SECRET": "Application secret (from Solarman API access request)",
    "SOLARMAN_EMAIL": "Solarman Smart account email",
    "SOLARMAN_PASSWORD": "Solarman Smart account password",
}

OPTIONAL_ENV_VARS = {
    "SOLARMAN_STATION_ID": "Station/plant ID (auto-discovered if not set)",
    "SOLARMAN_DEVICE_SN": "Inverter device serial number (auto-discovered if not set)",
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
        print("See README.md for how to obtain credentials from Solarman.")
        sys.exit(1)

    # Optional vars
    for var in OPTIONAL_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if val:
            creds[var] = val

    return creds


def api_request(path, body_dict, token=None, app_id=None):
    """Make a POST request to the Solarman Open API."""
    url = f"{API_HOST}{path}"
    if app_id and "?" not in path:
        url = f"{url}?appId={app_id}"
    elif app_id:
        url = f"{url}&appId={app_id}"

    body_bytes = json.dumps(body_dict).encode("utf-8")

    req = urllib.request.Request(url, data=body_bytes, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        print(f"  HTTP {e.code}: {e.reason} — {body}")
        raise
    except urllib.error.URLError as e:
        print(f"  Network error: {e.reason}")
        raise


def authenticate(app_id, app_secret, email, password):
    """Authenticate and obtain a bearer token.

    POST /account/v1.0/token?appId={appId}
    Body: {"appSecret": "...", "email": "...", "password": "<SHA256 hash>"}
    """
    password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()

    body = {
        "appSecret": app_secret,
        "email": email,
        "password": password_hash,
    }

    resp = api_request("/account/v1.0/token", body, app_id=app_id)

    if not resp or not isinstance(resp, dict):
        print("Error: Empty authentication response")
        sys.exit(1)

    if not resp.get("success", False) and resp.get("code") not in (None, "0"):
        msg = resp.get("msg", resp.get("message", "Unknown error"))
        print(f"Error: Authentication failed — {msg}")
        sys.exit(1)

    token = resp.get("access_token")
    if not token:
        print(f"Error: No access_token in response: {json.dumps(resp)[:200]}")
        sys.exit(1)

    # Token validity: resp.get("expires_in") is typically ~5184000 (60 days)
    return token


def discover_station(token, app_id):
    """Find the first station on the account.

    POST /station/v1.0/list
    """
    body = {"page": 1, "size": 10}
    resp = api_request("/station/v1.0/list", body, token=token, app_id=app_id)

    if not resp or not resp.get("success", False):
        msg = resp.get("msg", "Unknown error") if resp else "Empty response"
        print(f"Error: Failed to list stations — {msg}")
        sys.exit(1)

    stations = resp.get("stationList", [])
    if not stations:
        print("Error: No stations found on this account.")
        sys.exit(1)

    station = stations[0]
    station_id = station.get("id")
    station_name = station.get("name", "Unknown")

    if len(stations) > 1:
        print(f"Warning: {len(stations)} stations found, using first: '{station_name}' (ID: {station_id})")
        print("  Set SOLARMAN_STATION_ID to select a specific station.")
    else:
        print(f"Auto-discovered station: '{station_name}' (ID: {station_id})")

    return station_id


def discover_device(token, app_id, station_id):
    """Find the inverter device for a station.

    POST /station/v1.0/device
    """
    body = {"stationId": station_id, "page": 1, "size": 10}
    resp = api_request("/station/v1.0/device", body, token=token, app_id=app_id)

    if not resp or not resp.get("success", False):
        msg = resp.get("msg", "Unknown error") if resp else "Empty response"
        print(f"Error: Failed to list devices — {msg}")
        sys.exit(1)

    devices = resp.get("deviceListItems", [])
    if not devices:
        print("Error: No devices found for this station.")
        sys.exit(1)

    # Prefer inverter type devices
    inverter = None
    for dev in devices:
        # deviceType 1 = inverter in Solarman API
        if dev.get("deviceType") == 1 or "inverter" in dev.get("deviceTypeName", "").lower():
            inverter = dev
            break

    if not inverter:
        inverter = devices[0]
        print(f"Warning: No inverter-type device found, using first device.")

    device_sn = inverter.get("deviceSn", "")
    device_name = inverter.get("deviceName", inverter.get("name", "Unknown"))

    if len(devices) > 1:
        print(f"Warning: {len(devices)} devices found, using: '{device_name}' (SN: {device_sn})")
        print("  Set SOLARMAN_DEVICE_SN to select a specific device.")
    else:
        print(f"Auto-discovered device: '{device_name}' (SN: {device_sn})")

    return device_sn


def fetch_station_day(token, app_id, station_id, date_str):
    """Fetch 5-minute interval station data for a single day.

    POST /station/v1.0/history
    Body: {"stationId": X, "timeType": 1, "startTime": "YYYY-MM-DD", "endTime": "YYYY-MM-DD"}

    timeType=1 returns frame-level (5-minute) data for a single day.
    """
    body = {
        "stationId": station_id,
        "timeType": 1,
        "startTime": date_str,
        "endTime": date_str,
    }

    resp = api_request("/station/v1.0/history", body, token=token, app_id=app_id)

    if not resp or not isinstance(resp, dict):
        return []

    if not resp.get("success", False) and resp.get("code") not in (None, "0"):
        msg = resp.get("msg", resp.get("message", "Unknown error"))
        print(f"  API error: {msg}")
        return []

    # Response contains stationDataItems — list of frame records
    data = resp.get("stationDataItems", [])
    if data is None:
        data = []
    return data


def map_station_record(record):
    """Map Solarman station history fields to the CSV column schema.

    Station frame data fields (all in watts):
    - generationPower: PV generation power
    - usePower: total consumption/load power
    - gridPower: grid export power (positive = exporting)
    - purchasePower: grid import power (positive = importing)
    - batteryPower: battery power (positive = charging, negative = discharging)
    - chargePower: battery charging power (always >= 0)
    - dischargePower: battery discharging power (always >= 0)
    - batterySoc: battery state of charge (%)
    - wirePower: grid-tie power (alternative to gridPower)
    """
    def to_float(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    # Time: API provides "dateTime" as epoch seconds or formatted string
    time_str = ""
    date_time = record.get("dateTime")
    if date_time is not None:
        try:
            # If it's a unix timestamp (integer)
            ts = int(date_time)
            if ts > 1000000000000:  # milliseconds
                ts = ts / 1000
            time_str = datetime.fromtimestamp(ts).strftime("%H:%M")
        except (ValueError, TypeError, OSError):
            # If it's a string like "2026-02-01 08:05:00"
            dt_str = str(date_time)
            if " " in dt_str:
                time_str = dt_str.split(" ")[-1][:5]
            else:
                time_str = dt_str[:5]

    # PV generation — always >= 0
    pv_w = to_float(record.get("generationPower", 0))

    # Battery power
    # batteryPower: positive = charging, negative = discharging (matches our convention)
    battery_power = record.get("batteryPower")
    if battery_power is not None:
        battery_w = to_float(battery_power)
    else:
        # Fallback: use chargePower and dischargePower
        charge = to_float(record.get("chargePower", 0))
        discharge = to_float(record.get("dischargePower", 0))
        battery_w = charge - discharge  # positive = charging, negative = discharging

    # Grid power: positive = export, negative = import
    # purchasePower is import (always >= 0), gridPower/wirePower is export (always >= 0)
    purchase_w = to_float(record.get("purchasePower", 0))
    grid_export = to_float(record.get("gridPower", 0))
    if grid_export == 0:
        grid_export = to_float(record.get("wirePower", 0))
    grid_w = grid_export - purchase_w  # positive = export, negative = import

    # Load/consumption
    use_w = to_float(record.get("usePower", 0))

    # SOC
    soc = to_float(record.get("batterySoc", 0))

    return {
        "Time": time_str,
        "PV(W)": pv_w,
        "Battery(W)": battery_w,
        "Grid(W)": grid_w,
        "Grid Load(W)": use_w,
        "Backup Load(W)": 0.0,  # Not distinguishable from station-level data
        "SOC(%)": soc,
    }


def to_float(val):
    """Convert a value to float, returning 0.0 on failure."""
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def process_hourly(rows, date_str):
    """Group 5-minute records into hourly buckets."""
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
        print("\nExports Deye/Solarman inverter data for a full month via the API.")
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
    app_id = creds["SOLARMAN_APP_ID"]
    app_secret = creds["SOLARMAN_APP_SECRET"]
    email = creds["SOLARMAN_EMAIL"]
    password = creds["SOLARMAN_PASSWORD"]

    # Authenticate
    print("Authenticating with Solarman API...")
    token = authenticate(app_id, app_secret, email, password)
    print("Authentication successful.")
    print()

    # Discover or use configured station ID
    station_id = creds.get("SOLARMAN_STATION_ID")
    if not station_id:
        station_id = discover_station(token, app_id)
    else:
        print(f"Using configured station ID: {station_id}")

    # Ensure station_id is an integer for the API
    try:
        station_id = int(station_id)
    except (ValueError, TypeError):
        print(f"Error: Invalid station ID '{station_id}'. Must be numeric.")
        sys.exit(1)

    # Discover or use configured device SN (logged for reference)
    device_sn = creds.get("SOLARMAN_DEVICE_SN")
    if not device_sn:
        device_sn = discover_device(token, app_id, station_id)
    else:
        print(f"Using configured device SN: {device_sn}")

    print()

    num_days = calendar.monthrange(year, month)[1]
    output_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
        "data",
    )
    os.makedirs(output_dir, exist_ok=True)

    print(f"Exporting Solarman data for {target_month} ({num_days} days)")
    print(f"Station: {station_id}, Device: {device_sn}")
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
            raw_records = fetch_station_day(token, app_id, station_id, date_str)
        except Exception:
            failed_days.append(date_str)
            print("FAILED")
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
        mapped = [map_station_record(r) for r in raw_records]
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

    # Write CSV with identical column order to SolisCloud export
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
