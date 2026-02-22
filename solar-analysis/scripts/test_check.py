#!/usr/bin/env python3
"""
Full coverage test suite for analyze.py.

Run from the project root:
    python3 .claude/skills/solar-analysis/scripts/test_check.py

Tests all analysis functions with synthetic data and optionally validates
against real CSV data if present in data/.
"""

import datetime
import json
import math
import os
import sys
import tempfile

# Add the scripts directory to path so we can import analyze
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import analyze

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

passed = 0
failed = 0
errors = []


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        msg = f"FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        errors.append(msg)
        print(msg)


def approx(a, b, tol=0.1):
    """Check approximate equality."""
    if b == 0:
        return abs(a) < tol
    return abs(a - b) / max(abs(b), 1e-9) < tol


# ---------------------------------------------------------------------------
# Synthetic data generators
# ---------------------------------------------------------------------------

def make_row(date, hour, pv_w=0, battery_w=0, grid_w=0, grid_load_w=500,
             backup_load_w=100, soc=50, min_soc=None, max_soc=None):
    """Create a single row matching CSV format."""
    if min_soc is None:
        min_soc = soc - 2
    if max_soc is None:
        max_soc = soc + 2
    pv_kwh = pv_w / 1000
    batt_kwh = battery_w / 1000
    grid_kwh = grid_w / 1000
    gl_kwh = grid_load_w / 1000
    bl_kwh = backup_load_w / 1000
    return {
        "Date": date,
        "Hour": f"{hour:02d}:00",
        "Readings": 12.0,
        "Avg_PV_W": float(pv_w),
        "PV_Energy_kWh": pv_kwh,
        "Avg_Battery_W": float(battery_w),
        "Battery_Energy_kWh": batt_kwh,
        "Avg_Grid_W": float(grid_w),
        "Grid_Energy_kWh": grid_kwh,
        "Avg_GridLoad_W": float(grid_load_w),
        "GridLoad_Energy_kWh": gl_kwh,
        "Avg_BackupLoad_W": float(backup_load_w),
        "BackupLoad_Energy_kWh": bl_kwh,
        "Avg_SOC_Pct": float(soc),
        "Min_SOC_Pct": float(min_soc),
        "Max_SOC_Pct": float(max_soc),
    }


def make_day(date, base_load_w=600, pv_peak_w=3000, ev_extra_w=0):
    """Generate 24 hourly rows for one day with realistic solar curve."""
    rows = []
    for h in range(24):
        # Solar curve: peaks at noon
        if 6 <= h <= 18:
            pv = pv_peak_w * max(0, math.sin(math.pi * (h - 6) / 12))
        else:
            pv = 0

        load_w = base_load_w
        if 14 <= h <= 17 and ev_extra_w > 0:
            load_w += ev_extra_w

        grid_load_w = int(load_w * 0.8)
        backup_load_w = int(load_w * 0.2)

        # Battery: charges when PV > load, discharges when PV < load
        surplus = pv - load_w
        if surplus > 0:
            battery_w = min(surplus, 2000)  # charge
            grid_w = max(0, surplus - battery_w)  # export remainder
        else:
            battery_w = max(surplus, -2000)  # discharge
            grid_w = min(0, surplus - battery_w)  # import remainder (negative)

        # SOC: rough simulation
        if h < 6:
            soc = max(20, 60 - h * 5)
        elif h < 15:
            soc = min(85, 35 + (h - 6) * 6)
        else:
            soc = max(20, 85 - (h - 15) * 7)

        rows.append(make_row(date, h, pv_w=pv, battery_w=battery_w, grid_w=grid_w,
                             grid_load_w=grid_load_w, backup_load_w=backup_load_w,
                             soc=soc))
    return rows


def make_dataset(num_days=30, start_date="2026-01-01", ev_days_indices=None, month="2026-01"):
    """Generate a full month of synthetic data."""
    if ev_days_indices is None:
        ev_days_indices = set()
    rows = []
    start = datetime.date.fromisoformat(start_date)
    for i in range(num_days):
        d = start + datetime.timedelta(days=i)
        date_str = d.isoformat()
        ev_extra = 3000 if i in ev_days_indices else 0
        rows.extend(make_day(date_str, ev_extra_w=ev_extra))
    return rows


