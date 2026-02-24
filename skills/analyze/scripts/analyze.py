#!/usr/bin/env python3
"""
Solar analysis script — computes all metrics from hourly CSV data.

Usage:
    python3 analyze.py < config.json
    python3 analyze.py config.json

Input: JSON config on stdin or as file argument with keys:
    pv_kwp              float   PV system size in kWp
    inverter_kw         float   Inverter AC output capacity in kW
    battery_nominal_kwh float   Battery nominal capacity in kWh
    has_ev              bool    Whether household has EV/PHEV
    feedin_ratio        float   Feed-in tariff as ratio of import rate (0 = no feed-in, 0.5 = typical)
    additional_kwp      float   Additional panel capacity (0 if none)
    seasonal_factors    dict    Month number (str) -> adjustment factor
    grid_emission_factor float  kg CO2/kWh for the user's grid
    tariff.type         str     "flat", "tiered", or "tou"
    tariff.import_rate  float   Flat rate per kWh
    tariff.tiers        list    [{threshold, rate}, ...] for tiered (optional)
    tariff.tou          dict    {peak_hours: [...], peak_rate, offpeak_rate} (optional)
    roi                 dict    {total_cost, system_age_years} or null
    currency            str     Currency symbol

Output: JSON to stdout with all computed metrics.

Must be run from the project root directory (where data/ lives).
"""

import csv
import datetime
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_csv_files(data_dir: Path) -> list[dict]:
    """Load all solar_hourly_*.csv files, return list of row dicts."""
    files = sorted(data_dir.glob("solar_hourly_*.csv"))
    if not files:
        print(json.dumps({"error": "No solar_hourly_*.csv files found in data/"}))
        sys.exit(1)

    rows = []
    filenames = []
    for f in files:
        filenames.append(f.name)
        with open(f) as fh:
            reader = csv.DictReader(fh)
            for r in reader:
                # Convert numeric fields
                for k in r:
                    if k in ("Date", "Hour"):
                        continue
                    try:
                        r[k] = float(r[k])
                    except (ValueError, TypeError):
                        r[k] = 0.0
                rows.append(r)
    return rows, filenames


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


# ---------------------------------------------------------------------------
# Group data
# ---------------------------------------------------------------------------

def group_by(rows, key_fn):
    groups = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    return groups


def row_month(r):
    return r["Date"][:7]  # YYYY-MM


def row_day(r):
    return r["Date"][:10]  # YYYY-MM-DD


def row_hour(r):
    return r["Hour"]


# Derived fields per row
def enrich(r):
    r["Load_kWh"] = r["GridLoad_Energy_kWh"] + r["BackupLoad_Energy_kWh"]
    r["Load_W"] = r["Avg_GridLoad_W"] + r["Avg_BackupLoad_W"]
    r["Grid_Import_kWh"] = abs(min(0, r["Grid_Energy_kWh"]))
    r["Grid_Export_kWh"] = max(0, r["Grid_Energy_kWh"])
    r["Battery_Charge_kWh"] = max(0, r["Battery_Energy_kWh"])
    r["Battery_Discharge_kWh"] = abs(min(0, r["Battery_Energy_kWh"]))
    return r


# ---------------------------------------------------------------------------
# Analysis sections
# ---------------------------------------------------------------------------

def compute_monthly_totals(rows):
    """3a. Monthly totals."""
    by_month = group_by(rows, row_month)
    result = {}
    for m, mrows in sorted(by_month.items()):
        total_pv = sum(r["PV_Energy_kWh"] for r in mrows)
        total_load = sum(r["Load_kWh"] for r in mrows)
        grid_export = sum(r["Grid_Export_kWh"] for r in mrows)
        grid_import = sum(r["Grid_Import_kWh"] for r in mrows)
        battery_charge = sum(r["Battery_Charge_kWh"] for r in mrows)
        battery_discharge = sum(r["Battery_Discharge_kWh"] for r in mrows)
        self_consumed = total_load - grid_import
        sc_rate = (self_consumed / total_pv * 100) if total_pv > 0 else 0
        ss = (1 - grid_import / total_load) * 100 if total_load > 0 else None
        days = len(set(row_day(r) for r in mrows))

        result[m] = {
            "total_pv": round(total_pv, 1),
            "total_load": round(total_load, 1),
            "grid_export": round(grid_export, 1),
            "grid_import": round(grid_import, 1),
            "battery_charge": round(battery_charge, 1),
            "battery_discharge": round(battery_discharge, 1),
            "self_consumed": round(self_consumed, 1),
            "self_consumption_rate": round(sc_rate, 1),
            "self_sufficiency": round(ss, 1) if ss is not None else None,
            "days": days,
        }
    return result


def detect_ev_days(rows):
    """3b. EV day detection."""
    by_day = group_by(rows, row_day)
    daily_loads = {}
    for day, drows in by_day.items():
        if len(drows) > 20:
            daily_loads[day] = sum(r["Load_kWh"] for r in drows)

    if not daily_loads:
        return set(), set(), {}

    avg_load = mean(list(daily_loads.values()))
    threshold = max(8, avg_load * 0.3)

    ev_days = set()
    non_ev_days = set()
    for day, load in daily_loads.items():
        if load > avg_load + threshold:
            ev_days.add(day)
        else:
            non_ev_days.add(day)

    info = {
        "avg_daily_load": round(avg_load, 1),
        "threshold": round(threshold, 1),
        "ev_day_count": len(ev_days),
        "non_ev_day_count": len(non_ev_days),
        "total_full_days": len(daily_loads),
        "ev_dates": sorted(ev_days),
    }
    if ev_days:
        info["ev_avg_load"] = round(mean([daily_loads[d] for d in ev_days]), 1)
    if non_ev_days:
        info["non_ev_avg_load"] = round(
            mean([daily_loads[d] for d in non_ev_days]), 1
        )

    return ev_days, non_ev_days, info


