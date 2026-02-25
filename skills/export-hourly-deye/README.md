# Deye/Solarman Export Skill

Export monthly solar operating data from Deye inverters via the Solarman Open API into hourly CSV files for analysis.

**Version:** 1.0.0

## Overview

This skill fetches 5-minute interval data from a Deye inverter monitored via the Solarman cloud platform and aggregates it into hourly summaries. It uses the Solarman Open API directly — no browser automation needed.

| Method | Requirements | Speed |
|---|---|---|
| **API** | Environment variables with API credentials | ~15s per month |

The output CSV is identical in format to the SolisCloud export skill, so the analyze skill can process it directly.

## API Setup

### 1. Request API access from Solarman (one-time)

1. Email `service@solarmanpv.com` to request API access
2. Include your **Solarman Smart account email address** in the request
3. Solarman will reply with your `appId` and `appSecret` (typically within a few business days)

### 2. Gather station and device info

| Credential | Where to find it |
|---|---|
| **App ID** | Provided by Solarman via email |
| **App Secret** | Provided by Solarman via email |
| **Email** | Your Solarman Smart login email |
| **Password** | Your Solarman Smart login password |
| **Station ID** | Solarman Smart app → plant settings (optional — auto-discovered) |
| **Device SN** | Solarman Smart app → device list (optional — auto-discovered) |

### 3. Set environment variables

Add to `~/.zshrc`, `~/.bashrc`, or a `.env` file:

```bash
export SOLARMAN_APP_ID="your_app_id"
export SOLARMAN_APP_SECRET="your_app_secret"
export SOLARMAN_EMAIL="your_solarman_email"
export SOLARMAN_PASSWORD="your_solarman_password"

# Optional — auto-discovered if not set:
export SOLARMAN_STATION_ID="your_station_id"
export SOLARMAN_DEVICE_SN="your_device_sn"
```

If `SOLARMAN_STATION_ID` and `SOLARMAN_DEVICE_SN` are not set, the script will auto-discover them from your account. This works well if you have a single station with one inverter.

## Usage

```
/export-hourly-deye YYYY-MM
```

If no month is specified, uses the current month.

## Output Format

Output file: `data/solar_hourly_YYYY-MM.csv`

### Columns

| Column | Description |
|---|---|
| `Date` | Date (YYYY-MM-DD) |
| `Hour` | Hour bucket (HH:00) |
| `Readings` | Number of 5-min readings in this hour |
| `Avg_PV_W` | Average solar panel output (watts) |
| `PV_Energy_kWh` | Solar energy produced this hour |
| `Avg_Battery_W` | Average battery power (positive = charging, negative = discharging) |
| `Battery_Energy_kWh` | Battery energy flow this hour |
| `Avg_Grid_W` | Average grid power (positive = exporting, negative = importing) |
| `Grid_Energy_kWh` | Grid energy flow this hour |
| `Avg_GridLoad_W` | Average grid-connected load power |
| `GridLoad_Energy_kWh` | Grid load energy this hour |
| `Avg_BackupLoad_W` | Average backup load power (always 0 — not available from station-level data) |
| `BackupLoad_Energy_kWh` | Backup load energy this hour |
| `Avg_SOC_Pct` | Average battery state of charge (%) |
| `Min_SOC_Pct` | Minimum SOC in this hour |
| `Max_SOC_Pct` | Maximum SOC in this hour |

### Sign conventions

- **Battery:** positive = charging from PV/grid, negative = discharging to loads
- **Grid:** positive = exporting to grid, negative = importing from grid

### Field mapping

The Solarman station history API provides frame-level data with these fields:

| Solarman Field | CSV Column | Notes |
|---|---|---|
| `generationPower` | `PV(W)` | Always >= 0 |
| `batteryPower` | `Battery(W)` | Positive = charging, negative = discharging |
| `chargePower` / `dischargePower` | `Battery(W)` (fallback) | charge - discharge |
| `gridPower` - `purchasePower` | `Grid(W)` | Export - import |
| `usePower` | `Grid Load(W)` | Total consumption |
| `batterySoc` | `SOC(%)` | 0–100 |

## Scripts

| Script | Purpose |
|---|---|
| `scripts/api_export.py` | API-based export (stdlib only, no pip dependencies) |
