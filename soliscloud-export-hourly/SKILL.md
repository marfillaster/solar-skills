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
python3 .claude/skills/soliscloud-export-hourly/scripts/api_export.py TARGET_MONTH
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

## Chrome Fallback Path

### 3B. Pre-flight checks and user setup

Before starting, verify requirements and gather settings using AskUserQuestion.

**Remind the user of system requirements:**

> **Before we begin, please confirm:**
> - Claude Code is running with `--chrome` flag (required for browser automation)
> - You have an active Anthropic Pro/Max subscription (required for MCP tool access)
> - In Chrome settings (chrome://settings/downloads), **"Ask where to save each file before downloading"** is **disabled** — the bulk export downloads multiple files automatically and save-as dialogs will block the process
> - You are logged into [SolisCloud](https://www.soliscloud.com) in Chrome

**Ask for Chrome download folder:**

Use AskUserQuestion:
- **"Where does Chrome save downloaded files?"** — `~/Downloads` (Default) / Other (specify path)

Store the answer as `DOWNLOADS_DIR` for use in step 8B.

### 4B. Navigate to SolisCloud plant details

- Call `tabs_context_mcp` to find an existing SolisCloud tab, or create a new tab.
- Navigate to: `https://www.soliscloud.com/overview/plantStation`
- Click the first row in the Plant List table to open plant details.
- Wait for the Overview page to load (URL should contain `/details/overview/`).

### 5B. Navigate to any day in the target month

- The Operating Data panel has a date picker showing the current date and Day/Month/Year/Lifetime tabs.
- Ensure the **Day** tab is selected (orange text).
- Use the `<` and `>` arrows next to the date to navigate to any day within the target month. It doesn't matter which day — we just need to be viewing the target month so the export button and auth context are correct.

### 6B. Install bulk download interceptor

Inject the following JavaScript via `javascript_tool`. This intercepts the next export XHR, captures auth headers and request body, then replays the request for every day in the target month.

```javascript
// Bulk export interceptor for SolisCloud
(function() {
  const TARGET_MONTH = '__TARGET_MONTH__'; // replaced by agent, e.g. '2026-01'

  // Calculate all days in the month
  const [year, month] = TARGET_MONTH.split('-').map(Number);
  const daysInMonth = new Date(year, month, 0).getDate();
  const days = [];
  for (let d = 1; d <= daysInMonth; d++) {
    days.push(`${TARGET_MONTH}-${String(d).padStart(2, '0')}`);
  }

  window.__bulkExport = {
    status: 'waiting_for_trigger',
    total: days.length,
    completed: 0,
    failed: [],
    days: days
  };

  // Intercept XMLHttpRequest
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  let intercepted = false;

  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__url = url;
    this.__method = method;
    return origOpen.call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = function(body) {
    if (!intercepted && this.__url && this.__url.includes('/api/station/addChart')) {
      intercepted = true;

      // Capture headers from this request
      const origSetHeader = this.setRequestHeader;
      const capturedHeaders = {};
      this.setRequestHeader = function(name, value) {
        capturedHeaders[name] = value;
        return origSetHeader.call(this, name, value);
      };

      // Restore original prototypes immediately
      XMLHttpRequest.prototype.open = origOpen;
      XMLHttpRequest.prototype.send = origSend;

      // Parse the original body to use as template
      let bodyTemplate;
      try {
        bodyTemplate = JSON.parse(body);
      } catch(e) {
        bodyTemplate = {};
      }

      console.log('[BulkExport] Intercepted export request. Starting bulk download for', days.length, 'days');
      window.__bulkExport.status = 'downloading';
      window.__bulkExport.capturedHeaders = capturedHeaders;

      // Don't send the original request — start bulk download instead
      bulkDownload(capturedHeaders, bodyTemplate, days);
      return;
    }
    return origSend.call(this, body);
  };

  async function bulkDownload(headers, bodyTemplate, days) {
    for (let i = 0; i < days.length; i++) {
      const day = days[i];
      const body = Object.assign({}, bodyTemplate, { beginTime: day });

      try {
        const resp = await fetch('/api/station/addChart', {
          method: 'POST',
          headers: Object.assign({ 'Content-Type': 'application/json' }, headers),
          body: JSON.stringify(body)
        });

        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`);
        }

        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `Daily+Power+Station+Chart_${day}.xls`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        window.__bulkExport.completed++;
        console.log(`[BulkExport] Downloaded ${day} (${window.__bulkExport.completed}/${window.__bulkExport.total})`);
      } catch(e) {
        window.__bulkExport.failed.push(day);
        console.error(`[BulkExport] Failed ${day}:`, e);
      }

      // 500ms delay between requests
      if (i < days.length - 1) {
        await new Promise(r => setTimeout(r, 500));
      }
    }

    window.__bulkExport.status = 'complete';
    console.log(`[BulkExport] Done. ${window.__bulkExport.completed} succeeded, ${window.__bulkExport.failed.length} failed.`);
  }
})();
```

**Important:** Replace `__TARGET_MONTH__` with the actual `TARGET_MONTH` value before injecting.

### 7B. Trigger the interceptor

- Click the **export/download icon** (rightmost icon in the Operating Data toolbar, after the filter icon).
- Chrome will likely prompt "This site is trying to download multiple files" — tell the user: **"Chrome may ask permission to download multiple files. Please click 'Allow' if prompted."**
- The interceptor will capture the auth headers from this single click and begin replaying for all days.

### 8B. Wait and verify

- Poll `window.__bulkExport.status` via `javascript_tool` every 5 seconds.
- When status is `'complete'`, check `window.__bulkExport.completed` and `window.__bulkExport.failed`.
- If there are failures, log them but continue with available data.
- Wait a few seconds for final files to flush to disk.

### 9B. Process into monthly hourly CSV

Run the processing script, passing the downloads directory from step 3B:

```bash
python3 .claude/skills/soliscloud-export-hourly/scripts/process_month.py TARGET_MONTH DOWNLOADS_DIR
```

This reads all `Daily+Power+Station+Chart_YYYY-MM-DD.xls` files for the month from `DOWNLOADS_DIR` and outputs `data/solar_hourly_YYYY-MM.csv`.

### 10B. Display results

- Read the CSV file and display it to the user.
- Include the summary printed by the script (total days, total readings, total generation, any missing days).
- Highlight key monthly observations (peak generation day, average daily production, battery cycling patterns).

## XLS File Structure Reference (Chrome path)

The exported XLS has this layout:
- **Row 0**: Title (`Plant_YYYY-MM-DDChart`)
- **Row 2**: Plant name and installed capacity
- **Row 3**: Daily yield, earnings, full load hours
- **Row 28**: Column headers: `Number, Time, Working State, PV(W), Battery(W), Grid(W), Grid Load(W), Backup Load(W), SOC(%), GEN(W), Smart(W), AC Coupled(W)`
- **Row 29+**: Data rows at 5-minute intervals

Column definitions:
- **PV(W)**: Solar panel output power
- **Battery(W)**: Battery power (negative = discharging, positive = charging)
- **Grid(W)**: Grid power (negative = importing, positive = exporting)
- **Grid Load(W)**: Power consumed by grid-connected loads
- **Backup Load(W)**: Power consumed by backup loads
- **SOC(%)**: Battery state of charge
- **GEN(W)**, **Smart(W)**, **AC Coupled(W)**: Additional sources (typically zero)
