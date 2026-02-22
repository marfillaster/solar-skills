# Solar Skills

Claude Code skills for exporting and analyzing residential solar PV system data. Two skills work together to provide an end-to-end workflow: export hourly data from SolisCloud, then run a comprehensive analysis that produces a consultant-style report.

## Skills

### `/soliscloud-export-hourly`

Exports monthly 5-minute interval data from a SolisCloud-monitored solar+battery system and aggregates it into hourly CSV files.

- **Two export methods:** API (primary, ~15s/month) or Chrome browser automation (fallback)
- **Pure Python stdlib** — no pip dependencies for the API method
- Outputs standardized CSV with PV, battery, grid, load, and SOC columns

### `/solar-analysis`

Analyzes the exported hourly CSV data and produces a detailed performance report with actionable recommendations.

- Monthly totals, EV day detection, hourly patterns, weekday/weekend analysis
- System sizing evaluation, battery health, inverter clipping detection
- Bill impact with flat/tiered/TOU tariff support, ROI calculation
- Anomaly detection, annual projection, carbon offset estimation
- **180-test suite** included

## Installation

### Install the skills

Add both skills to your Claude Code project configuration (`.claude/settings.json`):

```json
{
  "skills": [
    "/path/to/solar-skills/soliscloud-export-hourly",
    "/path/to/solar-skills/solar-analysis"
  ]
}
```

Or install them from the GitHub repo:

```json
{
  "skills": [
    "https://github.com/marfillaster/solar-skills/tree/main/soliscloud-export-hourly",
    "https://github.com/marfillaster/solar-skills/tree/main/solar-analysis"
  ]
}
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
├── solar-analysis/
│   ├── SKILL.md              # Claude Code skill definition
│   ├── README.md             # Detailed documentation
│   └── scripts/
│       ├── analyze.py        # Analysis engine (pure stdlib)
│       └── test_check.py     # 180-test suite
└── soliscloud-export-hourly/
    ├── SKILL.md              # Claude Code skill definition
    ├── README.md             # Detailed documentation
    └── scripts/
        ├── api_export.py     # API-based export (pure stdlib)
        └── process_month.py  # XLS-to-CSV processor (requires xlrd)
```

## Requirements

- Python 3
- Claude Code (to run as skills)
- SolisCloud account with API access
- Optional: `xlrd` (only for Chrome fallback XLS processing)

## CSV Format

Both export methods produce identical output in `data/solar_hourly_YYYY-MM.csv` with 16 columns covering PV generation, battery flow, grid import/export, loads, and battery SOC. Sign conventions:

- **Battery:** positive = charging, negative = discharging
- **Grid:** positive = export, negative = import

See each skill's README for full details.