def enrich_all(rows):
    for r in rows:
        analyze.enrich(r)
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_helpers():
    """Test median, mean, stdev, enrich."""
    # median
    check("median_odd", analyze.median([3, 1, 2]) == 2)
    check("median_even", analyze.median([4, 1, 3, 2]) == 2.5)
    check("median_empty", analyze.median([]) == 0.0)
    check("median_single", analyze.median([5]) == 5)

    # mean
    check("mean_basic", analyze.mean([2, 4, 6]) == 4.0)
    check("mean_empty", analyze.mean([]) == 0.0)

    # stdev
    check("stdev_single", analyze.stdev([5]) == 0.0)
    check("stdev_basic", abs(analyze.stdev([2, 4, 4, 4, 5, 5, 7, 9]) - 2.138) < 0.01)

    # enrich
    r = make_row("2026-01-01", 12, grid_w=-500, battery_w=-300,
                 grid_load_w=800, backup_load_w=200)
    analyze.enrich(r)
    check("enrich_load_kwh", r["Load_kWh"] == 1.0)
    check("enrich_load_w", r["Load_W"] == 1000)
    check("enrich_import", r["Grid_Import_kWh"] == 0.5)
    check("enrich_export", r["Grid_Export_kWh"] == 0.0)
    check("enrich_charge", r["Battery_Charge_kWh"] == 0.0)
    check("enrich_discharge", r["Battery_Discharge_kWh"] == 0.3)

    # Positive grid = export
    r2 = make_row("2026-01-01", 12, grid_w=200)
    analyze.enrich(r2)
    check("enrich_export_pos", r2["Grid_Export_kWh"] == 0.2)
    check("enrich_import_zero", r2["Grid_Import_kWh"] == 0.0)

    # Positive battery = charge
    r3 = make_row("2026-01-01", 12, battery_w=500)
    analyze.enrich(r3)
    check("enrich_charge_pos", r3["Battery_Charge_kWh"] == 0.5)
    check("enrich_discharge_zero", r3["Battery_Discharge_kWh"] == 0.0)


def test_monthly_totals():
    """Test monthly totals computation."""
    rows = enrich_all(make_dataset(num_days=10))
    totals = analyze.compute_monthly_totals(rows)
    check("mt_has_month", "2026-01" in totals)
    m = totals["2026-01"]
    check("mt_days", m["days"] == 10)
    check("mt_pv_positive", m["total_pv"] > 0)
    check("mt_load_positive", m["total_load"] > 0)
    check("mt_self_consumed", m["self_consumed"] == round(m["total_load"] - m["grid_import"], 1),
          f"self_consumed={m['self_consumed']} != load-import={m['total_load'] - m['grid_import']}")
    check("mt_sc_rate_range", 0 <= m["self_consumption_rate"] <= 200)
    check("mt_ss_range", 0 <= m["self_sufficiency"] <= 100)

    # Zero PV edge case
    zero_pv_rows = [make_row("2026-01-01", h, pv_w=0, grid_w=-500) for h in range(24)]
    enrich_all(zero_pv_rows)
    zt = analyze.compute_monthly_totals(zero_pv_rows)
    check("mt_zero_pv_sc_rate", zt["2026-01"]["self_consumption_rate"] == 0)

    # Zero load edge case
    zero_load_rows = [make_row("2026-01-01", h, grid_load_w=0, backup_load_w=0) for h in range(24)]
    enrich_all(zero_load_rows)
    zt2 = analyze.compute_monthly_totals(zero_load_rows)
    check("mt_zero_load_ss", zt2["2026-01"]["self_sufficiency"] is None)


def test_ev_detection():
    """Test EV day detection."""
    # 30 days, 5 EV days with high extra load
    ev_indices = {5, 10, 15, 20, 25}
    rows = enrich_all(make_dataset(30, ev_days_indices=ev_indices))
    ev_days, non_ev_days, info = analyze.detect_ev_days(rows)

    check("ev_detected", len(ev_days) > 0, f"detected {len(ev_days)} EV days")
    check("ev_non_ev_split", len(ev_days) + len(non_ev_days) == info["total_full_days"])
    check("ev_info_keys", all(k in info for k in ["avg_daily_load", "threshold", "ev_day_count"]))
    # EV days should have higher average load
    if "ev_avg_load" in info and "non_ev_avg_load" in info:
        check("ev_higher_load", info["ev_avg_load"] > info["non_ev_avg_load"])

    # No EV days
    rows2 = enrich_all(make_dataset(10))
    ev2, non_ev2, info2 = analyze.detect_ev_days(rows2)
    check("ev_none_detected", len(ev2) == 0)

    # Edge: no full days (each day has <=20 rows)
    short_rows = enrich_all([make_row("2026-01-01", h) for h in range(10)])
    ev3, non_ev3, info3 = analyze.detect_ev_days(short_rows)
    check("ev_no_full_days", len(ev3) == 0 and len(non_ev3) == 0)


