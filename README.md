# Solar Skills

Claude Code skills for exporting and AI-driven analysis of residential solar PV system data. Two skills work together to provide an end-to-end workflow: export hourly data from SolisCloud, then run an AI-driven analysis that produces a consultant-style report.

## Skills

### `/soliscloud-export-hourly`

Exports monthly 5-minute interval data from a SolisCloud-monitored solar+battery system and aggregates it into hourly CSV files.

- **Two export methods:** API (primary, ~15s/month) or Chrome browser automation (fallback)
- **Pure Python stdlib** — no pip dependencies for the API method
- Outputs standardized CSV with PV, battery, grid, load, and SOC columns

### `/solar-analysis`

AI-driven analysis of the exported hourly CSV data that produces a detailed performance report with actionable recommendations.

- Monthly totals, EV day detection, hourly patterns, weekday/weekend analysis
- System sizing evaluation, battery health, inverter clipping detection
- Bill impact with flat/tiered/TOU tariff support, ROI calculation
- Anomaly detection, annual projection, carbon offset estimation
- **172-test suite** included

## Analysis Coverage

The `/solar-analysis` report covers the following areas, written in a consultant narrative style with quantified findings and actionable guidance:

| Category | What it covers |
|---|---|
| **Monthly Totals** | PV generation, consumption, grid import/export, battery throughput, self-consumption and self-sufficiency rates |
| **EV Day Detection** | Adaptive threshold detection of EV/PHEV charging days; splits all subsequent analysis into EV vs non-EV patterns |
| **Hourly Profiles** | Average PV, battery, grid, load, and SOC by hour; identifies peak generation, peak load, export windows, and battery taper timing |
| **Weekday vs Weekend** | Load shape comparison, self-sufficiency differences, occupancy-driven optimization opportunities |
| **System Sizing** | PV capacity factor, inverter clipping detection, DC/AC ratio, PV-to-load ratio, sizing verdict (undersized / correct / oversized) |
| **Battery Health** | Usable vs nominal capacity, round-trip efficiency, daily cycle depth, annual cycle projection, remaining cycle life estimate |
| **Additional Panels** | Hour-by-hour simulation of extra PV capacity, marginal self-consumption vs export split, inverter headroom check |
| **Peak Demand** | Peak grid draw and PV output with timestamps, EV vs non-EV day comparison |
| **Anomaly Detection** | PV shortfalls vs rolling baseline, unusual load spikes, battery efficiency outliers |
| **Bill Impact** | Flat, tiered, or TOU tariff modelling; monthly cost table; with-vs-without-solar comparison; feed-in credits |
| **ROI Estimation** | Payback period with 0.5%/yr panel degradation, 25-year lifetime savings, with-vs-without-battery comparison |
| **Month-over-Month Trends** | PV, load, self-sufficiency, and battery efficiency trends across months; flags significant shifts |
| **Annual Projection** | Latitude-based seasonal adjustment, de-seasonalized baseline, generation forecast at years 1/10/25 |
| **Carbon Offset** | Locale-based grid emission factor, annual CO₂ avoided, tree and driving equivalents |
| **Best / Worst Days** | Highest and lowest self-sufficiency days with full metrics and causal explanation |
| **Recommendations** | Prioritized, multi-paragraph actions (EV charge timing, base load reduction, appliance scheduling, SOC floor, battery sizing, TOU arbitrage) with quantified savings |

## Question Flow

The `/solar-analysis` skill asks only 9 questions. Long intake forms cause users to abandon the process or guess at values they don't actually know, which degrades analysis quality. These questions are limited to facts the homeowner knows offhand (system size, battery capacity, electricity rate) that can't be reliably inferred from meter data alone. Everything else — self-consumption rate, battery efficiency, EV detection thresholds, anomaly baselines, seasonal patterns — is computed directly from the CSV data, which is more accurate than asking the owner to estimate.