def compute_hourly_patterns(rows, ev_days, non_ev_days):
    """3c. Hourly patterns."""
    result = {}
    for label, day_set in [("non_ev", non_ev_days), ("ev", ev_days)]:
        subset = [r for r in rows if row_day(r) in day_set]
        if not subset:
            continue
        by_hour = group_by(subset, row_hour)
        hourly = {}
        for h, hrows in sorted(by_hour.items()):
            hourly[h] = {
                "avg_pv_w": round(mean([r["Avg_PV_W"] for r in hrows]), 0),
                "avg_load_w": round(mean([r["Load_W"] for r in hrows]), 0),
                "avg_battery_w": round(mean([r["Avg_Battery_W"] for r in hrows]), 0),
                "avg_grid_w": round(mean([r["Avg_Grid_W"] for r in hrows]), 0),
                "avg_soc": round(mean([r["Avg_SOC_Pct"] for r in hrows]), 1),
                "avg_grid_import": round(mean([r["Grid_Import_kWh"] for r in hrows]), 3),
                "avg_grid_export": round(mean([r["Grid_Export_kWh"] for r in hrows]), 3),
            }
        result[label] = hourly

    # Peak PV hours
    all_by_hour = group_by(rows, row_hour)
    pv_hourly = {h: mean([r["Avg_PV_W"] for r in hrs]) for h, hrs in all_by_hour.items()}
    max_pv = max(pv_hourly.values()) if pv_hourly else 0
    peak_pv_hours = sorted([h for h, v in pv_hourly.items() if v > max_pv * 0.5])

    # Export hours
    export_hourly = {
        h: mean([r["Grid_Export_kWh"] for r in hrs]) for h, hrs in all_by_hour.items()
    }
    export_hours = sorted([h for h, v in export_hourly.items() if v > 0.05])

    # EV charging hours
    ev_charging_hours = []
    if "ev" in result and "non_ev" in result:
        for h in sorted(result["ev"].keys()):
            if h in result["non_ev"]:
                diff = result["ev"][h]["avg_load_w"] - result["non_ev"][h]["avg_load_w"]
                if diff > 500:
                    ev_charging_hours.append(h)

    # Overnight SOC drain
    soc_drain = {}
    for label, day_set in [("non_ev", non_ev_days), ("ev", ev_days)]:
        subset = [r for r in rows if row_day(r) in day_set]
        evening = [r for r in subset if r["Hour"] in ("18:00", "19:00", "20:00")]
        morning = [r for r in subset if r["Hour"] in ("05:00", "06:00")]
        if evening and morning:
            eve_soc = mean([r["Max_SOC_Pct"] for r in evening])
            morn_soc = mean([r["Min_SOC_Pct"] for r in morning])
            soc_drain[label] = {
                "evening_soc": round(eve_soc, 0),
                "morning_soc": round(morn_soc, 0),
                "drain": round(eve_soc - morn_soc, 0),
            }

    return {
        "hourly": result,
        "peak_pv_hours": peak_pv_hours,
        "export_hours": export_hours,
        "ev_charging_hours": ev_charging_hours,
        "soc_drain": soc_drain,
    }


def compute_weekday_weekend(rows, non_ev_days):
    """3c2. Weekday vs weekend patterns (non-EV days only)."""
    non_ev_rows = [r for r in rows if row_day(r) in non_ev_days]
    if not non_ev_rows:
        return None

    def is_weekend(date_str):
        d = datetime.date.fromisoformat(date_str)
        return d.weekday() >= 5  # 5=Sat, 6=Sun

    weekday_days = {d for d in non_ev_days if not is_weekend(d)}
    weekend_days = {d for d in non_ev_days if is_weekend(d)}

    if not weekday_days or not weekend_days:
        return None

    by_day = group_by(non_ev_rows, row_day)

    result = {}
    for label, day_set in [("weekday", weekday_days), ("weekend", weekend_days)]:
        days_data = {d: drows for d, drows in by_day.items() if d in day_set and len(drows) > 20}
        if not days_data:
            continue
        daily_loads = [sum(r["Load_kWh"] for r in drows) for drows in days_data.values()]
        daily_pvs = [sum(r["PV_Energy_kWh"] for r in drows) for drows in days_data.values()]
        daily_imports = [sum(r["Grid_Import_kWh"] for r in drows) for drows in days_data.values()]
        daily_exports = [sum(r["Grid_Export_kWh"] for r in drows) for drows in days_data.values()]

        avg_load = mean(daily_loads)
        avg_import = mean(daily_imports)
        ss = (1 - avg_import / avg_load) * 100 if avg_load > 0 else 0

        # Hourly load profile
        all_rows_for_type = [r for d in days_data for r in days_data[d]]
        hourly_load = {}
        by_hour = group_by(all_rows_for_type, row_hour)
        for h, hrows in sorted(by_hour.items()):
            hourly_load[h] = round(mean([r["Load_W"] for r in hrows]), 0)

        result[label] = {
            "days": len(days_data),
            "avg_daily_load": round(avg_load, 1),
            "avg_daily_pv": round(mean(daily_pvs), 1),
            "avg_daily_import": round(avg_import, 1),
            "avg_daily_export": round(mean(daily_exports), 1),
            "self_sufficiency": round(ss, 0),
            "hourly_load_w": hourly_load,
        }

    # Find hours with significant difference (>200W)
    if "weekday" in result and "weekend" in result:
        sig_hours = []
        for h in result["weekday"]["hourly_load_w"]:
            if h in result["weekend"]["hourly_load_w"]:
                diff = result["weekend"]["hourly_load_w"][h] - result["weekday"]["hourly_load_w"][h]
                if abs(diff) > 200:
                    sig_hours.append({"hour": h, "diff_w": round(diff, 0)})
        result["significant_hourly_diffs"] = sig_hours

    return result


