# AGENTS.md

## Project Overview

Claude Code skills for exporting and analyzing residential solar PV system data. Two independent skills share a common CSV format via the `data/` directory.

## Repository Layout

- `skills/analyze/` — Analysis skill (reads CSV, outputs report)
- `skills/export-hourly-soliscloud/` — Data export skill (fetches from SolisCloud API, writes CSV)
- `data/` — Shared data directory (CSVs in, report out). Not checked into git.

## Running Tests

```bash
python3 skills/analyze/scripts/test_check.py
```

172 tests, runs in a few seconds. Always run after modifying `analyze.py`. No test framework — uses `unittest` from stdlib.

## Dependencies

All core scripts use **Python stdlib only**. Do not introduce external dependencies.

## Working Directory

All scripts assume they are run from the **project root** (the directory containing `data/`). File paths like `data/solar_hourly_*.csv` are relative to this root.

## CSV Contract

Both skills must produce/consume the same 16-column CSV format. The canonical column list and sign conventions are defined in `skills/export-hourly-soliscloud/README.md` under "Output Format".

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