```
1. What city/province are you in? — infers latitude, seasonal profile, grid emission factor, currency
2. Do you have an EV or PHEV? — Yes / No
3. What is your PV system size in kWp?
4. What is your inverter capacity in kW? — value or "I don't know" (estimates as PV ÷ 1.3)
5. Do you have a battery? — Yes / No
   ├─ Yes → What is your battery capacity? — kWh or Voltage × Ah
   └─ No → battery sections skipped
6. Is there room for additional panels? — No / specify kWp
7. What is your tariff structure?
   ├─ Flat → import rate
   ├─ Tiered → import rate + tier thresholds/rates
   └─ TOU → import rate + peak/off-peak hours/rates
8. Do you want an ROI estimate? — No / Yes
   └─ Yes → total cost, system age
        └─ if battery → battery cost (for with-vs-without comparison)
9. What is your feed-in tariff arrangement? — none / ~50% of import / specify ratio
```

## Installation

### Option A: Install as a plugin (recommended)

```
/plugin marketplace add marfillaster/solar-skills
/plugin install solar-skills@marfillaster-solar-skills
```

### Option B: Symlink into your project

```bash
mkdir -p .claude/skills
ln -s /path/to/solar-skills/plugin/skills/solar-analysis .claude/skills/solar-analysis
ln -s /path/to/solar-skills/plugin/skills/soliscloud-export-hourly .claude/skills/soliscloud-export-hourly
```

Once installed, the `/soliscloud-export-hourly` and `/solar-analysis` slash commands become available in Claude Code.

## Quick Start

### 1. Set up SolisCloud API credentials

Request API access from [Solis support](https://solis-service.solisinverters.com/en/support/tickets/new), then add to your shell profile:

```bash
export SOLISCLOUD_API_KEY="your_key_id"
export SOLISCLOUD_API_SECRET="your_key_secret"
export SOLISCLOUD_STATION_ID="your_station_id"
export SOLISCLOUD_INVERTER_SN="your_inverter_sn"
```

### 2. Export data

```
/soliscloud-export-hourly 2026-02
```

### 3. Run analysis

```
/solar-analysis
```

The analysis skill asks for system parameters interactively (PV size, battery capacity, tariff, etc.) and writes a report to `data/solar-analysis.md`.

## Project Structure

```
solar-skills/
├── .claude-plugin/
│   └── marketplace.json          # Marketplace manifest (source: ./plugin)
├── plugin/
│   ├── .claude-plugin/
│   │   └── plugin.json           # Plugin metadata
│   └── skills/
│       ├── solar-analysis/
│       │   ├── SKILL.md              # Claude Code skill definition
│       │   ├── README.md             # Detailed documentation
│       │   └── scripts/
│       │       ├── analyze.py        # Analysis engine (pure stdlib)
│       │       └── test_check.py     # 172-test suite
│       └── soliscloud-export-hourly/
│           ├── SKILL.md              # Claude Code skill definition
│           ├── README.md             # Detailed documentation
│           └── scripts/
│               ├── api_export.py     # API-based export (pure stdlib)
│               └── chrome_export.js  # Injectable JS for Chrome fallback
```

## Requirements

- Python 3
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- SolisCloud account with API access

### Claude Code authentication

Claude Code requires one of the following:

| Auth method | Export paths available |
|---|---|
| **Pro/Max subscription** | API export + Chrome fallback |
| **API key (pay-as-you-go)** | API export only |

To use an API key: sign up at [console.anthropic.com](https://console.anthropic.com/), add billing, generate a key, then set it in your shell profile:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

The Chrome fallback requires a Pro/Max subscription for MCP tool access (`--chrome` flag).

## CSV Format

Both export methods produce identical output in `data/solar_hourly_YYYY-MM.csv` with 16 columns covering PV generation, battery flow, grid import/export, loads, and battery SOC. Sign conventions:

- **Battery:** positive = charging, negative = discharging
- **Grid:** positive = export, negative = import

See each skill's README for full details.