def compute_system_sizing(rows, pv_kwp, inverter_kw, non_ev_days, ev_days):
    """3d. System sizing and inverter check."""
    by_day = group_by(rows, row_day)
    daily_pv = [sum(r["PV_Energy_kWh"] for r in drows) for drows in by_day.values()]
    avg_daily_pv = mean(daily_pv)
    capacity_factor = avg_daily_pv / (pv_kwp * 24)
    peak_sun_hours = avg_daily_pv / pv_kwp

    max_pv_w = max(r["Avg_PV_W"] for r in rows)
    nameplate_w = pv_kwp * 1000
    inverter_ac_w = inverter_kw * 1000

    # Clipping checks
    panel_clip_hours = sum(1 for r in rows if r["Avg_PV_W"] > nameplate_w * 0.85)
    inverter_clip_hours = sum(1 for r in rows if r["Avg_PV_W"] > inverter_ac_w)
    inverter_limited = max_pv_w >= inverter_ac_w * 0.95

    # PV/load ratio for non-EV
    non_ev_rows = [r for r in rows if row_day(r) in non_ev_days]
    non_ev_by_day = group_by(non_ev_rows, row_day)
    non_ev_daily_pv = [sum(r["PV_Energy_kWh"] for r in d) for d in non_ev_by_day.values()]
    non_ev_daily_load = [sum(r["Load_kWh"] for r in d) for d in non_ev_by_day.values()]
    pv_load_ratio = mean(non_ev_daily_pv) / mean(non_ev_daily_load) if non_ev_daily_load and mean(non_ev_daily_load) > 0 else 0

    # Per-month breakdown
    by_month = group_by(rows, row_month)
    monthly_sizing = {}
    for m, mrows in sorted(by_month.items()):
        m_by_day = group_by(mrows, row_day)
        m_daily_pv = [sum(r["PV_Energy_kWh"] for r in d) for d in m_by_day.values()]
        m_avg = mean(m_daily_pv)
        m_total_load = sum(r["Load_kWh"] for r in mrows)
        m_total_import = sum(r["Grid_Import_kWh"] for r in mrows)
        grid_dep = (m_total_import / m_total_load * 100) if m_total_load > 0 else 0
        monthly_sizing[m] = {
            "avg_daily_pv": round(m_avg, 1),
            "peak_sun_hours": round(m_avg / pv_kwp, 1),
            "capacity_factor": round(m_avg / (pv_kwp * 24) * 100, 1),
            "grid_dependence": round(grid_dep, 0),
        }

    return {
        "avg_daily_pv": round(avg_daily_pv, 2),
        "capacity_factor": round(capacity_factor * 100, 1),
        "peak_sun_hours": round(peak_sun_hours, 2),
        "max_pv_w": round(max_pv_w, 0),
        "nameplate_w": nameplate_w,
        "inverter_ac_w": round(inverter_ac_w, 0),
        "inverter_kw": inverter_kw,
        "dc_ac_ratio": round(pv_kwp / inverter_kw, 2),
        "max_pv_pct_nameplate": round(max_pv_w / nameplate_w * 100, 0),
        "max_pv_pct_inverter": round(max_pv_w / inverter_ac_w * 100, 0),
        "panel_clip_hours": panel_clip_hours,
        "inverter_clip_hours": inverter_clip_hours,
        "inverter_limited": inverter_limited,
        "pv_load_ratio": round(pv_load_ratio, 2),
        "monthly": monthly_sizing,
    }