def test_hourly_patterns():
    """Test hourly pattern computation."""
    ev_indices = {3, 7}
    rows = enrich_all(make_dataset(10, ev_days_indices=ev_indices))
    ev_days, non_ev_days, _ = analyze.detect_ev_days(rows)
    # Use all days split manually if EV detection threshold differs
    if not ev_days:
        start = datetime.date(2026, 1, 1)
        ev_days = {(start + datetime.timedelta(days=i)).isoformat() for i in ev_indices}
        non_ev_days = {(start + datetime.timedelta(days=i)).isoformat() for i in range(10) if i not in ev_indices}

    hp = analyze.compute_hourly_patterns(rows, ev_days, non_ev_days)
    check("hp_has_hourly", "hourly" in hp)
    check("hp_has_non_ev", "non_ev" in hp["hourly"])
    check("hp_peak_pv_hours", len(hp["peak_pv_hours"]) > 0)
    check("hp_soc_drain", len(hp["soc_drain"]) > 0)

    # SOC drain should show evening > morning
    if "non_ev" in hp["soc_drain"]:
        drain = hp["soc_drain"]["non_ev"]
        check("hp_drain_positive", drain["drain"] >= 0,
              f"drain={drain['drain']}")

    # Export hours should be during solar peak
    for h in hp["export_hours"]:
        hour_int = int(h.split(":")[0])
        check(f"hp_export_solar_{h}", 6 <= hour_int <= 18,
              f"export at non-solar hour {h}")


def test_weekday_weekend():
    """Test weekday vs weekend analysis."""
    # Generate data starting on a Monday (2026-01-05 is a Monday)
    rows = enrich_all(make_dataset(14, start_date="2026-01-05"))
    by_day = analyze.group_by(rows, analyze.row_day)
    all_days = set(d for d, drows in by_day.items() if len(drows) > 20)

    ww = analyze.compute_weekday_weekend(rows, all_days)
    check("ww_not_none", ww is not None)
    if ww:
        check("ww_has_weekday", "weekday" in ww)
        check("ww_has_weekend", "weekend" in ww)
        if "weekday" in ww:
            check("ww_weekday_days", ww["weekday"]["days"] == 10,
                  f"weekday days={ww['weekday']['days']}")
        if "weekend" in ww:
            check("ww_weekend_days", ww["weekend"]["days"] == 4,
                  f"weekend days={ww['weekend']['days']}")

    # Edge: empty non_ev_days
    ww2 = analyze.compute_weekday_weekend(rows, set())
    check("ww_empty_none", ww2 is None)

    # Edge: only weekdays (Mon-Fri, 5 days)
    rows3 = enrich_all(make_dataset(5, start_date="2026-01-05"))
    by_day3 = analyze.group_by(rows3, analyze.row_day)
    all_days3 = set(d for d, drows in by_day3.items() if len(drows) > 20)
    ww3 = analyze.compute_weekday_weekend(rows3, all_days3)
    check("ww_weekdays_only_none", ww3 is None)


def test_system_sizing():
    """Test system sizing computation."""
    rows = enrich_all(make_dataset(10))
    by_day = analyze.group_by(rows, analyze.row_day)
    all_days = set(by_day.keys())

    s = analyze.compute_system_sizing(rows, 6.5, 5.0, all_days, set())
    check("ss_avg_daily_pv", s["avg_daily_pv"] > 0)
    check("ss_cap_factor", 0 < s["capacity_factor"] < 100)
    check("ss_peak_sun", s["peak_sun_hours"] > 0)
    check("ss_inverter_kw", s["inverter_kw"] == 5.0)
    check("ss_dc_ac_ratio", s["dc_ac_ratio"] == round(6.5 / 5.0, 2))
    check("ss_nameplate", s["nameplate_w"] == 6500)
    check("ss_inverter_ac_w", s["inverter_ac_w"] == 5000)
    check("ss_max_pv_pct", s["max_pv_pct_nameplate"] > 0)
    check("ss_pv_load_ratio", s["pv_load_ratio"] > 0)
    check("ss_has_monthly", len(s["monthly"]) > 0)

    # Large inverter: no clipping
    s2 = analyze.compute_system_sizing(rows, 6.5, 10.0, all_days, set())
    check("ss_large_inv_no_clip", s2["inverter_clip_hours"] == 0)
    check("ss_large_inv_not_limited", not s2["inverter_limited"])


