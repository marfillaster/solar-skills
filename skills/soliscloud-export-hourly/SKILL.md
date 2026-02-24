---
name: soliscloud-export-hourly
description: Use when the user asks to export SolisCloud solar data, download solar metrics, get power station data, create hourly solar summary, or mentions SolisCloud export/download. Supports monthly bulk export.
version: 4.0.0
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion, mcp__claude-in-chrome__tabs_context_mcp, mcp__claude-in-chrome__tabs_create_mcp, mcp__claude-in-chrome__navigate, mcp__claude-in-chrome__computer, mcp__claude-in-chrome__read_page, mcp__claude-in-chrome__find, mcp__claude-in-chrome__javascript_tool
---

# SolisCloud Monthly Export

Export a full month of operating data from SolisCloud and produce a single hourly CSV.

Two export methods are available:
- **API export** (primary) — direct REST API calls, no browser needed
- **Chrome export** (fallback) — browser automation via Chrome MCP tools

## Arguments

`$ARGUMENTS` — a month in `YYYY-MM` format. If not provided, use the current month.

## Steps

### 1. Determine target month

- If `$ARGUMENTS` contains a value matching `YYYY-MM`, use it. Otherwise use the current month (`date +%Y-%m`).
- Store as `TARGET_MONTH` (e.g. `2026-01`).

### 2. Choose export method

Check if the `SOLISCLOUD_API_KEY` environment variable is set:

```bash
echo "${SOLISCLOUD_API_KEY:-NOT_SET}"
```

- **If set** → proceed to **Step 3A (API export)**.
- **If not set** → ask the user with AskUserQuestion:

  **"SolisCloud API credentials are not configured. How would you like to proceed?"**

  Options:
  1. **Set up API access** — I'll guide you through getting API credentials (recommended, no browser needed)
  2. **Use Chrome fallback** — Export via browser automation (requires `--chrome` flag)

  - If **Set up API access**: follow the API setup guidance below, then tell the user to re-run `/soliscloud-export-hourly` after setting env vars.
  - If **Chrome fallback**: proceed to **Step 3B**.

#### API setup guidance

Walk the user through these steps:

**Request API access from Solis (one-time):**

1. Go to **https://solis-service.solisinverters.com/en/support/tickets/new**
2. Select **"API Access Request"** as the ticket type
3. Fill in your **SolisCloud account email address**
4. Submit the ticket — Solis will enable API access on your account (typically within a few business days)
5. API access is available for **end users** (not installers). It provides read-only data access, not remote control.

**Once API access is approved:**

6. Log into SolisCloud web UI, go to **https://www.soliscloud.com/#/apiManage** (under Basic Settings → API Management) → click **Activate**
7. Copy the **Key ID** (numeric string) and **Key Secret** — the secret is only shown once, save it immediately
8. Get your **Station ID**: go to plant details page — the 19-digit ID is in the URL (e.g. `.../station/stationdetail_1/123456789012345678`)
9. Get your **Inverter SN**: go to inverter details within the plant — the serial number is displayed on the page
10. Set environment variables in shell profile (`~/.zshrc`, `~/.bashrc`) or a `.env` file:

```bash
export SOLISCLOUD_API_KEY="your_key_id"
export SOLISCLOUD_API_SECRET="your_key_secret"
export SOLISCLOUD_STATION_ID="your_station_id"
export SOLISCLOUD_INVERTER_SN="your_inverter_sn"
```

11. Re-run `/soliscloud-export-hourly YYYY-MM`

---

## API Export Path

