# Solar Analysis Skill

Analyzes hourly solar PV system data and produces a consultant-style performance report with actionable recommendations. Inverter/platform agnostic — works with any data source that exports to the expected CSV format.

## Usage

Invoke via Claude Code:

```
/solar-analysis
```

The skill reads all `data/solar_hourly_*.csv` files, asks for system parameters, runs the analysis, and writes a report to `data/solar-analysis.md`.

## Input Format

CSV files in `data/` named `solar_hourly_YYYY-MM.csv` with these columns:

```
Date, Hour, Readings, Avg_PV_W, PV_Energy_kWh, Avg_Battery_W, Battery_Energy_kWh,
Avg_Grid_W, Grid_Energy_kWh, Avg_GridLoad_W, GridLoad_Energy_kWh, Avg_BackupLoad_W,
BackupLoad_Energy_kWh, Avg_SOC_Pct, Min_SOC_Pct, Max_SOC_Pct
```

**Sign conventions:**
- Battery: positive = charging, negative = discharging
- Grid: positive = export, negative = import
- PV / Load: always >= 0

Each row represents one hour. A full day has 24 rows. Days with fewer than 21 rows are treated as partial and excluded from daily statistics.

## What It Computes

- **Monthly totals** — PV generation, consumption, grid import/export, self-consumption rate, self-sufficiency
- **EV day detection** — Flags high-consumption days as EV charging days using an adaptive threshold
- **Hourly patterns** — Average load, PV, battery, and SOC profiles for EV vs non-EV days
- **Weekday vs weekend** — Load pattern differences on non-EV days
- **System sizing** — Capacity factor, peak sun hours, DC/AC ratio, inverter clipping check
- **Battery analysis** — Usable capacity estimation, cycle depth, round-trip efficiency, avoidable import
- **Additional panels projection** — Marginal value of extra PV capacity
- **Peak demand** — Maximum grid draw and PV output with timestamps
- **Anomaly detection** — PV shortfalls (rolling 14-day baseline), load spikes (2-sigma), battery efficiency drops
- **Bill impact** — Supports flat, tiered, and time-of-use tariff structures
- **ROI** — Payback with 0.5%/year panel degradation, 25-year lifetime savings
- **Trends** — Month-over-month changes with significance flags
- **Battery health** — Cycle life estimation based on 6,000-cycle LFP rating
- **Annual projection** — De-seasonalized baseline with latitude-appropriate seasonal factors
- **Carbon offset** — Locale-inferred grid emission factor

## Report Structure

The report is ordered for a homeowner audience — most important information first:

1. **Executive Summary** — Key finding, payback number, top action, equipment alerts (readable in 15 seconds)
2. **Alerts** — Equipment faults and anomalies requiring attention (omitted if none)
3. **Recommendations** — Numbered, data-backed actions with quantified impact
4. **Bill Impact & ROI** — Financial analysis
5. **Key Metrics & Patterns** — Supporting data (hourly, weekday/weekend, peak demand)
6. **System Assessment** — PV and battery sizing evaluation
7. **Battery Health & Trends** — Degradation monitoring
8. **Annual Projection** — Generation forecast with confidence level
9. **Appendix** — Best/worst days, capacity factor table, next steps, assumptions, data sources

## Scripts

### `scripts/analyze.py`

Pure Python analysis script (no external dependencies). Takes a JSON config file as argument, reads CSV data from `data/`, outputs all metrics as JSON to stdout.

```bash
python3 scripts/analyze.py config.json
```

Config format:

```json
{
  "pv_kwp": 6.5,
  "inverter_kw": 8.0,
  "battery_nominal_kwh": 14.336,
  "has_ev": true,
  "feedin_ratio": 0.5,
  "additional_kwp": 0,
  "seasonal_factors": {"1": 1.07, "2": 1.07, "6": 0.93},
  "grid_emission_factor": 0.5,
  "tariff": {"type": "flat", "import_rate": 0.15},
  "roi": {"total_cost": 15000, "system_age_years": 0.25},
  "currency": "$"
}
```

### `scripts/test_check.py`

Full coverage test suite — 172 tests covering all analysis functions with synthetic data, edge cases, and integration tests against real data if present.

```bash
python3 scripts/test_check.py
```

## Version

7.0.0
