# AGENTS.md

## Project Overview

Claude Code skills for exporting and analyzing residential solar PV system data. Three skills share a common CSV format via the `data/` directory.

## Repository Layout

- `skills/analyze/` — Analysis skill (reads CSV, outputs report)
- `skills/export-hourly-soliscloud/` — Data export skill (fetches from SolisCloud API, writes CSV)
- `skills/export-hourly-deye/` — Data export skill (fetches from Solarman Open API for Deye inverters, writes CSV)
- `data/` — Shared data directory (CSVs in, report out). Not checked into git.

## Running Tests

```bash
python3 skills/analyze/scripts/test_check.py
```

180 tests, runs in a few seconds. Always run after modifying `analyze.py`. No test framework is configured; the suite is a custom Python script using stdlib only.

## Dependencies

All core scripts use **Python stdlib only**. Do not introduce external dependencies.

## Working Directory

All scripts default to a `data/` directory under the **current working directory**, so they are normally run from the **project root** (the directory containing `data/`). File paths like `data/solar_hourly_*.csv` are relative to this root.

Set the `SOLAR_DATA_DIR` environment variable to read and write the CSVs (and the SolisCloud auth cache) somewhere else — absolute, or relative to the current working directory. It is honored by `analyze.py`, both `api_export.py` exports, and `chrome_fetch.py`, so exports and analysis stay in sync. Useful for keeping the data in a synced/version-controlled folder outside the repo.

## CSV Contract

All export skills must produce, and the analysis skill must consume, the same 16-column CSV format. The canonical column list and sign conventions are defined in `skills/export-hourly-soliscloud/README.md` under "Output Format".

### Sign Conventions (critical)

- **Battery:** positive = charging, negative = discharging
- **Grid:** positive = exporting to grid, negative = importing from grid
- **PV and Load columns:** always >= 0

These conventions are used throughout the analysis code. Reversing them will produce silently wrong results.

## Code Style

- Python 3 with modern type hints (`list[dict]`, `str | None`)
- No classes — both scripts are functional style with module-level functions
- f-strings for formatting
- No linter or formatter is configured; match the existing style

## Key Design Constraints

- **Inverter agnostic:** The analysis skill must not contain SolisCloud-specific logic. It works with any data source that produces the expected CSV format.
- **No network calls in analysis:** `analyze.py` is a pure data-in/JSON-out script. All data fetching belongs in the export skill.
- **Homeowner audience:** Report text should explain causal chains (what → why → impact), not just present numbers. Recommendations must include quantified benefits.
