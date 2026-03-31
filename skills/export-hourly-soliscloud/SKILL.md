---
name: export-hourly-soliscloud
description: Use when the user asks to export SolisCloud solar data, download solar metrics, get power station data, create hourly solar summary, or mentions SolisCloud export/download. Supports monthly bulk export.
version: 4.0.1
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion, mcp__plugin_chrome-devtools-mcp_chrome-devtools__list_pages, mcp__plugin_chrome-devtools-mcp_chrome-devtools__new_page, mcp__plugin_chrome-devtools-mcp_chrome-devtools__select_page, mcp__plugin_chrome-devtools-mcp_chrome-devtools__navigate_page, mcp__plugin_chrome-devtools-mcp_chrome-devtools__take_snapshot, mcp__plugin_chrome-devtools-mcp_chrome-devtools__click, mcp__plugin_chrome-devtools-mcp_chrome-devtools__wait_for, mcp__plugin_chrome-devtools-mcp_chrome-devtools__list_network_requests, mcp__plugin_chrome-devtools-mcp_chrome-devtools__get_network_request
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
  2. **Use Chrome fallback** — Export via browser automation (requires Chrome DevTools MCP plugin)

  - If **Set up API access**: follow the API setup guidance below, then tell the user to re-run `/export-hourly-soliscloud` after setting env vars.
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

11. Re-run `/export-hourly-soliscloud YYYY-MM`

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
- Do not analyze the exported data or add month-level observations unless the user separately asks for analysis.

---

## Output Schema

Both export paths produce identical CSV output. The analyze skill depends on this exact schema.

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

### 3B. Check auth cache

Try running the fetch script with cached auth first:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/chrome_fetch.py TARGET_MONTH
```

- If it prints `Using cached auth` and succeeds → skip to **Step 8B**.
- If it prints `Auth cache expired`, `No auth provided`, or exits with code 2 → proceed to **Step 4B** to capture fresh auth.

### 4B. Open SolisCloud in Chrome and navigate to plant details  *(skip if cache was valid)*

- Call `list_pages` to check for an existing SolisCloud tab (URL contains `soliscloud.com`).
- If found, call `select_page` to switch to it.
- If not found, call `new_page` then `navigate_page` to `https://www.soliscloud.com`.
- Call `take_snapshot` to check the current page state.
- If the page shows a login form (not the dashboard), ask the user via AskUserQuestion:

  > **Please log into SolisCloud in Chrome, then reply here to continue.**

- Once the user replies, call `wait_for` until the dashboard or plant list is visible before proceeding.

### 4B-2. Navigate to SolisCloud plant details

- Call `navigate_page` to `https://www.soliscloud.com/overview/plantStation`.
- Call `wait_for` until the Plant List table is visible, then `take_snapshot` to get element UIDs.
- Click the plant name in the first row of the Plant List table.
- The site opens plant details in a **new tab**. Call `list_pages` to find the new tab (URL contains `/details/overview/`), then `select_page` to switch to it. Close any duplicate tabs for the same URL.
- Extract the **Station ID** from the URL — the 19-digit number in the path (e.g. `.../details/overview/1298491919450376600`). Store as `STATION_ID`.

### 5B. Navigate to any day in the target month  *(skip if cache was valid)*

- Call `wait_for` until the Operating Data panel is visible.
- Call `take_snapshot` to locate the Day/Month/Year tab row and the `<` / `>` date arrows.
- If the **Day** tab is not already active, click it and wait for the chart to update.
- If the currently displayed date is not within `TARGET_MONTH`, click the `<` or `>` arrow until it is, waiting for the chart to update each time.

### 6B. Trigger a chart request and capture it  *(skip if cache was valid)*

Click the **`<` date arrow** to trigger a `/api/chart/station/day/v2` request.

Then find it in the network log:

```
list_network_requests (resourceTypes: xhr, fetch) → find the most recent /api/chart/station/day/v2 entry → note its reqid
```

Call `get_network_request(reqid)` to retrieve the full request headers and body.

### 7B. Run the Python bulk fetcher  *(with fresh auth)*

Parse the headers and body from the `get_network_request` result, then pipe them to the fetch script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/chrome_fetch.py <<'EOF'
{
  "target_month": "TARGET_MONTH",
  "headers": {
    "authorization": "...",
    "content-md5": "...",
    "time": "...",
    "token": "...",
    "device-id": "...",
    "x-cloud-platform": "...",
    "version": "...",
    "language": "...",
    "platform": "...",
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json;charset=UTF-8"
  },
  "body_template": { "id": "...", "money": "...", "timeZone": 8, "version": 1, "localTimeZone": 8, "language": "2" }
}
EOF
```

Fill in all values from the `get_network_request` output. The script fetches every day in `TARGET_MONTH`, saves auth to `data/.soliscloud_auth.json`, and writes `data/solar_hourly_TARGET_MONTH.csv`.

If the script exits with code 2 (auth rejected), the cache is stale — repeat steps 4B–7B.

### 8B. Display results

- Read the CSV and display it to the user.
- Include the stats printed by the script (days, rows, kWh).
- Do not analyze the exported data or add month-level observations unless the user separately asks for analysis.