def compute_battery_analysis(rows, nominal_kwh, ev_days, non_ev_days):
    """3e. Battery analysis."""
    by_day = group_by(rows, row_day)

    # Usable capacity estimation via deepest monotonic SOC decline
    usable_estimates = []
    for day, drows in by_day.items():
        drows_sorted = sorted(drows, key=lambda r: r["Hour"])
        soc_vals = [r["Avg_SOC_Pct"] for r in drows_sorted]
        discharge_vals = [r["Battery_Discharge_kWh"] for r in drows_sorted]

        best_start = best_end = 0
        best_drop = 0
        i = 0
        while i < len(soc_vals) - 1:
            if soc_vals[i] > soc_vals[i + 1]:
                j = i + 1
                while j < len(soc_vals) - 1 and soc_vals[j] >= soc_vals[j + 1]:
                    j += 1
                drop = soc_vals[i] - soc_vals[j]
                if drop > best_drop:
                    best_drop = drop
                    best_start = i
                    best_end = j
                i = j
            else:
                i += 1

        if best_drop > 30:
            discharge_in_window = sum(discharge_vals[best_start : best_end + 1])
            if best_drop > 0 and discharge_in_window > 0:
                est = discharge_in_window / (best_drop / 100)
                usable_estimates.append(est)

    estimated_usable = median(usable_estimates) if usable_estimates else nominal_kwh * 0.9
    usable_pct = estimated_usable / nominal_kwh * 100

    # Daily charge/discharge
    daily_batt = {}
    for day, drows in by_day.items():
        ch = sum(r["Battery_Charge_kWh"] for r in drows)
        dis = sum(r["Battery_Discharge_kWh"] for r in drows)
        mx_soc = max(r["Max_SOC_Pct"] for r in drows)
        mn_soc = min(r["Min_SOC_Pct"] for r in drows)
        daily_batt[day] = {
            "charge": ch,
            "discharge": dis,
            "max_soc": mx_soc,
            "min_soc": mn_soc,
            "cycle_depth": dis / estimated_usable * 100 if estimated_usable > 0 else 0,
        }

    all_charges = [d["charge"] for d in daily_batt.values()]
    all_discharges = [d["discharge"] for d in daily_batt.values()]
    all_depths = [d["cycle_depth"] for d in daily_batt.values()]
    all_min_soc = [d["min_soc"] for d in daily_batt.values()]
    all_max_soc = [d["max_soc"] for d in daily_batt.values()]

    # Per-type stats
    type_stats = {}
    for label, day_set in [("non_ev", non_ev_days), ("ev", ev_days)]:
        subset = {d: v for d, v in daily_batt.items() if d in day_set}
        if subset:
            type_stats[label] = {
                "avg_charge": round(mean([v["charge"] for v in subset.values()]), 1),
                "avg_discharge": round(mean([v["discharge"] for v in subset.values()]), 1),
                "avg_cycle_depth": round(mean([v["cycle_depth"] for v in subset.values()]), 0),
            }

    # Monthly round-trip efficiency
    by_month = group_by(rows, row_month)
    monthly_efficiency = {}
    for m, mrows in sorted(by_month.items()):
        ch = sum(r["Battery_Charge_kWh"] for r in mrows)
        dis = sum(r["Battery_Discharge_kWh"] for r in mrows)
        eff = (dis / ch * 100) if ch > 0 else 0
        monthly_efficiency[m] = {
            "efficiency": round(eff, 1),
            "charge": round(ch, 1),
            "discharge": round(dis, 1),
        }

    # Avoidable import (daily upper-bound fallback)
    avoidable_total = 0
    for day, drows in by_day.items():
        day_import = sum(r["Grid_Import_kWh"] for r in drows)
        day_load = sum(r["Load_kWh"] for r in drows)
        day_pv = sum(r["PV_Energy_kWh"] for r in drows)
        theoretical_min = max(0, day_load - day_pv)
        avoidable_total += max(0, day_import - theoretical_min)

    num_days = len([d for d, drows in by_day.items() if len(drows) > 20])
    avg_avoidable = avoidable_total / num_days if num_days > 0 else 0

    return {
        "nominal_kwh": round(nominal_kwh, 1),
        "estimated_usable_kwh": round(estimated_usable, 1),
        "usable_pct": round(usable_pct, 0),
        "usable_estimate_days": len(usable_estimates),
        "avg_charge": round(mean(all_charges), 1),
        "avg_discharge": round(mean(all_discharges), 1),
        "avg_cycle_depth": round(mean(all_depths), 0),
        "avg_min_soc": round(mean(all_min_soc), 0),
        "avg_max_soc": round(mean(all_max_soc), 0),
        "type_stats": type_stats,
        "monthly_efficiency": monthly_efficiency,
        "avoidable_import_total": round(avoidable_total, 1),
        "avg_avoidable_per_day": round(avg_avoidable, 1),
    }


def compute_additional_panels(rows, current_kwp, additional_kwp, feedin_ratio, import_rate):
    """3f. Additional panels projection."""
    if additional_kwp <= 0:
        return None

    total_kwp = current_kwp + additional_kwp
    scale = total_kwp / current_kwp
    extra_self_consumed = 0
    extra_exported = 0

    for r in rows:
        extra_pv = r["PV_Energy_kWh"] * (scale - 1)
        if r["Grid_Import_kWh"] > 0:
            offset = min(extra_pv, r["Grid_Import_kWh"])
            extra_self_consumed += offset
            extra_exported += extra_pv - offset
        else:
            extra_exported += extra_pv

    by_day = group_by(rows, row_day)
    num_days = len([d for d, drows in by_day.items() if len(drows) > 20])

    return {
        "additional_kwp": additional_kwp,
        "total_kwp": total_kwp,
        "extra_self_consumed_total": round(extra_self_consumed, 1),
        "extra_exported_total": round(extra_exported, 1),
        "extra_self_consumed_daily": round(extra_self_consumed / num_days, 1) if num_days else 0,
        "extra_exported_daily": round(extra_exported / num_days, 1) if num_days else 0,
        "extra_daily_savings": round(
            (extra_self_consumed / num_days * import_rate
             + extra_exported / num_days * import_rate * feedin_ratio)
            if num_days else 0, 1
        ),
    }