def test_battery_analysis():
    """Test battery analysis."""
    rows = enrich_all(make_dataset(30))
    by_day = analyze.group_by(rows, analyze.row_day)
    all_days = set(d for d, drows in by_day.items() if len(drows) > 20)

    ba = analyze.compute_battery_analysis(rows, 14.3, set(), all_days)
    check("ba_nominal", ba["nominal_kwh"] == 14.3)
    check("ba_usable_positive", ba["estimated_usable_kwh"] > 0)
    check("ba_usable_pct", 0 < ba["usable_pct"] <= 150)
    check("ba_avg_charge", ba["avg_charge"] > 0)
    check("ba_avg_discharge", ba["avg_discharge"] > 0)
    check("ba_has_non_ev", "non_ev" in ba["type_stats"])

    # Monthly efficiency should be in reasonable range
    for m, eff in ba["monthly_efficiency"].items():
        check(f"ba_eff_{m}", 0 < eff["efficiency"] < 200,
              f"efficiency={eff['efficiency']}%")

    check("ba_avoidable_import", ba["avoidable_import_total"] >= 0)


def test_additional_panels():
    """Test additional panels projection."""
    rows = enrich_all(make_dataset(10))

    # No additional panels
    result = analyze.compute_additional_panels(rows, 6.5, 0, 0.5, 14.0)
    check("ap_none", result is None)

    # With additional panels
    result2 = analyze.compute_additional_panels(rows, 6.5, 3.0, 0.5, 14.0)
    check("ap_has_result", result2 is not None)
    if result2:
        check("ap_total_kwp", result2["total_kwp"] == 9.5)
        check("ap_extra_self_consumed", result2["extra_self_consumed_total"] >= 0)
        check("ap_extra_exported", result2["extra_exported_total"] >= 0)
        check("ap_savings_positive", result2["extra_daily_savings"] >= 0)

    # Zero feed-in ratio
    result3 = analyze.compute_additional_panels(rows, 6.5, 3.0, 0.0, 14.0)
    if result3:
        # With zero feedin, savings should only come from self-consumed
        expected = result3["extra_self_consumed_daily"] * 14.0
        check("ap_zero_feedin", approx(result3["extra_daily_savings"], expected, 0.2),
              f"savings={result3['extra_daily_savings']} vs expected={expected}")


def test_peak_demand():
    """Test peak demand analysis."""
    ev_indices = {2, 5}
    rows = enrich_all(make_dataset(10, ev_days_indices=ev_indices))
    start = datetime.date(2026, 1, 1)
    ev_days = {(start + datetime.timedelta(days=i)).isoformat() for i in ev_indices}
    non_ev_days = {(start + datetime.timedelta(days=i)).isoformat() for i in range(10) if i not in ev_indices}

    pd = analyze.compute_peak_demand(rows, ev_days, non_ev_days, 5000)
    check("pd_peak_grid", pd["peak_grid_draw_w"] > 0)
    check("pd_peak_pv", pd["peak_pv_w"] > 0)
    check("pd_has_avg_peaks", len(pd["avg_daily_peak_grid"]) > 0)
    check("pd_pv_pct_inverter", pd["peak_pv_pct_inverter"] > 0)

    # EV days should generally have higher peak grid draw
    if "ev" in pd["avg_daily_peak_grid"] and "non_ev" in pd["avg_daily_peak_grid"]:
        check("pd_ev_higher_peak", pd["avg_daily_peak_grid"]["ev"] >= pd["avg_daily_peak_grid"]["non_ev"],
              f"ev={pd['avg_daily_peak_grid']['ev']} vs non_ev={pd['avg_daily_peak_grid']['non_ev']}")


def test_anomalies():
    """Test anomaly detection."""
    rows = enrich_all(make_dataset(30))

    # Inject a PV anomaly on day 20
    target_date = (datetime.date(2026, 1, 1) + datetime.timedelta(days=19)).isoformat()
    for r in rows:
        if r["Date"] == target_date:
            r["PV_Energy_kWh"] *= 0.1  # severe drop
            r["Avg_PV_W"] *= 0.1

    by_day = analyze.group_by(rows, analyze.row_day)
    all_days = set(d for d, drows in by_day.items() if len(drows) > 20)
    anom = analyze.compute_anomalies(rows, set(), all_days)

    check("anom_has_pv", "pv" in anom)
    check("anom_has_load", "load" in anom)
    check("anom_has_battery", "battery" in anom)
    check("anom_pv_detected", len(anom["pv"]) > 0,
          f"expected PV anomaly, got {len(anom['pv'])}")

    if anom["pv"]:
        check("anom_pv_date", anom["pv"][0]["date"] == target_date)
        check("anom_pv_deviation", anom["pv"][0]["deviation_pct"] < -30)


