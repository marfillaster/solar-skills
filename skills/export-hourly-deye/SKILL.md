---
name: export-hourly-deye
description: Use when the user asks to export Deye solar data, download Solarman metrics, get Deye power station data, create hourly solar summary, or mentions Deye/Solarman export/download. Supports monthly bulk export.
version: 1.0.0
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

# Deye/Solarman Monthly Export

Export a full month of operating data from a Deye inverter via the Solarman Open API and produce a single hourly CSV.

## Arguments

`$ARGUMENTS` — a month in `YYYY-MM` format. If not provided, use the current month.

## Steps

### 1. Determine target month

- If `$ARGUMENTS` contains a value matching `YYYY-MM`, use it. Otherwise use the current month (`date +%Y-%m`).
- Store as `TARGET_MONTH` (e.g. `2026-01`).

### 2. Check API credentials

Check if the `SOLARMAN_APP_ID` environment variable is set:

```bash
echo "${SOLARMAN_APP_ID:-NOT_SET}"
```

- **If set** → proceed to **Step 3 (Run API export)**.
- **If not set** → ask the user with AskUserQuestion:

  **"Solarman API credentials are not configured. Would you like me to guide you through the setup?"**

  Options:
  1. **Yes, guide me through API setup** — I'll walk you through getting credentials
  2. **I'll set them up myself** — Show me the required environment variables

  Then follow the API setup guidance below, and tell the user to re-run `/export-hourly-deye` after setting env vars.

#### API setup guidance

Walk the user through these steps:

**Request API access from Solarman (one-time):**

1. Email `service@solarmanpv.com` to request API access (appId and appSecret)
2. Include your Solarman Smart account email address in the request
3. Solarman will reply with your `appId` and `appSecret` (typically within a few business days)

**Gather station and device info:**

4. Open the **Solarman Smart** app on your phone
5. Find your **Station ID**: go to plant settings — the station ID is displayed on the plant info page
6. Find your **Device SN** (inverter serial number): go to device list within the plant — the serial number is displayed on the device page
7. Alternatively, if you leave `SOLARMAN_STATION_ID` and `SOLARMAN_DEVICE_SN` unset, the script will auto-discover them from your account (works if you have a single station)

**Set environment variables:**

8. Add to your shell profile (`~/.zshrc`, `~/.bashrc`) or a `.env` file:

```bash
export SOLARMAN_APP_ID="your_app_id"
export SOLARMAN_APP_SECRET="your_app_secret"
export SOLARMAN_EMAIL="your_solarman_email"
export SOLARMAN_PASSWORD="your_solarman_password"
# Optional — auto-discovered if not set:
export SOLARMAN_STATION_ID="your_station_id"
export SOLARMAN_DEVICE_SN="your_device_sn"
```

9. Re-run `/export-hourly-deye YYYY-MM`

---

### 3. Run API export

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/api_export.py TARGET_MONTH
```

This script:
- Authenticates via the Solarman Open API (SHA256-hashed password)
- Auto-discovers station ID and device SN if not configured
- Fetches 5-minute interval station data for every day via `/station/v1.0/history`
- Aggregates into hourly buckets
- Writes `data/solar_hourly_YYYY-MM.csv`

### 4. Display results

- Read the CSV file and display it to the user.
- Include the summary printed by the script (total days, total readings, total generation, any failures).
- Highlight key monthly observations (peak generation day, average daily production, battery cycling patterns).

---

## Output Schema

The CSV output is identical to the export-hourly-soliscloud skill, so the analyze skill can process it directly.

**File:** `data/solar_hourly_YYYY-MM.csv`

```
Date,Hour,Readings,Avg_PV_W,PV_Energy_kWh,Avg_Battery_W,Battery_Energy_kWh,Avg_Grid_W,Grid_Energy_kWh,Avg_GridLoad_W,GridLoad_Energy_kWh,Avg_BackupLoad_W,BackupLoad_Energy_kWh,Avg_SOC_Pct,Min_SOC_Pct,Max_SOC_Pct
```

| Column | Description |
|---|---|
| `Date` | Date (YYYY-MM-DD) |
| `Hour` | Hour bucket (HH:00) |
| `Readings` | Number of 5-min readings in this hour (typically 12) |
| `Avg_*_W` | Average watts for the hour |
| `*_Energy_kWh` | `avg_watts × readings × 5 / 60 / 1000` |
| `Avg_SOC_Pct` | Average battery state of charge (%) |
| `Min_SOC_Pct` / `Max_SOC_Pct` | SOC range within the hour |

### Sign conventions

- **Battery:** positive = charging, negative = discharging
- **Grid:** positive = exporting to grid, negative = importing from grid
