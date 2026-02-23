// SolisCloud Chrome Export — injectable bulk fetch + CSV generation
// Template variables (replaced by agent before injection):
//   __TARGET_MONTH__  — e.g. '2026-01'
//   __STATION_ID__    — e.g. '1298491919450376600'

(function() {
  'use strict';

  const TARGET_MONTH = '__TARGET_MONTH__';
  const STATION_ID = '__STATION_ID__';

  // Calculate all days in the month
  const [year, month] = TARGET_MONTH.split('-').map(Number);
  const daysInMonth = new Date(year, month, 0).getDate();
  const days = [];
  for (let d = 1; d <= daysInMonth; d++) {
    days.push(`${TARGET_MONTH}-${String(d).padStart(2, '0')}`);
  }

  // Shared state
  window.__solis = window.__solis || {};

  // --- Step 1: Capture auth headers from the next XHR request ---
  window.__solis.capturedHeaders = null;
  window.__solis.capturedBody = null;

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;

  let _captureResolve = null;
  let _captured = false;

  // Patch setRequestHeader to record headers per-instance
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

      // Restore prototypes
      XMLHttpRequest.prototype.open = origOpen;
      XMLHttpRequest.prototype.send = origSend;
      XMLHttpRequest.prototype.setRequestHeader = origSetHeader;

      window.__solis.capturedHeaders = Object.assign({}, this.__headers);
      try {
        window.__solis.capturedBody = JSON.parse(body);
      } catch(e) {
        window.__solis.capturedBody = {};
      }

      console.log('[SolisExport] Captured auth headers:', Object.keys(window.__solis.capturedHeaders));

      if (_captureResolve) {
        _captureResolve(window.__solis.capturedHeaders);
        _captureResolve = null;
      }
    }
    return origSend.call(this, body);
  };

  // Returns a promise that resolves when headers are captured
  window.__solis.captureHeaders = function() {
    if (window.__solis.capturedHeaders) {
      return Promise.resolve(window.__solis.capturedHeaders);
    }
    return new Promise(function(resolve) {
      _captureResolve = resolve;
    });
  };

  // --- Step 2: Bulk fetch all days ---
  window.__solis.fetchAllDays = function() {
    const headers = window.__solis.capturedHeaders;
    const bodyTemplate = window.__solis.capturedBody || {};

    if (!headers) {
      return Promise.reject(new Error('No captured headers. Click the < arrow first.'));
    }

    window.__solis.results = {};
    window.__solis.fetchStatus = {
      status: 'downloading',
      total: days.length,
      completed: 0,
      failed: []
    };

    return new Promise(function(resolve) {
      (async function() {
        for (let i = 0; i < days.length; i++) {
          const day = days[i];
          const body = Object.assign({}, bodyTemplate, {
            stationId: STATION_ID,
            date: day,
            time: day,
            localTime: Date.now()
          });

          try {
            const result = await new Promise(function(res, rej) {
              const xhr = new XMLHttpRequest();
              xhr.open('POST', '/api/chart/station/day/v2', true);
              const hdrs = window.__solis.capturedHeaders;
              for (const name of Object.keys(hdrs)) {
                xhr.setRequestHeader(name, hdrs[name]);
              }
              xhr.onload = function() {
                if (xhr.status === 401 || xhr.status === 403) {
                  rej(new Error('AUTH_EXPIRED'));
                } else if (xhr.status === 200) {
                  res(JSON.parse(xhr.responseText));
                } else {
                  rej(new Error('HTTP ' + xhr.status));
                }
              };
              xhr.onerror = function() { rej(new Error('Network error')); };
              xhr.send(JSON.stringify(body));
            });

            window.__solis.results[day] = result;
            window.__solis.fetchStatus.completed++;
            if (i % 5 === 0) {
              console.log('[SolisExport] Fetched ' + day + ' (' + window.__solis.fetchStatus.completed + '/' + window.__solis.fetchStatus.total + ')');
            }
          } catch(e) {
            if (e.message === 'AUTH_EXPIRED') {
              window.__solis.fetchStatus.status = 'auth_expired';
              console.error('[SolisExport] Auth expired at ' + day + '. Re-inject script and recapture headers.');
              resolve(window.__solis.fetchStatus);
              return;
            }
            window.__solis.fetchStatus.failed.push({ day: day, error: e.message });
            console.error('[SolisExport] Failed ' + day + ':', e.message);
          }

          if (i < days.length - 1) {
            await new Promise(function(r) { setTimeout(r, 300); });
          }
        }

        window.__solis.fetchStatus.status = 'complete';
        console.log('[SolisExport] Done. ' + window.__solis.fetchStatus.completed + ' succeeded, ' + window.__solis.fetchStatus.failed.length + ' failed.');
        resolve(window.__solis.fetchStatus);
      })();
    });
  };

  // --- Step 3: Generate CSV matching solar-analysis schema ---
  // CSV columns: Date,Hour,Readings,Avg_PV_W,PV_Energy_kWh,Avg_Battery_W,Battery_Energy_kWh,
  //   Avg_Grid_W,Grid_Energy_kWh,Avg_GridLoad_W,GridLoad_Energy_kWh,
  //   Avg_BackupLoad_W,BackupLoad_Energy_kWh,Avg_SOC_Pct,Min_SOC_Pct,Max_SOC_Pct
  //
  // API response arrays (288 entries each, values in kW except SOC in %):
  //   power[], batteryPower[], psum[], familyLoadPowerList[],
  //   bypassLoadPowerList[], batteryCapacitySocList[]

  window.__solis.generateCSV = function() {
    const results = window.__solis.results;
    if (!results || Object.keys(results).length === 0) {
      return { error: 'No results to process' };
    }

    const sortedDays = Object.keys(results).sort();
    const header = 'Date,Hour,Readings,Avg_PV_W,PV_Energy_kWh,Avg_Battery_W,Battery_Energy_kWh,Avg_Grid_W,Grid_Energy_kWh,Avg_GridLoad_W,GridLoad_Energy_kWh,Avg_BackupLoad_W,BackupLoad_Energy_kWh,Avg_SOC_Pct,Min_SOC_Pct,Max_SOC_Pct';
    const rows = [header];

    let totalPvKwh = 0;
    let daysWithData = 0;

    for (const day of sortedDays) {
      const resp = results[day];
      const data = resp && resp.data;
      if (!data || !data.time || data.time.length === 0) continue;

      daysWithData++;
      const len = data.time.length; // typically 288

      // Field arrays (default to empty arrays if missing)
      const power = data.power || [];
      const battery = data.batteryPower || [];
      const grid = data.psum || [];
      const gridLoad = data.familyLoadPowerList || [];
      const backupLoad = data.bypassLoadPowerList || [];
      const soc = data.batteryCapacitySocList || [];

      // Group readings by actual hour parsed from data.time (e.g. "06:30")
      const hourly = {};
      for (let i = 0; i < len; i++) {
        const t = data.time[i];
        if (!t || typeof t !== 'string') continue;
        const h = parseInt(t.split(':')[0], 10);
        if (isNaN(h)) continue;

        if (!hourly[h]) {
          hourly[h] = { pvSum: 0, batSum: 0, gridSum: 0, glSum: 0, blSum: 0, socSum: 0, socMin: Infinity, socMax: -Infinity, count: 0 };
        }
        const b = hourly[h];
        b.pvSum += (power[i] || 0);
        b.batSum += (battery[i] || 0);
        b.gridSum += (grid[i] || 0);
        b.glSum += (gridLoad[i] || 0);
        b.blSum += (backupLoad[i] || 0);
        const s = soc[i] || 0;
        b.socSum += s;
        if (s < b.socMin) b.socMin = s;
        if (s > b.socMax) b.socMax = s;
        b.count++;
      }

      const hours = Object.keys(hourly).map(Number).sort(function(a, c) { return a - c; });
      for (const h of hours) {
        const b = hourly[h];
        if (b.count === 0) continue;

        const avgPv = b.pvSum / b.count;
        const avgBat = b.batSum / b.count;
        const avgGrid = b.gridSum / b.count;
        const avgGl = b.glSum / b.count;
        const avgBl = b.blSum / b.count;
        const avgSoc = b.socSum / b.count;

        // Energy: avg_watts * readings * 5min / 60 / 1000
        const energyFactor = b.count * 5 / 60 / 1000;
        const pvEnergy = avgPv * energyFactor;
        const batEnergy = avgBat * energyFactor;
        const gridEnergy = avgGrid * energyFactor;
        const glEnergy = avgGl * energyFactor;
        const blEnergy = avgBl * energyFactor;

        totalPvKwh += pvEnergy;

        const hourStr = String(h).padStart(2, '0') + ':00';

        rows.push([
          day,
          hourStr,
          b.count,
          Math.round(avgPv * 10) / 10,
          Math.round(pvEnergy * 1000) / 1000,
          Math.round(avgBat * 10) / 10,
          Math.round(batEnergy * 1000) / 1000,
          Math.round(avgGrid * 10) / 10,
          Math.round(gridEnergy * 1000) / 1000,
          Math.round(avgGl * 10) / 10,
          Math.round(glEnergy * 1000) / 1000,
          Math.round(avgBl * 10) / 10,
          Math.round(blEnergy * 1000) / 1000,
          Math.round(avgSoc * 10) / 10,
          b.socMin === Infinity ? 0 : Math.round(b.socMin),
          b.socMax === -Infinity ? 0 : Math.round(b.socMax)
        ].join(','));
      }
    }

    const csv = rows.join('\n') + '\n';

    // Trigger download
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'solar_hourly_' + TARGET_MONTH + '.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    // Store CSV content for retrieval
    window.__solis.csvContent = csv;

    const stats = {
      totalDays: sortedDays.length,
      daysWithData: daysWithData,
      totalRows: rows.length - 1,
      totalPvKwh: Math.round(totalPvKwh * 10) / 10,
      filename: 'solar_hourly_' + TARGET_MONTH + '.csv'
    };

    console.log('[SolisExport] CSV generated:', JSON.stringify(stats));
    return stats;
  };

  console.log('[SolisExport] Initialized for ' + TARGET_MONTH + ' (' + days.length + ' days). Waiting for header capture...');
  console.log('[SolisExport] Click the < date arrow to trigger a chart request, then call window.__solis.fetchAllDays()');
})();