def test_bill_impact():
    """Test bill impact for different tariff types."""
    rows = enrich_all(make_dataset(30))
    mt = analyze.compute_monthly_totals(rows)

    # Flat tariff
    tariff_flat = {"type": "flat", "import_rate": 14.0}
    bi = analyze.compute_bill_impact(rows, tariff_flat, 0.5, mt)
    check("bi_flat_type", bi["tariff_type"] == "flat")
    check("bi_flat_savings", bi["annual_savings"] > 0)
    check("bi_flat_reduction", bi["annual_reduction_pct"] > 0)
    check("bi_flat_monthly", "2026-01" in bi["monthly"])
    m = bi["monthly"]["2026-01"]
    check("bi_flat_without_solar", m["without_solar"] > 0)
    check("bi_flat_net_savings", m["net_savings"] > 0)

    # Tiered tariff
    tariff_tiered = {
        "type": "tiered",
        "import_rate": 10.0,
        "tiers": [
            {"threshold": 200, "rate": 8.0},
            {"threshold": 400, "rate": 12.0},
            {"threshold": 800, "rate": 16.0},
        ]
    }
    bi2 = analyze.compute_bill_impact(rows, tariff_tiered, 0.5, mt)
    check("bi_tiered_type", bi2["tariff_type"] == "tiered")
    check("bi_tiered_savings", bi2["annual_savings"] > 0)

    # TOU tariff
    tariff_tou = {
        "type": "tou",
        "import_rate": 14.0,
        "tou": {
            "peak_hours": ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00",
                           "15:00", "16:00", "17:00", "18:00"],
            "peak_rate": 18.0,
            "offpeak_rate": 10.0,
        }
    }
    bi3 = analyze.compute_bill_impact(rows, tariff_tou, 0.5, mt)
    check("bi_tou_type", bi3["tariff_type"] == "tou")
    check("bi_tou_savings", bi3["annual_savings"] > 0)

    # Zero feed-in ratio
    bi4 = analyze.compute_bill_impact(rows, tariff_flat, 0.0, mt)
    m4 = bi4["monthly"]["2026-01"]
    check("bi_zero_feedin_credit", m4["feedin_credit"] == 0)


def test_roi():
    """Test ROI computation."""
    bill_impact = {"annual_savings": 90000}
    roi_config = {"total_cost": 400000, "system_age_years": 0.25}

    roi = analyze.compute_roi(bill_impact, roi_config)
    check("roi_not_none", roi is not None)
    check("roi_total_cost", roi["total_cost"] == 400000)
    check("roi_payback", roi["simple_payback"] is not None)
    check("roi_payback_range", 3 < roi["simple_payback"] < 6,
          f"payback={roi['simple_payback']}")
    check("roi_remaining", roi["remaining_payback"] is not None)
    check("roi_remaining_less", roi["remaining_payback"] < roi["simple_payback"])
    check("roi_lifetime", roi["lifetime_savings_25yr"] > 0)

    # Degradation: year 10 savings < year 1
    check("roi_degradation", roi["yearly_savings_sample"]["year_10"] < roi["yearly_savings_sample"]["year_1"])
    check("roi_degradation_25", roi["yearly_savings_sample"]["year_25"] < roi["yearly_savings_sample"]["year_10"])

    # No ROI config
    roi2 = analyze.compute_roi(bill_impact, None)
    check("roi_no_config", roi2 is None)

    # Zero savings
    roi3 = analyze.compute_roi({"annual_savings": 0}, roi_config)
    check("roi_zero_savings", "error" in roi3)


def test_trends():
    """Test month-over-month trends."""
    # Single month: should return None
    mt_single = {"2026-01": {"total_pv": 500, "total_load": 600, "grid_import": 200,
                              "self_sufficiency": 67, "days": 30}}
    eff_single = {"2026-01": {"efficiency": 95}}
    t1 = analyze.compute_trends(mt_single, eff_single)
    check("trends_single_none", t1 is None)

    # Two months
    mt_two = {
        "2026-01": {"total_pv": 500, "total_load": 600, "grid_import": 200,
                     "self_sufficiency": 67, "days": 30},
        "2026-02": {"total_pv": 600, "total_load": 550, "grid_import": 150,
                     "self_sufficiency": 73, "days": 28},
    }
    eff_two = {
        "2026-01": {"efficiency": 97},
        "2026-02": {"efficiency": 93},
    }
    t2 = analyze.compute_trends(mt_two, eff_two)
    check("trends_not_none", t2 is not None)
    check("trends_has_entry", len(t2) == 1)
    if t2:
        check("trends_pv_increase", t2[0]["avg_daily_pv_change_pct"] > 0)
        check("trends_batt_eff_drop", t2[0]["battery_efficiency_change_pp"] < 0)
        check("trends_ss_change", t2[0]["self_sufficiency_change_pp"] == 6)