def compute_peak_demand(rows, ev_days, non_ev_days, inverter_ac_w):
    """3g. Peak demand analysis."""
    # Peak grid draw when importing
    importing_rows = [r for r in rows if r["Grid_Energy_kWh"] < 0]
    if importing_rows:
        peak_import_row = max(importing_rows, key=lambda r: abs(r["Avg_Grid_W"]))
        peak_grid_draw_w = abs(peak_import_row["Avg_Grid_W"])
        peak_grid_date = peak_import_row["Date"]
        peak_grid_hour = peak_import_row["Hour"]
        peak_grid_is_ev = row_day(peak_import_row) in ev_days
    else:
        peak_grid_draw_w = 0
        peak_grid_date = peak_grid_hour = ""
        peak_grid_is_ev = False

    # Average daily peak grid draw by type
    by_day = group_by(rows, row_day)
    avg_peaks = {}
    for label, day_set in [("non_ev", non_ev_days), ("ev", ev_days)]:
        day_peaks = []
        for day in day_set:
            if day in by_day:
                imp_rows = [r for r in by_day[day] if r["Grid_Energy_kWh"] < 0]
                if imp_rows:
                    day_peaks.append(max(abs(r["Avg_Grid_W"]) for r in imp_rows))
        if day_peaks:
            avg_peaks[label] = round(mean(day_peaks), 0)

    # Peak PV output
    peak_pv_row = max(rows, key=lambda r: r["Avg_PV_W"])
    peak_pv_w = peak_pv_row["Avg_PV_W"]

    return {
        "peak_grid_draw_w": round(peak_grid_draw_w, 0),
        "peak_grid_draw_kw": round(peak_grid_draw_w / 1000, 1),
        "peak_grid_date": peak_grid_date,
        "peak_grid_hour": peak_grid_hour,
        "peak_grid_is_ev": peak_grid_is_ev,
        "avg_daily_peak_grid": avg_peaks,
        "peak_pv_w": round(peak_pv_w, 0),
        "peak_pv_kw": round(peak_pv_w / 1000, 1),
        "peak_pv_date": peak_pv_row["Date"],
        "peak_pv_hour": peak_pv_row["Hour"],
        "peak_pv_pct_inverter": round(peak_pv_w / inverter_ac_w * 100, 0) if inverter_ac_w > 0 else 0,
    }


def compute_anomalies(rows, ev_days, non_ev_days):
    """3h. Anomaly detection."""
    by_day = group_by(rows, row_day)
    sorted_days = sorted(by_day.keys())

    # --- PV anomalies ---
    daily_pv = {d: sum(r["PV_Energy_kWh"] for r in drows) for d, drows in by_day.items()}
    pv_anomalies = []
    for i, day in enumerate(sorted_days):
        if i < 3:
            continue
        # Rolling 14-day mean
        window_start = max(0, i - 14)
        window_days = sorted_days[window_start:i]
        ref_mean = mean([daily_pv[d] for d in window_days])
        if ref_mean > 0 and daily_pv[day] < ref_mean * 0.6:
            pv_anomalies.append({
                "date": day,
                "daily_pv": round(daily_pv[day], 1),
                "expected": round(ref_mean, 1),
                "deviation_pct": round((daily_pv[day] - ref_mean) / ref_mean * 100, 0),
            })

    # --- Load anomalies ---
    non_ev_loads = [
        sum(r["Load_kWh"] for r in by_day[d])
        for d in non_ev_days
        if d in by_day and len(by_day[d]) > 20
    ]
    load_anomalies = []
    if len(non_ev_loads) >= 5:
        load_mean = mean(non_ev_loads)
        load_std = stdev(non_ev_loads)
        threshold = load_mean + 2 * load_std
        for d in non_ev_days:
            if d in by_day and len(by_day[d]) > 20:
                day_load = sum(r["Load_kWh"] for r in by_day[d])
                if day_load > threshold:
                    load_anomalies.append({
                        "date": d,
                        "daily_load": round(day_load, 1),
                        "expected_mean": round(load_mean, 1),
                        "expected_std": round(load_std, 1),
                    })

    # --- Battery anomalies ---
    battery_anomalies = []
    for day, drows in by_day.items():
        drows_sorted = sorted(drows, key=lambda r: r["Hour"])
        if len(drows_sorted) < 20:
            continue
        start_soc = drows_sorted[0]["Avg_SOC_Pct"]
        end_soc = drows_sorted[-1]["Avg_SOC_Pct"]
        if abs(start_soc - end_soc) > 5:
            continue
        ch = sum(r["Battery_Charge_kWh"] for r in drows_sorted)
        dis = sum(r["Battery_Discharge_kWh"] for r in drows_sorted)
        if ch > 1:  # meaningful cycling
            eff = dis / ch * 100
            if eff < 80:
                battery_anomalies.append({
                    "date": day,
                    "efficiency": round(eff, 1),
                    "charge": round(ch, 1),
                    "discharge": round(dis, 1),
                })

    return {
        "pv": pv_anomalies,
        "load": load_anomalies,
        "battery": battery_anomalies,
    }


