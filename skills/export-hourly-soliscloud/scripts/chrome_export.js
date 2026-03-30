// SolisCloud Chrome Export — injectable bulk fetch + CSV generation
// Template variable (replaced by agent before injection):
//   __TARGET_MONTH__  — e.g. '2026-01'

(function() {
  'use strict';

  const TARGET_MONTH = '__TARGET_MONTH__';

  // Calculate all days in the month
  const [year, month] = TARGET_MONTH.split('-').map(Number);
  const daysInMonth = new Date(year, month, 0).getDate();
  const days = [];
  for (let d = 1; d <= daysInMonth; d++) {
    days.push(`${TARGET_MONTH}-${String(d).padStart(2, '0')}`);
  }

  // Shared state
  window.__solis = window.__solis || {};

  // --- Step 1: Capture auth headers + body from the next XHR to /api/chart/station/day ---
  // The body template is required: the API uses fields like `id`, `timeZone`, `version`,
  // `language` that vary per account and are not inferrable. We spread it and override
  // only the date-specific fields for each day's fetch.
  window.__solis.capturedHeaders = null;
  window.__solis.capturedBody = null;

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;

  let _captureResolve = null;
  let _captured = false;

  XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
    if (!this.__headers) this.__headers = {};
    this.__headers[name] = value;
    return origSetHeader.call(this, name, value);
  };

  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__url = url;
    this.__method = method;
    this.__headers = {};
    return origOpen.call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = function(body) {
    if (!_captured && this.__url && this.__url.includes('/api/chart/station/day')) {
      _captured = true;

      // Restore prototypes immediately so normal page operation resumes
      XMLHttpRequest.prototype.open = origOpen;
      XMLHttpRequest.prototype.send = origSend;
      XMLHttpRequest.prototype.setRequestHeader = origSetHeader;

      window.__solis.capturedHeaders = Object.assign({}, this.__headers);
      try { window.__solis.capturedBody = JSON.parse(body); } catch(e) { window.__solis.capturedBody = {}; }
      console.log('[SolisExport] Captured headers:', Object.keys(window.__solis.capturedHeaders));
      console.log('[SolisExport] Captured body keys:', Object.keys(window.__solis.capturedBody));

      if (_captureResolve) {
        _captureResolve(window.__solis.capturedHeaders);
        _captureResolve = null;
      }
    }
    return origSend.call(this, body);
  };

  window.__solis.captureHeaders = function() {
    if (window.__solis.capturedHeaders) return Promise.resolve(window.__solis.capturedHeaders);
    return new Promise(function(resolve) { _captureResolve = resolve; });
  };

  // --- Step 2: Bulk fetch all days ---
  window.__solis.fetchAllDays = function() {
    const headers = window.__solis.capturedHeaders;
    if (!headers) return Promise.reject(new Error('No captured headers — click the < arrow first.'));

    // Spread the captured body template so all account-specific fields are preserved,
    // then override only the date fields for each day's request.
    const bodyTemplate = window.__solis.capturedBody || {};

    window.__solis.results = {};
    window.__solis.fetchStatus = { status: 'downloading', total: days.length, completed: 0, failed: [] };

    return new Promise(function(resolve) {
      (async function() {
        for (let i = 0; i < days.length; i++) {
          const day = days[i];
          const body = JSON.stringify(Object.assign({}, bodyTemplate, {
            date: day,
            time: day,
            localTime: Date.now()
          }));

          try {
            const result = await new Promise(function(res, rej) {
              const xhr = new XMLHttpRequest();
              xhr.open('POST', '/api/chart/station/day/v2', true);
              for (const name of Object.keys(headers)) xhr.setRequestHeader(name, headers[name]);
              xhr.onload = function() {
                if (xhr.status === 401 || xhr.status === 403) rej(new Error('AUTH_EXPIRED'));
                else if (xhr.status === 200) res(JSON.parse(xhr.responseText));
                else rej(new Error('HTTP ' + xhr.status));
              };
              xhr.onerror = function() { rej(new Error('Network error')); };
              xhr.send(body);
            });

            window.__solis.results[day] = result;
            window.__solis.fetchStatus.completed++;
            if (i % 5 === 0) console.log('[SolisExport] Fetched ' + day + ' (' + window.__solis.fetchStatus.completed + '/' + days.length + ')');
          } catch(e) {
            if (e.message === 'AUTH_EXPIRED') {
              window.__solis.fetchStatus.status = 'auth_expired';
              console.error('[SolisExport] Auth expired at ' + day + '. Re-inject script and recapture headers.');
              resolve(window.__solis.fetchStatus);
              return;
            }
            window.__solis.fetchStatus.failed.push({ day, error: e.message });
            console.warn('[SolisExport] Failed ' + day + ':', e.message);
          }

          if (i < days.length - 1) await new Promise(function(r) { setTimeout(r, 300); });
        }

        window.__solis.fetchStatus.status = 'complete';
        console.log('[SolisExport] Done. ' + window.__solis.fetchStatus.completed + ' succeeded, ' + window.__solis.fetchStatus.failed.length + ' failed.');
        resolve(window.__solis.fetchStatus);
      })();
    });
  };

  // --- Step 3: Generate CSV matching solar-analysis schema ---
  // API response field reference:
  //   timeStr[]               — "HH:MM:SS" strings (use this for hour bucketing)
  //   time[]                  — Unix ms timestamps (fallback if timeStr absent)
  //   power[]                 — PV power (W)
  //   batteryPower[]          — battery power, positive=charging, negative=discharging (W)
  //   psum[]                  — grid power, positive=export, negative=import (W)
  //   familyLoadPowerList[]   — grid-connected load (W)
  //   bypassLoadPowerList[]   — backup load (W)
  //   batteryCapacitySocList[]— battery SOC (%)

  window.__solis.generateCSV = function() {
    const results = window.__solis.results;
    if (!results || Object.keys(results).length === 0) return { error: 'No results to process' };

    const sortedDays = Object.keys(results).sort();
    const header = 'Date,Hour,Readings,Avg_PV_W,PV_Energy_kWh,Avg_Battery_W,Battery_Energy_kWh,Avg_Grid_W,Grid_Energy_kWh,Avg_GridLoad_W,GridLoad_Energy_kWh,Avg_BackupLoad_W,BackupLoad_Energy_kWh,Avg_SOC_Pct,Min_SOC_Pct,Max_SOC_Pct';
    const rows = [header];
    let totalPvKwh = 0, daysWithData = 0;

    for (const day of sortedDays) {
      const data = results[day] && results[day].data;
      if (!data) continue;

      // Prefer timeStr ("HH:MM:SS") for hour extraction; fall back to unix ms timestamps
      const timeArr = (data.timeStr && data.timeStr.length > 0) ? data.timeStr : data.time;
      if (!timeArr || timeArr.length === 0) continue;

      daysWithData++;
      const power    = data.power                  || [];
      const battery  = data.batteryPower           || [];
      const grid     = data.psum                   || [];
      const gridLoad = data.familyLoadPowerList    || [];
      const backup   = data.bypassLoadPowerList    || [];
      const soc      = data.batteryCapacitySocList || [];

      const hourly = {};
      for (let i = 0; i < timeArr.length; i++) {
        const t = timeArr[i];
        let h;
        if (typeof t === 'string') {
          h = parseInt(t.split(':')[0], 10);       // "HH:MM:SS" or "HH:MM"
        } else if (typeof t === 'number') {
          h = new Date(t).getHours();               // Unix ms, browser local time
        }
        if (h === undefined || isNaN(h)) continue;

        if (!hourly[h]) hourly[h] = { pvSum:0, batSum:0, gridSum:0, glSum:0, blSum:0, socSum:0, socMin:Infinity, socMax:-Infinity, count:0 };
        const b = hourly[h];
        b.pvSum   += (power[i]    || 0);
        b.batSum  += (battery[i]  || 0);
        b.gridSum += (grid[i]     || 0);
        b.glSum   += (gridLoad[i] || 0);
        b.blSum   += (backup[i]   || 0);
        const s = soc[i] || 0;
        b.socSum += s;
        if (s < b.socMin) b.socMin = s;
        if (s > b.socMax) b.socMax = s;
        b.count++;
      }

      for (const h of Object.keys(hourly).map(Number).sort((a, b) => a - b)) {
        const b = hourly[h];
        if (b.count === 0) continue;
        const avgPv   = b.pvSum   / b.count;
        const avgBat  = b.batSum  / b.count;
        const avgGrid = b.gridSum / b.count;
        const avgGl   = b.glSum   / b.count;
        const avgBl   = b.blSum   / b.count;
        const avgSoc  = b.socSum  / b.count;
        const ef = b.count * 5 / 60 / 1000;  // readings × 5 min ÷ 60 ÷ 1000 → kWh factor
        totalPvKwh += avgPv * ef;
        rows.push([
          day,
          String(h).padStart(2, '0') + ':00',
          b.count,
          Math.round(avgPv   * 10) / 10,  Math.round(avgPv   * ef * 1000) / 1000,
          Math.round(avgBat  * 10) / 10,  Math.round(avgBat  * ef * 1000) / 1000,
          Math.round(avgGrid * 10) / 10,  Math.round(avgGrid * ef * 1000) / 1000,
          Math.round(avgGl   * 10) / 10,  Math.round(avgGl   * ef * 1000) / 1000,
          Math.round(avgBl   * 10) / 10,  Math.round(avgBl   * ef * 1000) / 1000,
          Math.round(avgSoc  * 10) / 10,
          b.socMin ===  Infinity ? 0 : Math.round(b.socMin),
          b.socMax === -Infinity ? 0 : Math.round(b.socMax)
        ].join(','));
      }
    }

    const csv = rows.join('\n') + '\n';
    window.__solis.csvContent = csv;

    // Trigger a browser download so the file lands in ~/Downloads.
    // The agent then moves it to data/ via Bash — avoids the evaluate_script size limit.
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'solar_hourly_' + TARGET_MONTH + '.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    const stats = {
      totalDays:   sortedDays.length,
      daysWithData,
      totalRows:   rows.length - 1,
      totalPvKwh:  Math.round(totalPvKwh * 10) / 10,
      filename:    'solar_hourly_' + TARGET_MONTH + '.csv'
    };
    console.log('[SolisExport] CSV generated:', JSON.stringify(stats));
    return stats;
  };

  console.log('[SolisExport] Initialized for ' + TARGET_MONTH + ' (' + days.length + ' days). Click the < date arrow to capture auth headers.');
})();