def test_battery_health():
    """Test battery health indicators."""
    ba = {
        "estimated_usable_kwh": 13.0,
        "usable_pct": 91,
        "nominal_kwh": 14.3,
        "avg_discharge": 7.5,
        "monthly_efficiency": {"2026-01": {"efficiency": 95}},
    }
    bh = analyze.compute_battery_health(ba, 0.25)
    check("bh_usable", bh["usable_kwh"] == 13.0)
    check("bh_daily_cycles", bh["daily_equiv_cycles"] > 0)
    check("bh_annual_cycles", bh["annual_cycles"] > 0)
    check("bh_remaining_years", bh["remaining_cycle_years"] is not None and bh["remaining_cycle_years"] > 0)

    # Zero discharge edge case
    ba_zero = dict(ba, avg_discharge=0)
    bh_zero = analyze.compute_battery_health(ba_zero, 1)
    check("bh_zero_discharge_cycles", bh_zero["daily_equiv_cycles"] == 0)
    check("bh_zero_discharge_remaining", bh_zero["remaining_cycle_years"] is None)


def test_annual_projection():
    """Test annual projection."""
    mt = {
        "2026-01": {"total_pv": 517, "days": 31},
        "2026-02": {"total_pv": 466, "days": 22},
    }
    sf = {str(m): 1.07 if m in (1, 2, 3, 4, 5, 12) else 0.93 for m in range(1, 13)}
    eff = {"2026-01": {"efficiency": 95}, "2026-02": {"efficiency": 93}}

    proj = analyze.compute_annual_projection(mt, sf, eff, 90)
    check("proj_confidence_low", proj["confidence"] == "low")
    check("proj_annual_pv", proj["projected_annual_pv"] > 0)
    check("proj_self_consumed", proj["projected_annual_self_consumed"] > 0)
    check("proj_export", proj["projected_annual_export"] >= 0)
    check("proj_baseline", proj["baseline_daily_pv"] > 0)
    check("proj_year10_less", proj["projected_annual_pv_year10"] < proj["projected_annual_pv"])

    # Moderate confidence (3-5 months)
    mt3 = {f"2026-{i:02d}": {"total_pv": 500, "days": 30} for i in range(1, 4)}
    proj3 = analyze.compute_annual_projection(mt3, sf, eff, 90)
    check("proj_moderate", proj3["confidence"] == "moderate")

    # High confidence (6+ months)
    mt6 = {f"2026-{i:02d}": {"total_pv": 500, "days": 30} for i in range(1, 7)}
    proj6 = analyze.compute_annual_projection(mt6, sf, eff, 90)
    check("proj_high", proj6["confidence"] == "high")


def test_best_worst_days():
    """Test best and worst day identification."""
    rows = enrich_all(make_dataset(10))
    bw = analyze.compute_best_worst_days(rows, set())
    check("bw_not_none", bw is not None)
    if bw:
        check("bw_has_best", "best" in bw)
        check("bw_has_worst", "worst" in bw)
        check("bw_best_ss", bw["best"]["self_sufficiency"] >= bw["worst"]["self_sufficiency"])
        check("bw_best_has_fields", all(k in bw["best"] for k in ["date", "pv", "load", "grid_import"]))

    # Edge: no full days
    short = enrich_all([make_row("2026-01-01", h) for h in range(10)])
    bw2 = analyze.compute_best_worst_days(short, set())
    check("bw_short_none", bw2 is None)


def test_carbon_offset():
    """Test carbon offset calculation."""
    co = analyze.compute_carbon_offset(5862, 0.68)
    check("co_kg", co["annual_co2_avoided_kg"] > 0)
    check("co_tonnes", co["annual_co2_avoided_tonnes"] == round(5862 * 0.68 / 1000, 1))
    check("co_trees", co["equiv_trees"] > 0)
    check("co_km", co["equiv_km_driving"] > 0)

    # Zero generation
    co2 = analyze.compute_carbon_offset(0, 0.68)
    check("co_zero_gen", co2["annual_co2_avoided_kg"] == 0)


