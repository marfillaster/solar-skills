# SolisCloud Export Skill

Export monthly solar operating data from SolisCloud into hourly CSV files without analyzing the resulting CSV unless asked separately.

**Version:** 4.2.0

## Overview

This skill fetches 5-minute interval data from a SolisCloud-monitored solar+battery system and aggregates it into hourly summaries. Two export methods are supported:

| Method | Requirements | Speed | Status |
|---|---|---|---|
| **API** (primary) | Environment variables with API credentials | ~15s per month | Untested — awaiting Solis API access approval |
| **Browser** (fallback) | cmux agent browser (preferred), Chrome DevTools MCP plugin, or Claude in Chrome, plus a browser login | ~30s per month | Working |

Both methods produce identical CSV output. The API method does not require an Anthropic Pro/Max subscription — only a direct Anthropic API key or equivalent.

## API Setup

### 1. Request API access from Solis (one-time)

API access must be enabled on your account by Solis support before you can activate it. It is available for **end users** (not installers) and provides read-only data access (not remote control).

1. Go to the [Solis Service Centre ticket page](https://solis-service.solisinverters.com/en/support/tickets/new)
2. Select **"API Access Request"** as the ticket type
3. Fill in your **SolisCloud account email address**
4. Submit — Solis will enable API access on your account (typically within a few business days)

### 2. Activate API and gather credentials

Once Solis has approved your request:

1. Log into [SolisCloud](https://www.soliscloud.com), go to **Basic Settings → API Management** (`https://www.soliscloud.com/#/apiManage`)
2. Click **Activate**

| Credential | Where to find it |
|---|---|
| **API Key ID** | API Management page — numeric string |
| **API Secret** | API Management page — shown once on activation, save immediately |
| **Station ID** | Plant details URL — 19-digit number (e.g. `123456789012345678`) |
| **Inverter SN** | Inverter details page within the plant |

### 3. Set environment variables

Add to `~/.zshrc`, `~/.bashrc`, or a `.env` file:

```bash
export SOLISCLOUD_API_KEY="your_key_id"
export SOLISCLOUD_API_SECRET="your_key_secret"
export SOLISCLOUD_STATION_ID="your_station_id"
export SOLISCLOUD_INVERTER_SN="your_inverter_sn"
```

Optional: `SOLISCLOUD_TIMEZONE` — integer UTC offset (e.g. `8` for UTC+8). Defaults to system timezone.

## Browser Fallback Requirements

If API credentials aren't configured, the skill falls back to browser automation. It prefers the **cmux agent browser**, then Chrome DevTools MCP, then Claude in Chrome:

| Backend | Requirements | How auth is captured |
|---|---|---|
| **cmux** (preferred) | [cmux](https://cmux.com) installed with the browser panel enabled (`cmux browser-status` → `enabled`), logged into SolisCloud in the cmux browser | `cmux browser <surface> cookies get --all` returns the httpOnly `token` cookie directly |
| **Chrome DevTools MCP** | [Chrome DevTools MCP plugin](https://github.com/ChromeDevTools/chrome-devtools-mcp) installed and active, logged into SolisCloud in Chrome | Capture a `/api/chart/station/day/v2` request and read its `Cookie` header |
| **Claude in Chrome** | The Claude in Chrome extension, logged into SolisCloud in Chrome | Not captured: `chrome_fetch.py --serve` signs the requests, the page sends them with its own cookie and posts the responses back to `127.0.0.1` |

cmux runs the whole path through the `cmux browser` CLI (Bash only, no MCP tools). Its WKWebView engine does not support network-request inspection, so the cmux path reads the session cookie from the cookie store instead — that plus the station ID from the plant details URL is all `chrome_fetch.py` needs.

The station ID is kept in the auth cache (`data/.soliscloud_auth.json`) and reused after the cookie expires. The cookie itself expires after 4 hours, so monthly runs usually need a fresh login; only the API method runs unattended.

## Usage

```
/export-hourly-soliscloud YYYY-MM
```

If no month is specified, uses the current month.

## Output Format

Output file: `data/solar_hourly_YYYY-MM.csv` (set `SOLAR_DATA_DIR` to write elsewhere)

### Columns

| Column | Description |
|---|---|
| `Date` | Date (YYYY-MM-DD) |
| `Hour` | Hour bucket (HH:00) |
| `Readings` | Number of 5-min readings in this hour |
| `Avg_PV_W` | Average solar panel output (watts) |
| `PV_Energy_kWh` | Solar energy produced this hour |
| `Avg_Battery_W` | Average battery power (negative = discharging, positive = charging) |
| `Battery_Energy_kWh` | Battery energy flow this hour |
| `Avg_Grid_W` | Average grid power (negative = importing, positive = exporting) |
| `Grid_Energy_kWh` | Grid energy flow this hour |
| `Avg_GridLoad_W` | Average grid-connected load power |
| `GridLoad_Energy_kWh` | Grid load energy this hour |
| `Avg_BackupLoad_W` | Average backup load power |
| `BackupLoad_Energy_kWh` | Backup load energy this hour |
| `Avg_SOC_Pct` | Average battery state of charge (%) |
| `Min_SOC_Pct` | Minimum SOC in this hour |
| `Max_SOC_Pct` | Maximum SOC in this hour |

### Sign conventions

- **Battery**: positive = charging from PV/grid, negative = discharging to loads
- **Grid**: positive = exporting to grid, negative = importing from grid

## Scripts

| Script | Purpose |
|---|---|
| `scripts/api_export.py` | API-based export (stdlib only, no pip dependencies) |
| `scripts/chrome_fetch.py` | Browser fallback bulk fetcher — takes a captured SolisCloud session cookie and station ID (from cmux or DevTools MCP), re-signs daily web requests in Python, writes CSV. With `--serve`, signs requests for a logged-in page and collects its responses over 127.0.0.1 instead (Claude in Chrome). Stdlib only |
