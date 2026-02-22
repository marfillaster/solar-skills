#!/usr/bin/env python3
"""Process SolisCloud monthly export XLS files into a single hourly CSV."""

import calendar
import csv
import os
import sys
from collections import defaultdict

import xlrd


def parse_xls(filepath):
    """Parse the SolisCloud daily export XLS file."""
    wb = xlrd.open_workbook(filepath)
    sheet = wb.sheets()[0]

    # Find header row by looking for "Number" in column 0
    header_row = None
    for i in range(sheet.nrows):
        if str(sheet.cell_value(i, 0)).strip() == "Number":
            header_row = i
            break

    if header_row is None:
        raise ValueError(f"Could not find header row in {filepath}")

    headers = [str(sheet.cell_value(header_row, j)).strip() for j in range(sheet.ncols)]

    rows = []
    for i in range(header_row + 1, sheet.nrows):
        row = {}
        for j, h in enumerate(headers):
            row[h] = sheet.cell_value(i, j)
        rows.append(row)

    return rows


def to_float(val):
    """Convert a value to float, returning 0.0 on failure."""
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def process_hourly(rows):
    """Group 5-minute data into hourly buckets and compute summaries."""
    hourly = defaultdict(list)

    for row in rows:
        time_str = str(row.get("Time", "")).strip()
        if not time_str or ":" not in time_str:
            continue
        hour = time_str.split(":")[0].zfill(2)
        hourly[hour].append(row)

    # Only include meaningful power columns (drop GEN, Smart, AC Coupled which are always zero)
    power_cols = ["PV(W)", "Battery(W)", "Grid(W)", "Grid Load(W)", "Backup Load(W)"]
    results = []

    for hour in sorted(hourly.keys()):
        readings = hourly[hour]
        n = len(readings)

        entry = {
            "Hour": f"{hour}:00",
            "Readings": n,
        }

        for col in power_cols:
            values = [to_float(r.get(col, 0)) for r in readings]
            avg = sum(values) / n if n else 0
            col_key = col.replace("(W)", "").replace(" ", "")
            entry[f"Avg_{col_key}_W"] = round(avg, 1)
            # Energy in kWh: avg_watts * (readings * 5min) / 60 / 1000
            entry[f"{col_key}_Energy_kWh"] = round(avg * n * 5 / 60 / 1000, 3)

        soc_values = [to_float(r.get("SOC(%)", 0)) for r in readings]
        entry["Avg_SOC_Pct"] = round(sum(soc_values) / n, 1) if n else 0
        entry["Min_SOC_Pct"] = int(min(soc_values)) if soc_values else 0
        entry["Max_SOC_Pct"] = int(max(soc_values)) if soc_values else 0

        results.append(entry)

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: process_month.py YYYY-MM [DOWNLOADS_DIR]")
        sys.exit(1)

    target_month = sys.argv[1]
    downloads_dir = os.path.expanduser(sys.argv[2]) if len(sys.argv) >= 3 else os.path.expanduser("~/Downloads")

    # Parse year and month
    try:
        parts = target_month.split("-")
        year = int(parts[0])
        month = int(parts[1])
    except (IndexError, ValueError):
        print(f"Error: Invalid month format '{target_month}'. Expected YYYY-MM.")
        sys.exit(1)

    num_days = calendar.monthrange(year, month)[1]
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "data")
    os.makedirs(output_dir, exist_ok=True)

    all_rows = []
    missing_days = []
    total_readings = 0
    total_pv_kwh = 0.0
    days_processed = 0

    for day in range(1, num_days + 1):
        date_str = f"{target_month}-{day:02d}"
        input_file = os.path.join(downloads_dir, f"Daily+Power+Station+Chart_{date_str}.xls")

        if not os.path.exists(input_file):
            missing_days.append(date_str)
            continue

        try:
            rows = parse_xls(input_file)
        except Exception as e:
            print(f"Warning: Failed to parse {date_str}: {e}")
            missing_days.append(date_str)
            continue

        hourly = process_hourly(rows)
        days_processed += 1
        total_readings += len(rows)

        for entry in hourly:
            entry["Date"] = date_str
            total_pv_kwh += entry.get("PV_Energy_kWh", 0)
            all_rows.append(entry)

    if not all_rows:
        print(f"Error: No data found for {target_month}")
        sys.exit(1)

    # Define column order with Date first
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
        writer.writerows(all_rows)

    # Print summary
    print(f"=== Solar Monthly Summary: {target_month} ===")
    print(f"Days processed: {days_processed}/{num_days}")
    print(f"Total readings: {total_readings}")
    print(f"Total hourly rows: {len(all_rows)}")
    print(f"Total PV generation: {total_pv_kwh:.1f} kWh")
    if missing_days:
        print(f"Missing days ({len(missing_days)}): {', '.join(missing_days)}")
    print(f"Output: {output_file}")


if __name__ == "__main__":
    main()