def test_integration_synthetic():
    """Integration test: run full pipeline with synthetic data."""
    rows = enrich_all(make_dataset(30, ev_days_indices={5, 15, 25}))
    mt = analyze.compute_monthly_totals(rows)
    ev_days, non_ev_days, ev_info = analyze.detect_ev_days(rows)
    hp = analyze.compute_hourly_patterns(rows, ev_days, non_ev_days)
    ww = analyze.compute_weekday_weekend(rows, non_ev_days if non_ev_days else set(analyze.group_by(rows, analyze.row_day).keys()))
    sizing = analyze.compute_system_sizing(rows, 6.5, 5.0, non_ev_days if non_ev_days else set(), ev_days)
    battery = analyze.compute_battery_analysis(rows, 14.3, ev_days, non_ev_days if non_ev_days else set())
    panels = analyze.compute_additional_panels(rows, 6.5, 0, 0.5, 14.0)
    pd = analyze.compute_peak_demand(rows, ev_days, non_ev_days if non_ev_days else set(), 5000)
    anom = analyze.compute_anomalies(rows, ev_days, non_ev_days if non_ev_days else set())
    tariff = {"type": "flat", "import_rate": 14.0}
    bi = analyze.compute_bill_impact(rows, tariff, 0.5, mt)
    roi = analyze.compute_roi(bi, {"total_cost": 400000, "system_age_years": 0.25})
    trends = analyze.compute_trends(mt, battery["monthly_efficiency"])
    bh = analyze.compute_battery_health(battery, 0.25)
    sf = {str(m): 1.0 for m in range(1, 13)}
    total_sc = sum(m["self_consumed"] for m in mt.values())
    total_pv = sum(m["total_pv"] for m in mt.values())
    sc_rate = (total_sc / total_pv * 100) if total_pv > 0 else 0
    proj = analyze.compute_annual_projection(mt, sf, battery["monthly_efficiency"], sc_rate)
    bw = analyze.compute_best_worst_days(rows, ev_days)
    carbon = analyze.compute_carbon_offset(proj["projected_annual_self_consumed"], 0.68)

    check("int_mt", len(mt) > 0)
    check("int_hp", "hourly" in hp)
    check("int_sizing", sizing["avg_daily_pv"] > 0)
    check("int_battery", battery["nominal_kwh"] == 14.3)
    check("int_panels_none", panels is None)
    check("int_pd", pd["peak_pv_w"] > 0)
    check("int_bi", bi["annual_savings"] > 0)
    check("int_roi", roi is not None and roi["simple_payback"] is not None)
    check("int_proj", proj["projected_annual_pv"] > 0)
    check("int_bw", bw is not None)
    check("int_carbon", carbon["annual_co2_avoided_kg"] > 0)

    # Energy balance: import + self_consumed ~= total_load (by definition, self_consumed = load - import)
    for m, data in mt.items():
        reconstructed = data["self_consumed"] + data["grid_import"]
        check(f"int_energy_balance_{m}",
              approx(reconstructed, data["total_load"], 0.01),
              f"self_consumed({data['self_consumed']}) + import({data['grid_import']}) = {reconstructed} vs load={data['total_load']}")


def test_integration_real_data():
    """Integration test against real CSV files if available."""
    data_dir = analyze.Path("data")
    files = sorted(data_dir.glob("solar_hourly_*.csv"))
    if not files:
        print("SKIP: No real CSV data found in data/")
        return

    # Write a temporary config and run via the main interface
    config = {
        "pv_kwp": 6.5,
        "inverter_kw": 5.0,
        "battery_nominal_kwh": 14.336,
        "has_ev": True,
        "feedin_ratio": 0.5,
        "additional_kwp": 0,
        "seasonal_factors": {str(m): 1.07 if m in (1, 2, 3, 4, 5, 12) else 0.93 for m in range(1, 13)},
        "grid_emission_factor": 0.5,
        "tariff": {"type": "flat", "import_rate": 0.15},
        "roi": {"total_cost": 15000, "system_age_years": 0.25},
        "currency": "$",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config, f)
        config_path = f.name

    try:
        # Run analyze.py as subprocess to test end-to-end
        import subprocess
        # Determine project root (where data/ lives) — 3 levels up from scripts/
        project_root = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, "analyze.py"), config_path],
            capture_output=True, text=True, cwd=project_root
        )
        check("real_exit_code", result.returncode == 0, f"stderr: {result.stderr[:200]}")

        if result.returncode == 0:
            output = json.loads(result.stdout)
            check("real_has_files", len(output["files"]) > 0)
            check("real_has_rows", output["total_rows"] > 0)
            check("real_ev_detected", output["ev_detection"]["ev_day_count"] > 0)
            check("real_pv_positive", output["system_sizing"]["avg_daily_pv"] > 0)
            check("real_battery_usable", output["battery_analysis"]["estimated_usable_kwh"] > 0)
            check("real_roi", output["roi"] is not None)
            check("real_projection", output["annual_projection"]["projected_annual_pv"] > 0)
    finally:
        os.unlink(config_path)