def compute_bill_impact(rows, tariff, feedin_ratio, monthly_totals):
    """3i. Bill impact estimate."""
    tariff_type = tariff.get("type", "flat")
    import_rate = tariff.get("import_rate", 0)

    def calc_flat(kwh):
        return kwh * import_rate

    def calc_tiered(kwh):
        tiers = tariff.get("tiers", [])
        if not tiers:
            return kwh * import_rate
        cost = 0
        remaining = kwh
        for i, tier in enumerate(tiers):
            thresh = tier.get("threshold", float("inf"))
            rate = tier.get("rate", import_rate)
            if i == 0:
                tier_kwh = min(remaining, thresh)
            else:
                prev_thresh = tiers[i - 1].get("threshold", 0)
                tier_kwh = min(remaining, thresh - prev_thresh)
            cost += tier_kwh * rate
            remaining -= tier_kwh
            if remaining <= 0:
                break
        if remaining > 0:
            cost += remaining * tiers[-1].get("rate", import_rate)
        return cost

    def calc_tou_monthly(mrows, rate_fn):
        """Apply TOU rates to hourly data."""
        tou = tariff.get("tou", {})
        peak_hours = set(tou.get("peak_hours", []))
        peak_rate = tou.get("peak_rate", import_rate)
        offpeak_rate = tou.get("offpeak_rate", import_rate)
        cost = 0
        for r in mrows:
            kwh = rate_fn(r)
            if r["Hour"] in peak_hours:
                cost += kwh * peak_rate
            else:
                cost += kwh * offpeak_rate
        return cost

    calc_fn = {"flat": calc_flat, "tiered": calc_tiered}.get(tariff_type, calc_flat)

    by_month = group_by(rows, row_month)
    monthly_bill = {}
    for m, mrows in sorted(by_month.items()):
        total_load = sum(r["Load_kWh"] for r in mrows)
        grid_import = sum(r["Grid_Import_kWh"] for r in mrows)
        grid_export = sum(r["Grid_Export_kWh"] for r in mrows)

        if tariff_type == "tou":
            without_solar = calc_tou_monthly(mrows, lambda r: r["Load_kWh"])
            with_solar = calc_tou_monthly(mrows, lambda r: r["Grid_Import_kWh"])
        else:
            without_solar = calc_fn(total_load)
            with_solar = calc_fn(grid_import)

        feedin_credit = grid_export * import_rate * feedin_ratio
        net_savings = without_solar - with_solar + feedin_credit

        days = monthly_totals[m]["days"] if m in monthly_totals else 30
        monthly_bill[m] = {
            "without_solar": round(without_solar, 0),
            "with_solar": round(with_solar, 0),
            "feedin_credit": round(feedin_credit, 0),
            "net_savings": round(net_savings, 0),
            "days": days,
        }

        # Tier reduction info for tiered tariffs
        if tariff_type == "tiered":
            tiers = tariff.get("tiers", [])
            if tiers:
                # Which tier without solar
                for i, t in enumerate(tiers):
                    if total_load <= t.get("threshold", float("inf")):
                        without_tier = i + 1
                        break
                else:
                    without_tier = len(tiers) + 1
                for i, t in enumerate(tiers):
                    if grid_import <= t.get("threshold", float("inf")):
                        with_tier = i + 1
                        break
                else:
                    with_tier = len(tiers) + 1
                monthly_bill[m]["without_tier"] = without_tier
                monthly_bill[m]["with_tier"] = with_tier

    # Annual projection
    total_days = sum(v["days"] for v in monthly_bill.values())
    total_without = sum(v["without_solar"] for v in monthly_bill.values())
    total_with = sum(v["with_solar"] for v in monthly_bill.values())
    total_credit = sum(v["feedin_credit"] for v in monthly_bill.values())
    total_savings = sum(v["net_savings"] for v in monthly_bill.values())

    annual_without = total_without / total_days * 365 if total_days > 0 else 0
    annual_with = total_with / total_days * 365 if total_days > 0 else 0
    annual_credit = total_credit / total_days * 365 if total_days > 0 else 0
    annual_savings = total_savings / total_days * 365 if total_days > 0 else 0

    return {
        "tariff_type": tariff_type,
        "monthly": monthly_bill,
        "annual_without_solar": round(annual_without, 0),
        "annual_with_solar": round(annual_with, 0),
        "annual_feedin_credit": round(annual_credit, 0),
        "annual_savings": round(annual_savings, 0),
        "annual_reduction_pct": round(
            (annual_savings / annual_without * 100) if annual_without > 0 else 0, 0
        ),
    }


def compute_roi(bill_impact, roi_config, annual_savings_override=None):
    """3j. ROI estimate with panel degradation."""
    if not roi_config:
        return None

    total_cost = roi_config.get("total_cost", 0)
    system_age = roi_config.get("system_age_years", 0)
    annual_savings = annual_savings_override or bill_impact.get("annual_savings", 0)

    if annual_savings <= 0:
        return {"error": "No savings to compute ROI"}

    # Find payback year with 0.5%/year degradation
    cumulative = 0
    payback_year = None
    yearly_savings = []
    for n in range(26):
        year_savings = annual_savings * (1 - 0.005) ** n
        yearly_savings.append(round(year_savings, 0))
        cumulative += year_savings
        if payback_year is None and cumulative >= total_cost:
            # Interpolate within the year
            prev_cum = cumulative - year_savings
            fraction = (total_cost - prev_cum) / year_savings if year_savings > 0 else 0
            payback_year = n + fraction

    lifetime_savings_25 = sum(
        annual_savings * (1 - 0.005) ** n for n in range(25)
    )

    daily_savings = annual_savings / 365

    return {
        "total_cost": total_cost,
        "system_age_years": system_age,
        "daily_savings": round(daily_savings, 1),
        "annual_savings_year1": round(annual_savings, 0),
        "simple_payback": round(payback_year, 1) if payback_year else None,
        "remaining_payback": round(max(0, payback_year - system_age), 1) if payback_year else None,
        "lifetime_savings_25yr": round(lifetime_savings_25, 0),
        "yearly_savings_sample": {
            "year_1": yearly_savings[0] if len(yearly_savings) > 0 else 0,
            "year_10": yearly_savings[9] if len(yearly_savings) > 9 else 0,
            "year_25": yearly_savings[24] if len(yearly_savings) > 24 else 0,
        },
    }


