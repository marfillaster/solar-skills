---
name: solar-analysis
description: Use when the user asks to analyze solar data, review solar performance, get solar recommendations, check system sizing, compare EV vs non-EV days, or assess ROI on their PV system.
version: 7.0.0
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

# Solar Analysis

Analyze hourly solar CSV data from `data/` and produce a consultant-style performance report with actionable recommendations.

## Arguments

`$ARGUMENTS` — None required. Reads all `solar_hourly_*.csv` files from `data/`.

## CSV format

Files: `data/solar_hourly_YYYY-MM.csv`

Columns: `Date, Hour, Readings, Avg_PV_W, PV_Energy_kWh, Avg_Battery_W, Battery_Energy_kWh, Avg_Grid_W, Grid_Energy_kWh, Avg_GridLoad_W, GridLoad_Energy_kWh, Avg_BackupLoad_W, BackupLoad_Energy_kWh, Avg_SOC_Pct, Min_SOC_Pct, Max_SOC_Pct`

Sign conventions:
- **Battery**: positive = charging, negative = discharging
- **Grid**: positive = export, negative = import
- **PV / Load**: always >= 0

## Steps

### 1. Find CSV data files

Use Glob to find all `data/solar_hourly_*.csv` files. If none exist, tell the user to export their solar data first (e.g., `/soliscloud-export-hourly YYYY-MM` for SolisCloud users).

### 2. Gather user parameters

Ask the user using AskUserQuestion:

1. **"What city/province are you in?"** — Used to infer latitude, seasonal irradiance profile, grid emission factor, and currency. No default — user must provide.
2. **"Do you have an EV or PHEV?"** — Yes / No
3. **"What is your PV system size in kWp?"** — No default. User must provide their system's nameplate DC capacity (e.g. 6.5 kWp, 10 kWp).
4. **"What is your inverter capacity in kW?"** — User provides the AC output rating printed on the inverter / I don't know (estimate from PV size). If unknown, estimate as `pv_kwp / 1.3`.
5. **"Do you have a battery?"** — Yes / No. If yes, ask: **"What is your battery capacity?"** — Provide in kWh (e.g. 13.3 kWh) / Provide as voltage + Ah (e.g. 51.2V × 280Ah). If voltage+Ah, compute: `kWh = voltage × Ah / 1000`. This is the **nominal** capacity; usable capacity is estimated from data in step 3e. If no battery, skip battery-related analysis sections (3e, 3l, Battery Health, battery portions of anomaly detection).
6. **"Is there room for additional panels?"** — No / Other (specify kWp)
7. **"What is your tariff structure?"** — Flat rate / Tiered/block rate (price increases with consumption) / Time-of-use (different rates by time of day). For all types, ask for the import rate per kWh. If tiered, also ask for tier thresholds and rates. If TOU, ask for peak/off-peak hours and rates.
8. **"Do you want an ROI estimate?"** — No / Yes (if yes, ask for: total setup cost (hint: if financed/loaned, include total financing cost such as interest) and system age in years. Infer currency from the user's location (Q1). Derive the import rate from Q7. If the system has a battery, also ask: **"What was the battery cost (included in total)?"** — this is needed to project ROI without battery.)
9. **"What is your feed-in tariff arrangement?"** — No feed-in (I don't get paid for export) / Feed-in at ~50% of import rate (Typical) / Other (specify ratio)

### 3. Run analysis

Build a JSON config from user parameters and run the analysis script. The script reads CSV files from `data/` and outputs all metrics as JSON.

#### 3.0 Run the analysis script

Build a config JSON with these keys from the user's answers:

```json
{
  "location": "Manila, Philippines",
  "pv_kwp": 6.5,
  "inverter_kw": 5.0,
  "has_battery": true,
  "battery_nominal_kwh": 14.336,
  "has_ev": true,
  "feedin_ratio": 0.5,
  "additional_kwp": 0,
  "seasonal_factors": {"1": 1.07, ...},
  "grid_emission_factor": 0.68,
  "tariff": {"type": "flat", "import_rate": 14},
  "roi": {"total_cost": 400000, "battery_cost": 100000, "system_age_years": 0.25},
  "currency": "₱"
}
```

Key notes:
- `location`: user's city/province from Q1. Used to infer `seasonal_factors`, `grid_emission_factor`, and `currency`.
- `inverter_kw`: user-provided inverter AC capacity in kW. If unknown, use `pv_kwp / 1.3` as fallback.
- `has_battery`: `true` if user has a battery, `false` otherwise. If `false`, set `battery_nominal_kwh` to `0` and skip battery analysis sections.
- `battery_nominal_kwh`: if user gave voltage + Ah, compute `voltage * Ah / 1000`
- `seasonal_factors`: infer from user's location/climate (see 3m below for factor tables)
- `grid_emission_factor`: infer from user's location (see 3o below for reference values)
- `tariff.type`: `"flat"`, `"tiered"`, or `"tou"`. `tariff.import_rate` comes from Q7.
- For tiered: add `tariff.tiers: [{"threshold": 200, "rate": 10}, {"threshold": 400, "rate": 12}, ...]`
- For TOU: add `tariff.tou: {"peak_hours": ["09:00", "10:00", ...], "peak_rate": 16, "offpeak_rate": 10}`
- `roi.battery_cost`: cost of battery portion (only if `has_battery` is true); used for "without battery" ROI projection
- `roi`: set to `null` if user declined ROI estimate

Write the config to `data/analysis_config.json`, then run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/solar-analysis/scripts/analyze.py data/analysis_config.json
```

The script outputs JSON to stdout with all computed metrics. Capture this output and use it to write the report in step 4.

The sections below document what the script computes. Use them as reference for interpreting the JSON output and writing the narrative report.

#### 3a. Monthly totals

For each month, compute:
- `total_pv = sum(PV_Energy_kWh)`
- `total_load = sum(GridLoad_Energy_kWh) + sum(BackupLoad_Energy_kWh)`
- `grid_export = sum(Grid_Energy_kWh where > 0)`
- `grid_import = abs(sum(Grid_Energy_kWh where < 0))`
- `battery_charge = sum(Battery_Energy_kWh where > 0)`
- `battery_discharge = abs(sum(Battery_Energy_kWh where < 0))`
- `self_consumed = total_load - grid_import` (measures actual solar offset of load; avoids inflating self-consumption by battery round-trip losses)
- `self_consumption_rate = self_consumed / total_pv` (guard: if `total_pv == 0`, report as 0%)
- `self_sufficiency = 1 - (grid_import / total_load)` (guard: if `total_load == 0`, report as N/A)

#### 3b. EV day detection (if has EV)

- Compute daily load for each full day (>20 hourly rows)
- Compute the EV detection threshold as `max(8, average_daily_load * 0.3)` kWh above mean — this catches both PHEV (smaller batteries) and full EV charging days relative to the household's baseline
- Flag days where `daily_load > average_daily_load + threshold` as EV days
- Do NOT infer EV battery capacity — just flag the days
- Split all subsequent analysis by EV vs non-EV days

#### 3c. Hourly patterns

- Compute average hourly values (PV, Battery, Grid, Load, SOC) across non-EV and EV days separately
- Identify: peak PV hours, peak load hours, EV charging hours (hours where EV-day load exceeds non-EV-day load by >500W), export hours, battery taper hours
- Compute overnight SOC drain (evening 18–20h Max_SOC_Pct → morning 05–06h Min_SOC_Pct)

#### 3c2. Weekday vs weekend patterns

- Classify each day as weekday (Mon–Fri) or weekend (Sat–Sun) using the date
- For non-EV days only (to isolate behavioral patterns from EV noise), compute:
  - Average daily load, PV generation, grid import, grid export, and self-sufficiency for weekdays vs weekends
  - Hourly load profile for weekdays vs weekends — identify significant differences (>200W sustained)
- Report which day type has higher self-sufficiency and why (e.g., "weekends have 15% higher self-sufficiency because daytime load is higher when occupants are home, better matching PV generation")
- Use this to tailor recommendations: "shift loads to solar hours" is more practical on weekends; weekday optimization may require timer-based automation

#### 3d. System sizing and inverter check

- `capacity_factor = avg_daily_pv / (pv_kwp * 24)`
- `peak_sun_hours = avg_daily_pv / pv_kwp`
- Peak PV output per day, max peak as % of nameplate
- **Inverter clipping check**: Use user-provided `inverter_kw` (AC output rating). Check hours where `Avg_PV_W > inverter_kw * 1000`. If max observed PV output is within 5% of inverter capacity, flag as likely inverter-limited. Report DC/AC ratio (`pv_kwp / inverter_kw`), panel nameplate, and inverter capacity.
- Clipping check against panel nameplate: hours where Avg_PV_W > 85% of nameplate
- PV/load ratio for non-EV baseline

#### 3e. Battery analysis

- Use user-provided nominal capacity (from question 3) as the nameplate kWh
- Estimate usable battery kWh: for each day, find the deepest monotonic SOC decline (longest continuous discharge without intermediate recharge). Compute `discharge_kwh_in_that_window / (soc_start - soc_end) * 100`. Take the median across days with >30% SOC swing in the deepest cycle. This avoids inflation from intra-day recharge cycles.
- Report both nominal and estimated usable capacity (usable is typically 80–95% of nominal depending on chemistry and SOC floor)
- Daily charge/discharge amounts, cycle depth as % of usable capacity
- Round-trip efficiency: `discharge / charge` — compute on monthly aggregates only (daily values are unreliable due to SOC imbalance between start and end of day)
- Avoidable import: compute hourly — `sum over hours of max(0, hourly_import - max(0, hourly_load - hourly_pv - hourly_battery_discharge_available))`. This captures the temporal mismatch that daily totals miss. If hourly battery state is too complex to model, fall back to daily `actual_import - max(0, load - pv)` but note it is an upper-bound estimate.

#### 3f. Additional panels (if extra capacity specified)

- Scale PV proportionally: `new_pv = pv * (total_kwp / current_kwp)`
- Check inverter headroom: if clipping was detected in 3d (either panel or inverter), note that additional panels will increase clipping losses and the projection is optimistic
- For each hour: if currently importing, extra PV offsets import first; remainder exported
- Compute extra self-consumed vs extra exported, value at feed-in ratio
- Note assumption: projection assumes same orientation/tilt as existing panels and sufficient inverter capacity

#### 3g. Peak demand analysis

- For each hour, record `abs(Avg_Grid_W)` when importing (Grid_Energy_kWh < 0) as hourly grid draw
- Find the peak hourly grid draw (max Avg_Grid_W when importing) across the entire dataset
- Report: peak grid draw in kW, date and hour it occurred, whether it was an EV day
- Compute average peak daily grid draw (max import hour per day, averaged across days), split by EV vs non-EV
- Compute peak PV output in kW and the hour/date it occurred
- If the user has a demand charge tariff, estimate the monthly demand charge impact

#### 3h. Anomaly detection

- **PV anomalies**: For each day, compare daily PV generation to the rolling 14-day mean for that day (or monthly mean if <14 days of prior data). Flag days where daily PV is <60% of the reference mean as potential anomalies. Exclude the first 3 days of data (insufficient baseline).
- **Load anomalies**: Flag non-EV days where daily load exceeds the non-EV mean by more than 2 standard deviations
- **Battery anomalies**: Flag days where round-trip efficiency (daily discharge/charge, only for days where SOC starts and ends within 5% of each other) falls below 80%
- For each anomaly, report the date, metric value, expected range, and possible explanation (weather for PV, unusual load for consumption, potential fault for battery)
- If no anomalies detected, state that system operation appears normal

#### 3i. Bill impact estimate

- **Without solar**: estimate what the electricity bill would be if all load came from grid
  - Flat rate: `total_load * import_rate`
  - Tiered: apply tier thresholds to total monthly load, sum each tier's contribution
  - TOU: split hourly load into peak/off-peak buckets using the user's TOU schedule, apply respective rates
- **With solar**: actual grid import cost
  - Flat rate: `grid_import * import_rate`
  - Tiered: apply tier thresholds to monthly grid import (solar reduces consumption into lower tiers — compute tier savings explicitly)
  - TOU: split hourly grid import into peak/off-peak, apply rates. Also compute feed-in credit from export during each period.
- **Bill savings** = without_solar - with_solar + feed-in_credit
- Report monthly and projected annual bill impact
- For tiered tariffs, highlight the "tier reduction benefit" — solar may push consumption into a lower tier, providing savings beyond the raw kWh offset

#### 3j. ROI estimate (if requested)

- `daily_savings` = from bill impact estimate (3i) if available, otherwise fall back to `self_consumed_kwh * import_rate + exported_kwh * import_rate * feedin_ratio`
- `annual_savings = daily_savings * 365`
- Apply panel degradation: `year_n_savings = annual_savings * (1 - 0.005)^n` (0.5%/year panel degradation)
- `cumulative_savings_at_year_n = sum(year_n_savings for n=0 to N)`
- `simple_payback` = year where cumulative savings exceeds total cost (accounting for degradation)
- `remaining_payback = max(0, simple_payback - system_age_years)`
- Report 25-year lifetime savings (with degradation) alongside simple payback
- **Without-battery projection** (if `has_battery` is true): compute a parallel ROI using `total_cost - battery_cost` as the investment. For savings, remove battery's contribution: assume all solar energy not consumed in real-time is exported (no evening/night self-consumption from battery), so `no_battery_savings = direct_solar_self_consumed * import_rate + (exported + battery_discharged_to_load) * import_rate * feedin_ratio`. Where `direct_solar_self_consumed` = hours where PV > 0: `min(pv, load)` summed. Compare payback periods side-by-side to show the battery's incremental ROI.
- Use detected currency for all monetary values

#### 3k. Month-over-month trends (if ≥2 months of data)

- Compare across months: avg daily PV, avg daily load, self-sufficiency, self-consumption rate, grid dependence, battery round-trip efficiency
- Flag significant changes (>10% shift) with likely explanation (seasonal, behavioral, degradation)
- If battery efficiency is declining month-over-month, flag as potential battery health concern

#### 3l. Battery health indicators

- `usable_capacity_pct = estimated_usable_kwh / nominal_kwh * 100` — compare to expected range for battery age
- Track round-trip efficiency per month; typical LFP is 92–95%, declining below 88% suggests degradation
- Estimate daily equivalent full cycles: `daily_discharge / usable_kwh`
- Estimate annual cycles: `daily_equiv_cycles * 365`
- At typical LFP rating of 6,000 cycles, estimate years of remaining cycle life: `(6000 - annual_cycles * system_age_years) / annual_cycles`
- If only 1 month of data, report baseline values and note that trends require more data

#### 3m. Annual generation projection

- Infer the user's latitude and climate from their location (Q1) to determine seasonal irradiance profile and apply monthly adjustment factors relative to annual mean:
  - **Tropical (< ~23° latitude)**: relatively flat curve; wet/dry season variation ~10–15%. Adjustment: wet months ×0.93, dry months ×1.07, transitional ×1.0
  - **Temperate (23°–50°)**: significant summer/winter variation. Adjustment by month relative to annual mean: Dec/Jan ×0.55, Feb/Nov ×0.65, Mar/Oct ×0.85, Apr/Sep ×1.05, May/Aug ×1.25, Jun/Jul ×1.35 (southern hemisphere: shift by 6 months)
  - **High latitude (> 50°)**: extreme seasonal variation. Adjustment: Dec/Jan ×0.25, Feb/Nov ×0.45, Mar/Oct ×0.75, Apr/Sep ×1.10, May/Aug ×1.45, Jun/Jul ×1.55 (southern hemisphere: shift by 6 months)
- Estimate annual daily average PV: weight each available month's avg daily PV by dividing out its seasonal factor, then average to get a de-seasonalized baseline, then apply all 12 monthly factors to project the full year
- Apply panel degradation for future years: `year_n_generation = projected_annual_pv * (1 - 0.005)^n`
- `projected_annual_pv = adjusted_avg_daily_pv * 365`
- `projected_annual_savings` = project from daily savings using the same seasonal adjustment
- Note the projection confidence: <3 months = low confidence, 3–6 months = moderate, 6+ months = high

#### 3n. Best and worst days

- Identify the day with highest self-sufficiency (lowest grid import as % of load) and the day with lowest
- For each, report: date, PV generation, load, grid import, grid export, peak SOC, whether it was an EV day
- Brief explanation of what made each day good or bad (e.g., high solar + low load, or cloudy + EV charging)

#### 3o. Carbon offset estimate

- Infer the grid emission factor from the user's location (Q1) (e.g., ~0.39 for US, ~0.23 for EU, ~0.61 for Australia, ~0.68 for Philippines — look up the appropriate value)
- `annual_co2_avoided = projected_annual_self_consumed * grid_emission_factor` (in kg, convert to tonnes)
- Express as equivalent: trees planted (~22 kg CO₂/tree/year), km driven (~0.21 kg CO₂/km for average car)

### 4. Write report

Produce a narrative markdown report. Use `~` for approximate values in prose. Write like a solar consultant — explain causal chains (what → why → impact), not just numbers.

**Style rules:**
- Narrative prose and bullet points, not raw data dumps
- Hourly patterns as 5–7 key insight bullets, NOT a 24-row table
- Recommendations as numbered subsections with multi-paragraph explanations: what's currently happening, why it's suboptimal, what to change, and quantified impact
- Use `~` prefix for rounded values in prose (e.g. "~21 kWh", "~85% SOC")
- Tables only where they add clarity: Key Metrics comparison, Capacity Factor summary
- Collapse low-signal sections: if weekday/weekend self-sufficiency differs by <5pp, summarize in one sentence instead of a full table. If environmental impact has no unusual findings, keep to 2–3 bullets without a dedicated section header — fold into the executive summary or annual projection.

**Report structure:**

The report follows this order, designed so a homeowner who reads only the first page gets the most important information:

1. Executive Summary (the "so what")
2. Alerts (anything requiring immediate attention)
3. Recommendations (what to do)
4. Supporting analysis (why we say what we say)
5. Technical appendix (detailed data for reference)

```markdown
# Solar System Recommendations

Based on analysis of solar data from {month range} ({x} days).

## Executive Summary

{3–5 lines maximum. Lead with the single most important finding, then key number, then top action. This should be readable in 15 seconds.}

{Example: "Your 6.5 kWp system is well-sized for household consumption and on track for a 4.5-year payback on $15k invested. Self-sufficiency improved from 60% to 73% as the dry season progressed. The single highest-impact optimization is shifting EV charging to morning hours (09:00–14:00), which could save an additional $400–700/year and reduce payback to ~3.8 years. No equipment faults detected, though two days of abnormally low generation warrant a check of inverter logs."}

{If anomalies requiring action were detected, mention them here: "Action needed: {date} showed near-zero PV output — check inverter logs to rule out equipment fault."}

{Include environmental headline if meaningful: "The system avoids ~{x} tonnes of CO₂ annually."}

## System Profile

- **PV capacity**: {kWp} kWp, inverter: {inverter_kw} kW AC (DC/AC ratio: {ratio})
- **Battery**: {nominal_kWh} kWh nominal, ~{usable_kWh} kWh usable (SOC range {min}%–{max}%)
- **EV/PHEV**: {present or not, charge frequency}
- **Tariff**: {flat / tiered / TOU} — {rate details}
- **Feed-in tariff**: {ratio}% of import rate

## Alerts

{Only include this section if anomalies were detected. If no anomalies, omit entirely — don't write "no alerts."}

{Prioritize equipment-related anomalies over weather. Anomalies that could indicate faults should be presented first with clear action items.}

### PV Generation Alerts

{If severe anomalies (>80% deviation) found:}
**Action required:** On {date}, PV generation was {x} kWh against an expected ~{x} kWh ({deviation}% below baseline). If this does not correspond to a known weather event or planned shutdown, check inverter logs and error codes for that date. Possible causes: inverter fault, tripped breaker, or grid outage preventing export.

{If moderate anomalies (40–80% deviation) found:}
| Date | Daily PV (kWh) | Expected (kWh) | Deviation |
|---|---|---|---|
| {date} | {x} | ~{x} | -{x}% |

{Narrative: "These days likely reflect heavy cloud cover or storms. If generation dips this severe recur without corresponding weather, investigate panel soiling or partial shading from new obstructions."}

### Load Alerts

{If load anomalies found:}
- {date}: {x} kWh consumed (expected ~{x} ± {x} kWh) — unusually high for a non-EV day. Possible causes: undetected EV charge below threshold, guest load, or appliance running abnormally. Worth investigating if this recurs.

### Battery Alerts

{If battery anomalies found:}
- {date}: round-trip efficiency {x}% (expected >85%) — may indicate BMS recalibration event or measurement error. Monitor for recurrence.

## Recommendations

### 1. {Highest impact action} (highest impact)

Paragraph 1: What's currently happening and its cost.
Paragraph 2: What to change, why it works, quantified benefit.
Paragraph 3: How to implement (e.g. EVSE scheduling, timer settings).

### 2. {Next action}

Similar multi-paragraph structure with data-backed reasoning.

(Continue numbered recommendations. Typical set: EV charge timing, overnight base load reduction, appliance scheduling, SOC floor assessment. Only include what the data supports. Tailor implementation advice to weekday vs weekend where relevant — e.g., "shifting loads is more practical on weekends when occupants are home; weekday optimization requires timer-based automation.")

### Not Recommended

{Fold into recommendations section as a closing subsection rather than a standalone top-level section.}

- **Grid-charging battery at off-peak**: {one-line explanation why it doesn't work with this tariff/efficiency}
- **Second battery**: {one-line explanation why export volume is too low to justify}

(Only include items relevant to the system.)

## Bill Impact

### Monthly Electricity Cost Comparison

| Month | Without Solar | With Solar | Feed-in Credit | Net Savings |
|---|---|---|---|---|
| {Mon} | {currency}{x} | {currency}{x} | {currency}{x} | {currency}{x} |

{For tiered tariffs: "Solar reduces your monthly consumption from tier {x} ({rate}/kWh) to tier {y} ({rate}/kWh), saving an additional {currency}{x}/month beyond the raw kWh offset."}

{For TOU tariffs: "Solar generation peaks during {peak/off-peak} hours, offsetting {currency}{x}/month of {peak rate} electricity. Export credit during {peak/off-peak} hours adds {currency}{x}/month."}

- Estimated annual bill without solar: {currency}{x}
- Estimated annual bill with solar: {currency}{x}
- **Annual bill reduction: {currency}{x} ({x}%)**

## ROI Estimate

(Only if user requested. Placed immediately after Bill Impact for financial flow.)

| Metric | With Battery | Without Battery |
|---|---|---|
| System cost | {currency}{total_cost} | {currency}{total_cost - battery_cost} |
| Estimated annual savings (year 1) | {currency}{amount} | {currency}{amount} |
| **Simple payback** | **{x} years** | **{x} years** |
| Remaining payback | {x} years | {x} years |
| 25-year lifetime savings | {currency}{amount} | {currency}{amount} |

(If no battery, use a single-column table with just the system values — no "Without Battery" column.)

**Battery incremental ROI**: The battery cost {currency}{battery_cost} adds {currency}{battery_annual_benefit}/year in avoided import (energy shifted from export at feed-in rate to self-consumption at import rate). Battery-only payback: ~{x} years. {Narrative: whether the battery investment is justified given cycle life, or whether the panels alone carry most of the ROI.}

Note: "Total system cost" is the user-reported figure, which may include financing costs (e.g., loan interest) beyond hardware and installation. If the user indicated financing, note this in the narrative — e.g., "System cost includes financing; hardware-only cost would yield a shorter payback."

Narrative: payback context relative to panel lifespan (25+ years) and battery lifespan (from cycle life estimate). Note that degradation-adjusted payback is slightly longer than a naive calculation. If recommendations are implemented, estimated improved payback.

## Key Metrics

| Metric | Non-EV Days | EV Days |
|---|---|---|
| Daily PV generation | ~{x} kWh | ~{x} kWh |
| Daily consumption | ~{x} kWh | ~{x} kWh |
| Daily grid import | ~{x} kWh | ~{x} kWh |
| Daily grid export | ~{x} kWh | ~{x} kWh |
| Evening SOC | ~{x}% | ~{x}% |

(If no EV, use single-column table.)

Followed by narrative bullets:
- Self-consumption rate: {x}% ({Mon}), {x}% ({Mon})
- Self-sufficiency: {x}% ({Mon}), {x}% ({Mon})
- Grid export concentrated at {HH:MM}–{HH:MM} when battery is full (~{x}% SOC)
- Battery drains from ~{x}% to ~{x}% overnight on non-EV days (~{x}% drain)
- On EV days, battery depletes to {x}% by {HH:MM}, forcing heavy grid import
- Non-EV baseline load is well-matched to PV generation (~{x} kWh each)

### Hourly Patterns

5–7 narrative bullets summarizing:
- PV peak window and wattage range
- Non-EV load peak timing (typically after PV declines)
- EV charging window and surge wattage
- Battery charge taper timing and SOC level causing export
- Overnight grid import window and average kWh/day

### Weekday vs Weekend

{If self-sufficiency difference is ≥5pp or load difference is ≥15%, show the full table:}

| Metric | Weekdays | Weekends |
|---|---|---|
| Avg daily load | ~{x} kWh | ~{x} kWh |
| Avg daily grid import | ~{x} kWh | ~{x} kWh |
| Self-sufficiency | {x}% | {x}% |

{Narrative explaining the difference and its implications for recommendations.}

{If the difference is <5pp and <15% load difference, collapse to a single line:}
Weekday and weekend consumption patterns are similar (~{x} kWh each, {x}% vs {x}% self-sufficiency), with the main difference being {brief note on hourly shift if significant_hourly_diffs exist, or "no significant hourly differences" if none}.

### Peak Demand

- Peak grid draw: {x} kW on {date} at {hour} ({EV/non-EV day})
- Average daily peak: ~{x} kW (non-EV), ~{x} kW (EV days)
- Peak PV output: {x} kW on {date} at {hour} ({x}% of inverter capacity)
- {If near inverter capacity (>90%): "Peak PV output reached {x}% of inverter AC capacity ({inverter_kw} kW), suggesting the inverter may be limiting output during peak hours."}

## System Size Assessment

One-line intro about panel room availability.

### PV Array ({kWp} kWp): {correctly sized | undersized | oversized} for base load

Bullet list:
- Peak output reached {x}W ({x}% of nameplate, {x}% of inverter capacity)
- {If inverter-limited: "Output appears capped by inverter AC capacity ({inverter_kw} kW). With a higher-rated inverter, peak generation could increase by ~{x}%."}
- Peak sun hours per month
- Non-EV PV/load ratio and what it means
- EV day coverage percentage and deficit
- Clipping hours (panel nameplate and inverter) and what they indicate

### Battery ({kWh} kWh): {adequate | undersized}, {not the bottleneck | stretched on EV days}

Bullet list:
- Non-EV cycle depth, charge/discharge per day, headroom assessment, avoidable import
- EV cycle depth and why more battery wouldn't help (deficit is generation)
- Round-trip efficiency observed

### Verdict

Single paragraph: is the system well-sized? What would/wouldn't help? Where does optimization lie?

## Battery Health

- Nominal capacity: {nominal_kWh} kWh, estimated usable: ~{usable_kWh} kWh ({usable_pct}% of nominal)
- Round-trip efficiency: {efficiency}% (typical LFP range: 92–95%)
- Daily equivalent full cycles: ~{cycles} ({annual_cycles} per year)
- Estimated cycle life remaining: ~{years} years at current usage (based on 6,000-cycle LFP rating)

If ≥2 months of data: note any efficiency trend. If efficiency is declining, flag for monitoring.
If only baseline data: note that trends will be trackable in future reports.

## Month-over-Month Trends

(Only if ≥2 months of data.)

| Metric | {Mon1} | {Mon2} | Change |
|---|---|---|---|
| Avg daily PV | ~{x} kWh | ~{x} kWh | {+/-x}% |
| Avg daily load | ~{x} kWh | ~{x} kWh | {+/-x}% |
| Self-sufficiency | {x}% | {x}% | {+/-x}pp |
| Grid dependence | {x}% | {x}% | {+/-x}pp |
| Battery efficiency | {x}% | {x}% | {+/-x}pp |

Narrative explaining observed changes (seasonal shift, behavioral change, etc.).

## Annual Projection

- Data coverage: {n} months ({confidence}: low <3mo, moderate 3–6mo, high 6+mo)
- Seasonal context: {season months in data}, adjustment factor applied: {x}
- Projected annual generation: ~{x} kWh (year 1), ~{x} kWh (year 10), ~{x} kWh (year 25)
- Projected annual self-consumed: ~{x} kWh
- Projected annual grid export: ~{x} kWh
- Environmental impact: ~{x} tonnes CO₂ avoided annually (at {grid_emission_factor} kg CO₂/kWh), equivalent to ~{x} trees planted

Narrative on expected seasonal variation appropriate to the user's climate (inferred from location).

## Appendix

### Best and Worst Days

**Best day: {date}** — PV: {x} kWh, Load: {x} kWh, Import: {x} kWh, Export: {x} kWh. {EV/non-EV}. {Brief explanation.} Self-sufficiency: {x}%.

**Worst day: {date}** — PV: {x} kWh, Load: {x} kWh, Import: {x} kWh, Export: {x} kWh. {EV/non-EV}. {Brief explanation.} Self-sufficiency: {x}%.

### Capacity Factor

| Month | Avg Daily kWh | Peak Sun Hours | Capacity Factor | Grid Dependence |
|---|---|---|---|---|

### Next Steps

- {If anomalies detected: "Check inverter logs for {date(s)} to rule out equipment faults"}
- Run this analysis again after {next month} to add wet/dry season data and improve projection confidence
- {If EV recommendation given: "Configure EVSE charging schedule per Recommendation 1 and compare next month's EV-day import"}
- {If overnight base load recommendation given: "Use a plug-in power meter to identify overnight loads and report findings for next analysis"}
- Monitor battery efficiency trend — current 93–97% range is healthy; flag if any month drops below 90%

### Assumptions and Limitations

- Self-consumed energy is calculated as `total_load - grid_import`, which measures actual solar offset of load and avoids inflating results by battery round-trip losses
- EV charging days are detected automatically using a threshold of {x} kWh above the {x} kWh daily average. Days near this threshold may be misclassified
- Annual projection uses {n} months of data ({confidence} confidence). Seasonal adjustment factors are estimates based on the user's climate zone and may not reflect local microclimate
- Panel degradation assumed at 0.5%/year (industry standard for monocrystalline silicon)
- Battery usable capacity estimated from observed deepest discharge cycles; actual usable capacity may differ from BMS-reported values
- Bill impact assumes consistent consumption patterns year-round; seasonal behavioral changes (e.g., air conditioning) are not modeled
- {If feedin_ratio > 0: "Feed-in tariff assumed constant; regulatory changes could affect export revenue"}

### Data Sources

- `data/{filename}` — {x} days
```

### 5. Display and save

- Show the report to the user
- Write it to `data/solar-analysis.md`
- Clean up `data/analysis_config.json`