### 3A. Run API export

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/api_export.py TARGET_MONTH
```

This script:
- Reads credentials from `SOLISCLOUD_API_KEY`, `SOLISCLOUD_API_SECRET`, `SOLISCLOUD_STATION_ID`, `SOLISCLOUD_INVERTER_SN`
- Fetches 5-minute interval data for every day via `POST /v1/api/inverterDay`
- Aggregates into hourly buckets
- Writes `data/solar_hourly_YYYY-MM.csv`

### 4A. Display results

- Read the CSV file and display it to the user.
- Include the summary printed by the script (total days, total readings, total generation, any failures).
- Highlight key monthly observations (peak generation day, average daily production, battery cycling patterns).

---

## Output Schema

Both export paths produce identical CSV output. The solar-analysis skill depends on this exact schema.

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

**Power units:** The SolisCloud JSON API (`/api/chart/station/day/v2`) returns power values in watts despite `powerStr` labelling them as "kW". The Chrome export script uses these values directly. To verify: compare one day's `Avg_PV_W` against the SolisCloud dashboard's Operating Data chart — they should match without any ×1000 scaling.

---

## Chrome Fallback Path

### 3B. Pre-flight checks

Remind the user of requirements using AskUserQuestion:

> **Before we begin, please confirm:**
> - Claude Code is running with `--chrome` flag (required for browser automation)
> - You are logged into [SolisCloud](https://www.soliscloud.com) in Chrome

### 4B. Navigate to SolisCloud plant details

- Call `tabs_context_mcp` to find an existing SolisCloud tab, or create a new tab.
- Navigate to: `https://www.soliscloud.com/overview/plantStation`
- Click the first row in the Plant List table to open plant details.
- Wait for the Overview page to load (URL should contain `/details/overview/`).
- Extract the **Station ID** from the URL — the 19-digit number after `/overview/` (e.g. `1298491919450376600`). Store as `STATION_ID`.

### 5B. Navigate to any day in the target month

- The Operating Data panel has a date picker showing the current date and Day/Month/Year/Lifetime tabs.
- Ensure the **Day** tab is selected (orange text).
- Use the `<` and `>` arrows next to the date to navigate to any day within the target month.

### 6B. Install request capture interceptor

Read the script file and inject it via `javascript_tool`:

```bash
# Read the script
cat ${CLAUDE_PLUGIN_ROOT}/scripts/chrome_export.js
```

Before injecting, replace the template variables in the script content:
- `__TARGET_MONTH__` → actual `TARGET_MONTH` value (e.g. `2026-01`)
- `__STATION_ID__` → actual `STATION_ID` from step 4B

Inject the modified script via `javascript_tool`. This patches `XMLHttpRequest` to capture auth headers from the next chart API request.

Then click the **`<` date arrow** to trigger a `/api/chart/station/day/v2` request. This lets the interceptor capture the auth headers (Authorization, token, Content-MD5, etc.) from SolisCloud's own request.

### 7B. Verify captured headers

Check that headers were captured:

```javascript
JSON.stringify({ captured: !!window.__solis.capturedHeaders, keys: window.__solis.capturedHeaders ? Object.keys(window.__solis.capturedHeaders) : [] })
```

If `captured` is `false`, click the `<` arrow again and re-check. The interceptor auto-restores original XHR prototypes after the first capture.

### 8B. Bulk fetch all days

Trigger the bulk fetch:

```javascript
window.__solis.fetchAllDays().then(s => JSON.stringify(s))
```

This POSTs to `/api/chart/station/day/v2` for each day using the captured auth headers and returns JSON responses directly (no file downloads). Results are stored in `window.__solis.results`.

Poll progress if needed:

```javascript
JSON.stringify(window.__solis.fetchStatus)
```

### 9B. Remove existing download to prevent numeric suffix

Chrome appends a numeric suffix (e.g. `solar_hourly_2026-02 (1).csv`) when a file with the same name already exists in the Downloads folder. Delete any existing file before triggering the download:

```bash
rm -f ~/Downloads/solar_hourly_TARGET_MONTH.csv
```

Replace `TARGET_MONTH` with the actual value (e.g. `2026-01`).

### 10B. Process and download CSV

Generate the hourly CSV and trigger a browser download:

```javascript
JSON.stringify(window.__solis.generateCSV())
```

This aggregates the 5-minute JSON arrays into hourly buckets matching the output schema, then downloads `solar_hourly_TARGET_MONTH.csv` via blob URL.

### 11B. Move CSV to project and display results

- Move the downloaded CSV from the Downloads folder to `data/solar_hourly_TARGET_MONTH.csv`:

```bash
mkdir -p data && mv ~/Downloads/solar_hourly_TARGET_MONTH.csv data/solar_hourly_TARGET_MONTH.csv
```

Replace `TARGET_MONTH` with the actual value (e.g. `2026-01`).

- Read the CSV file and display it to the user.
- Include the stats returned by `generateCSV()` (total days, rows, PV generation).
- Highlight key monthly observations (peak generation day, average daily production, battery cycling patterns).