def compute_trends(monthly_totals, monthly_efficiency):
    """3k. Month-over-month trends."""
    months = sorted(monthly_totals.keys())
    if len(months) < 2:
        return None

    trends = []
    for i in range(1, len(months)):
        m1, m2 = months[i - 1], months[i]
        d1, d2 = monthly_totals[m1], monthly_totals[m2]

        avg_pv1 = d1["total_pv"] / d1["days"]
        avg_pv2 = d2["total_pv"] / d2["days"]
        avg_load1 = d1["total_load"] / d1["days"]
        avg_load2 = d2["total_load"] / d2["days"]
        gd1 = (d1["grid_import"] / d1["total_load"] * 100) if d1["total_load"] > 0 else 0
        gd2 = (d2["grid_import"] / d2["total_load"] * 100) if d2["total_load"] > 0 else 0

        eff1 = monthly_efficiency.get(m1, {}).get("efficiency", 0)
        eff2 = monthly_efficiency.get(m2, {}).get("efficiency", 0)

        trends.append({
            "from": m1,
            "to": m2,
            "avg_daily_pv": [round(avg_pv1, 1), round(avg_pv2, 1)],
            "avg_daily_pv_change_pct": round((avg_pv2 - avg_pv1) / avg_pv1 * 100, 0) if avg_pv1 > 0 else 0,
            "avg_daily_load": [round(avg_load1, 1), round(avg_load2, 1)],
            "avg_daily_load_change_pct": round((avg_load2 - avg_load1) / avg_load1 * 100, 0) if avg_load1 > 0 else 0,
            "self_sufficiency": [d1.get("self_sufficiency"), d2.get("self_sufficiency")],
            "self_sufficiency_change_pp": round(
                (d2.get("self_sufficiency", 0) or 0) - (d1.get("self_sufficiency", 0) or 0), 0
            ),
            "grid_dependence": [round(gd1, 0), round(gd2, 0)],
            "grid_dependence_change_pp": round(gd2 - gd1, 0),
            "battery_efficiency": [eff1, eff2],
            "battery_efficiency_change_pp": round(eff2 - eff1, 1),
        })

    return trends


def compute_battery_health(battery_analysis, system_age_years):
    """3l. Battery health indicators."""
    usable = battery_analysis["estimated_usable_kwh"]
    avg_discharge = battery_analysis["avg_discharge"]
    daily_cycles = avg_discharge / usable if usable > 0 else 0
    annual_cycles = daily_cycles * 365
    cycles_used = annual_cycles * system_age_years
    remaining_cycles = max(0, 6000 - cycles_used)
    remaining_years = remaining_cycles / annual_cycles if annual_cycles > 0 else float("inf")

    return {
        "usable_kwh": battery_analysis["estimated_usable_kwh"],
        "usable_pct": battery_analysis["usable_pct"],
        "nominal_kwh": battery_analysis["nominal_kwh"],
        "daily_equiv_cycles": round(daily_cycles, 2),
        "annual_cycles": round(annual_cycles, 0),
        "cycles_used": round(cycles_used, 0),
        "remaining_cycle_years": round(remaining_years, 0) if remaining_years != float("inf") else None,
        "monthly_efficiency": battery_analysis["monthly_efficiency"],
    }


def compute_annual_projection(monthly_totals, seasonal_factors, monthly_efficiency, sc_rate):
    """3m. Annual generation projection."""
    months = sorted(monthly_totals.keys())

    deseasonalized = []
    for m in months:
        month_num = str(int(m.split("-")[1]))
        factor = seasonal_factors.get(month_num, 1.0)
        avg_daily = monthly_totals[m]["total_pv"] / monthly_totals[m]["days"]
        deseasonalized.append(avg_daily / factor)

    baseline_daily = mean(deseasonalized)

    projected_annual_pv = sum(
        baseline_daily * seasonal_factors.get(str(m), 1.0) * 30.44
        for m in range(1, 13)
    )

    projected_self_consumed = projected_annual_pv * (sc_rate / 100) if sc_rate > 0 else 0
    projected_export = projected_annual_pv - projected_self_consumed

    confidence = "low" if len(months) < 3 else "moderate" if len(months) < 6 else "high"

    # Degradation
    year10 = projected_annual_pv * (1 - 0.005) ** 10
    year25 = projected_annual_pv * (1 - 0.005) ** 25

    return {
        "months_count": len(months),
        "confidence": confidence,
        "baseline_daily_pv": round(baseline_daily, 1),
        "projected_annual_pv": round(projected_annual_pv, 0),
        "projected_annual_self_consumed": round(projected_self_consumed, 0),
        "projected_annual_export": round(projected_export, 0),
        "projected_annual_pv_year10": round(year10, 0),
        "projected_annual_pv_year25": round(year25, 0),
        "deseasonalized_months": {
            m: {
                "avg_daily": round(monthly_totals[m]["total_pv"] / monthly_totals[m]["days"], 1),
                "factor": seasonal_factors.get(str(int(m.split("-")[1])), 1.0),
                "deseasonalized": round(d, 1),
            }
            for m, d in zip(months, deseasonalized)
        },
    }


def compute_best_worst_days(rows, ev_days):
    """3n. Best and worst days."""
    by_day = group_by(rows, row_day)
    stats = {}
    for day, drows in by_day.items():
        if len(drows) <= 20:
            continue
        pv = sum(r["PV_Energy_kWh"] for r in drows)
        load = sum(r["Load_kWh"] for r in drows)
        imp = sum(r["Grid_Import_kWh"] for r in drows)
        exp = sum(r["Grid_Export_kWh"] for r in drows)
        peak_soc = max(r["Max_SOC_Pct"] for r in drows)
        ss = (1 - imp / load) * 100 if load > 0 else 0
        stats[day] = {
            "pv": round(pv, 1),
            "load": round(load, 1),
            "grid_import": round(imp, 1),
            "grid_export": round(exp, 1),
            "peak_soc": round(peak_soc, 0),
            "self_sufficiency": round(ss, 0),
            "is_ev": day in ev_days,
        }

    if not stats:
        return None

    best = max(stats, key=lambda d: stats[d]["self_sufficiency"])
    worst = min(stats, key=lambda d: stats[d]["self_sufficiency"])

    return {
        "best": {"date": best, **stats[best]},
        "worst": {"date": worst, **stats[worst]},
    }