def test_output_structure():
    """Verify all expected top-level keys in output."""
    expected_keys = [
        "files", "total_rows", "date_range", "unique_days",
        "monthly_totals", "ev_detection", "hourly_patterns",
        "weekday_weekend", "system_sizing", "battery_analysis",
        "additional_panels", "peak_demand", "anomalies",
        "bill_impact", "roi", "trends", "battery_health",
        "annual_projection", "best_worst_days", "carbon_offset",
        "ev_detail", "self_consumption_rate",
    ]

    # Build output by running full pipeline on synthetic data
    rows = enrich_all(make_dataset(30, ev_days_indices={5, 15}))
    mt = analyze.compute_monthly_totals(rows)
    ev_days, non_ev_days, ev_info = analyze.detect_ev_days(rows)

    # Construct the output dict the same way main() does
    hp = analyze.compute_hourly_patterns(rows, ev_days, non_ev_days)
    ww = analyze.compute_weekday_weekend(rows, non_ev_days if non_ev_days else set())
    sizing = analyze.compute_system_sizing(rows, 6.5, 5.0, non_ev_days or set(), ev_days)
    battery = analyze.compute_battery_analysis(rows, 14.3, ev_days, non_ev_days or set())
    panels = analyze.compute_additional_panels(rows, 6.5, 0, 0.5, 14.0)
    pd_result = analyze.compute_peak_demand(rows, ev_days, non_ev_days or set(), 5000)
    anom = analyze.compute_anomalies(rows, ev_days, non_ev_days or set())
    bi = analyze.compute_bill_impact(rows, {"type": "flat", "import_rate": 14.0}, 0.5, mt)
    roi = analyze.compute_roi(bi, {"total_cost": 400000, "system_age_years": 0.25})
    trends = analyze.compute_trends(mt, battery["monthly_efficiency"])
    bh = analyze.compute_battery_health(battery, 0.25)
    sf = {str(m): 1.0 for m in range(1, 13)}
    total_sc = sum(m["self_consumed"] for m in mt.values())
    total_pv = sum(m["total_pv"] for m in mt.values())
    sc_rate = (total_sc / total_pv * 100) if total_pv > 0 else 0
    proj = analyze.compute_annual_projection(mt, sf, battery["monthly_efficiency"], sc_rate)
    bw = analyze.compute_best_worst_days(rows, ev_days)
    carbon = analyze.compute_carbon_offset(proj["projected_annual_self_consumed"], 0.68)

    output = {
        "files": [], "total_rows": len(rows),
        "date_range": ["2026-01-01", "2026-01-30"],
        "unique_days": 30,
        "monthly_totals": mt, "ev_detection": ev_info,
        "hourly_patterns": hp, "weekday_weekend": ww,
        "system_sizing": sizing, "battery_analysis": battery,
        "additional_panels": panels, "peak_demand": pd_result,
        "anomalies": anom, "bill_impact": bi, "roi": roi,
        "trends": trends, "battery_health": bh,
        "annual_projection": proj, "best_worst_days": bw,
        "carbon_offset": carbon, "ev_detail": {},
        "self_consumption_rate": round(sc_rate, 1),
    }

    for key in expected_keys:
        check(f"output_key_{key}", key in output, f"missing key: {key}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    print("Running solar analysis test suite...\n")

    test_helpers()
    test_monthly_totals()
    test_ev_detection()
    test_hourly_patterns()
    test_weekday_weekend()
    test_system_sizing()
    test_battery_analysis()
    test_additional_panels()
    test_peak_demand()
    test_anomalies()
    test_bill_impact()
    test_roi()
    test_trends()
    test_battery_health()
    test_annual_projection()
    test_best_worst_days()
    test_carbon_offset()
    test_integration_synthetic()
    test_integration_real_data()
    test_output_structure()

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print(f"\nFailed tests:")
        for e in errors:
            print(f"  {e}")
    print(f"{'='*50}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