def compute_carbon_offset(projected_self_consumed, grid_emission_factor):
    """3o. Carbon offset estimate."""
    co2_kg = projected_self_consumed * grid_emission_factor
    return {
        "grid_emission_factor": grid_emission_factor,
        "annual_co2_avoided_kg": round(co2_kg, 0),
        "annual_co2_avoided_tonnes": round(co2_kg / 1000, 1),
        "equiv_trees": round(co2_kg / 22, 0),
        "equiv_km_driving": round(co2_kg / 0.21, 0),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Load config
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            config = json.load(f)
    else:
        config = json.load(sys.stdin)

    pv_kwp = config["pv_kwp"]
    inverter_kw = config.get("inverter_kw", pv_kwp / 1.3)
    nominal_kwh = config["battery_nominal_kwh"]
    has_ev = config.get("has_ev", False)
    feedin_ratio = config.get("feedin_ratio", 0.0)
    additional_kwp = config.get("additional_kwp", 0)
    seasonal_factors = config.get("seasonal_factors", {str(m): 1.0 for m in range(1, 13)})
    grid_emission_factor = config.get("grid_emission_factor", 0.5)
    tariff = config.get("tariff", {"type": "flat", "import_rate": 0})
    roi_config = config.get("roi")
    currency = config.get("currency", "$")
    system_age = roi_config.get("system_age_years", 0) if roi_config else 0

    data_dir = Path("data")
    rows, filenames = load_csv_files(data_dir)
    for r in rows:
        enrich(r)

    # 3a
    monthly_totals = compute_monthly_totals(rows)

    # 3b
    if has_ev:
        ev_days, non_ev_days, ev_info = detect_ev_days(rows)
    else:
        by_day = group_by(rows, row_day)
        all_days = set(d for d, drows in by_day.items() if len(drows) > 20)
        ev_days, non_ev_days, ev_info = set(), all_days, {"ev_day_count": 0, "non_ev_day_count": len(all_days), "total_full_days": len(all_days)}

    # 3c
    hourly = compute_hourly_patterns(rows, ev_days, non_ev_days)

    # 3c2
    weekday_weekend = compute_weekday_weekend(rows, non_ev_days)

    # 3d
    sizing = compute_system_sizing(rows, pv_kwp, inverter_kw, non_ev_days, ev_days)

    # 3e
    battery = compute_battery_analysis(rows, nominal_kwh, ev_days, non_ev_days)

    # 3f
    panels = compute_additional_panels(
        rows, pv_kwp, additional_kwp, feedin_ratio, tariff.get("import_rate", 0)
    )

    # 3g
    peak_demand = compute_peak_demand(rows, ev_days, non_ev_days, sizing["inverter_ac_w"])

    # 3h
    anomalies = compute_anomalies(rows, ev_days, non_ev_days)

    # 3i
    bill_impact = compute_bill_impact(rows, tariff, feedin_ratio, monthly_totals)

    # 3j
    roi = compute_roi(bill_impact, roi_config)

    # 3k
    trends = compute_trends(monthly_totals, battery["monthly_efficiency"])

    # 3l
    battery_health = compute_battery_health(battery, system_age)

    # Self-consumption rate for projection
    total_sc = sum(m["self_consumed"] for m in monthly_totals.values())
    total_pv = sum(m["total_pv"] for m in monthly_totals.values())
    sc_rate = (total_sc / total_pv * 100) if total_pv > 0 else 0

    # 3m
    projection = compute_annual_projection(
        monthly_totals, seasonal_factors, battery["monthly_efficiency"], sc_rate
    )

    # 3n
    best_worst = compute_best_worst_days(rows, ev_days)

    # 3o
    carbon = compute_carbon_offset(
        projection["projected_annual_self_consumed"], grid_emission_factor
    )

    # EV day detail metrics
    ev_detail = {}
    if has_ev and ev_days:
        by_day = group_by(rows, row_day)
        for label, day_set in [("ev", ev_days), ("non_ev", non_ev_days)]:
            ds = [d for d in day_set if d in by_day]
            if ds:
                ev_detail[label] = {
                    "avg_pv": round(mean([sum(r["PV_Energy_kWh"] for r in by_day[d]) for d in ds]), 1),
                    "avg_load": round(mean([sum(r["Load_kWh"] for r in by_day[d]) for d in ds]), 1),
                    "avg_import": round(mean([sum(r["Grid_Import_kWh"] for r in by_day[d]) for d in ds]), 1),
                    "avg_export": round(mean([sum(r["Grid_Export_kWh"] for r in by_day[d]) for d in ds]), 1),
                }
        # Evening SOC by type
        for label, day_set in [("non_ev", non_ev_days), ("ev", ev_days)]:
            subset = [r for r in rows if row_day(r) in day_set and r["Hour"] in ("18:00", "19:00", "20:00")]
            if subset:
                ev_detail.setdefault(label, {})["evening_soc"] = round(mean([r["Avg_SOC_Pct"] for r in subset]), 0)

    # Assemble output
    output = {
        "files": filenames,
        "total_rows": len(rows),
        "date_range": [min(r["Date"] for r in rows), max(r["Date"] for r in rows)],
        "unique_days": len(set(row_day(r) for r in rows)),
        "monthly_totals": monthly_totals,
        "ev_detection": ev_info,
        "hourly_patterns": hourly,
        "weekday_weekend": weekday_weekend,
        "system_sizing": sizing,
        "battery_analysis": battery,
        "additional_panels": panels,
        "peak_demand": peak_demand,
        "anomalies": anomalies,
        "bill_impact": bill_impact,
        "roi": roi,
        "trends": trends,
        "battery_health": battery_health,
        "annual_projection": projection,
        "best_worst_days": best_worst,
        "carbon_offset": carbon,
        "ev_detail": ev_detail,
        "self_consumption_rate": round(sc_rate, 1),
    }

    json.dump(output, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
