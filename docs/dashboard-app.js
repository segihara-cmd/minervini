/* eslint-disable no-unused-vars */
const BLUE = '#1d4ed8', RED = '#dc2626', AMBER = '#f59e0b', CYAN = '#22d3ee', PURPLE = '#7c3aed', GRAY = '#94a3b8';
const KOSPI_LINE = '#172554'; // 짙은 남색
let chartInstances = [];

const API_CANDIDATES = () => {
  const q = new URLSearchParams(location.search).get('api');
  if (q) return [q.replace(/\/$/, '')];
  const fromCfg = (window.__DASHBOARD_API_BASES || []);
  const list = [...fromCfg];
  if (location.hostname.includes('vercel.app')) list.unshift('');
  if (!list.includes('https://minervini.vercel.app')) {
    list.push('https://minervini.vercel.app');
  }
  return [...new Set(list)];
};

async function loadApiConfig() {
  try {
    const res = await fetchWithTimeout(`./config.json?t=${Date.now()}`, 8000);
    if (res.ok) {
      const cfg = await res.json();
      window.__DASHBOARD_API_BASES = cfg.apiBases || [];
    }
  } catch (_) { /* optional */ }
}

async function fetchWithTimeout(url, timeoutMs = 12000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { cache: 'no-store', signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

function fmtVal(v, unit = '', decimals = 2) {
  if (v == null) return 'N/A';
  return `${v.toLocaleString('en-US', { maximumFractionDigits: decimals, minimumFractionDigits: decimals })}${unit}`;
}

function fmtPct(v) {
  if (v == null) return '';
  const sign = v >= 0 ? '+' : '';
  const color = v >= 0 ? '#16a34a' : '#dc2626';
  const arrow = v >= 0 ? '▲' : '▼';
  return `<span style="color:${color}">${arrow} ${sign}${v.toFixed(2)}%</span>`;
}

function lastVal(arr) {
  if (!arr?.length) return null;
  for (let i = arr.length - 1; i >= 0; i--) {
    if (arr[i] != null && !Number.isNaN(arr[i])) return arr[i];
  }
  return null;
}

function latestItemsHtml(items) {
  const parts = items
    .filter(it => it.val != null)
    .map(it => {
      let v;
      if (it.pct) v = `${it.val >= 0 ? '+' : ''}${it.val.toFixed(it.dec ?? 2)}%`;
      else if (it.prefix && it.unit) v = `${it.prefix}${fmtVal(it.val, '', it.dec ?? 2)}${it.unit}`;
      else if (it.prefix) v = `${it.prefix}${fmtVal(it.val, '', it.dec ?? 2)}`;
      else v = fmtVal(it.val, it.unit || '', it.dec ?? 2);
      return `<span class="latest-item"><span class="latest-dot" style="background:${it.color}"></span>${it.label} <strong style="color:${it.color}">${v}</strong></span>`;
    });
  return parts.length ? `<span class="chart-latest">${parts.join('')}</span>` : '';
}

function sigRow(label, ok, detail = '') {
  const ico = ok ? '🟢' : '🔴';
  const stat = ok ? '정상' : '이탈';
  const col = ok ? '#16a34a' : '#dc2626';
  return `<tr>
    <td style="padding:6px 12px">${label}</td>
    <td style="padding:6px 12px;text-align:center">${ico}</td>
    <td style="padding:6px 12px;color:${col};font-weight:600">${stat}</td>
    <td style="padding:6px 12px;color:#64748b;font-size:.85em">${detail}</td>
  </tr>`;
}

function tierRow(label, val, tiers, detail = '') {
  if (val == null) return sigRow(label, false, detail || 'N/A');
  for (const [threshold, ico, stat, col] of tiers) {
    if (val >= threshold) {
      return `<tr>
        <td style="padding:6px 12px">${label}</td>
        <td style="padding:6px 12px;text-align:center">${ico}</td>
        <td style="padding:6px 12px;color:${col};font-weight:600">${stat}</td>
        <td style="padding:6px 12px;color:#64748b;font-size:.85em">${detail}</td>
      </tr>`;
    }
  }
  const last = tiers[tiers.length - 1];
  return `<tr>
    <td style="padding:6px 12px">${label}</td>
    <td style="padding:6px 12px;text-align:center">${last[1]}</td>
    <td style="padding:6px 12px;color:${last[3]};font-weight:600">${last[2]}</td>
    <td style="padding:6px 12px;color:#64748b;font-size:.85em">${detail}</td>
  </tr>`;
}

function skewTierRow(val) {
  if (val == null) return sigRow('CBOE SKEW', false, 'N/A');
  let ico, stat, col;
  if (val <= 135) { ico = '🟢'; stat = '안정'; col = '#16a34a'; }
  else if (val <= 145) { ico = '⚠️'; stat = '주의'; col = '#ca8a04'; }
  else { ico = '🔴'; stat = '꼬리위험↑'; col = '#dc2626'; }
  return `<tr>
    <td style="padding:6px 12px">CBOE SKEW (꼬리위험)</td>
    <td style="padding:6px 12px;text-align:center">${ico}</td>
    <td style="padding:6px 12px;color:${col};font-weight:600">${stat}</td>
    <td style="padding:6px 12px;color:#64748b;font-size:.85em">현재 ${fmtVal(val, '', 2)} · 135↓안정 / 145↑경계</td>
  </tr>`;
}

function destroyCharts() {
  chartInstances.forEach(c => c.destroy());
  chartInstances = [];
}

function chartCfg(labels, datasets, yLabel = '', y2Label = '') {
  return {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1e293b', titleColor: '#94a3b8', bodyColor: '#e2e8f0',
          borderColor: '#334155', borderWidth: 1,
        },
      },
      scales: {
        x: { ticks: { color: '#64748b', maxTicksLimit: 6, font: { size: 10 } }, grid: { color: '#cbd5e188' } },
        y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#94a3b844' }, title: { display: !!yLabel, text: yLabel, color: '#64748b', font: { size: 10 } } },
        ...(y2Label ? { y2: { type: 'linear', position: 'right', ticks: { color: '#64748b', font: { size: 10 } }, grid: { drawOnChartArea: false }, title: { display: true, text: y2Label, color: '#64748b', font: { size: 10 } } } } : {}),
      },
    },
  };
}

function ds(label, data, color, yID = 'y', fill = false, dash = [], borderWidth = 2) {
  return {
    label, data,
    borderColor: color,
    backgroundColor: fill ? (color.startsWith('#') && color.length === 7 ? color + '22' : color) : 'transparent',
    borderWidth, pointRadius: 0, tension: 0.3, yAxisID: yID,
    ...(dash.length ? { borderDash: dash } : {}),
  };
}

function mkChart(id, labels, datasets, yLabel = '', y2Label = '') {
  const el = document.getElementById(id);
  if (!el) return;
  const ch = new Chart(el, chartCfg(labels, datasets, yLabel, y2Label));
  chartInstances.push(ch);
}

function drawLabelHalo(ctx, text, x, y, fillColor) {
  ctx.font = '600 8px -apple-system,sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineWidth = 3;
  ctx.strokeStyle = '#ffffff';
  ctx.strokeText(text, x, y);
  ctx.fillStyle = fillColor;
  ctx.fillText(text, x, y);
}

function exportComboLabelPlugin() {
  return {
    id: 'exportComboLabels',
    afterDatasetsDraw(chart) {
      const { ctx } = chart;
      const barMeta = chart.getDatasetMeta(0);
      ctx.save();

      // 1) 막대 수출 라벨
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      barMeta.data.forEach((el, i) => {
        const val = chart.data.datasets[0].data[i];
        if (val == null || Number.isNaN(val)) return;
        ctx.font = '600 9px -apple-system,sans-serif';
        ctx.fillStyle = '#1e40af';
        ctx.fillText(`$${val.toFixed(1)}B`, el.x, el.y - 4);
      });

      // 2) QoQ — 막대 안쪽 / YoY — 선 포인트 위 (원래 위치)
      chart.data.datasets.forEach((dataset, di) => {
        if (dataset.type === 'bar') return;
        const meta = chart.getDatasetMeta(di);
        if (meta.hidden) return;
        const isYoy = dataset.label === 'YoY';
        const color = dataset.borderColor || '#64748b';
        meta.data.forEach((el, i) => {
          const val = dataset.data[i];
          if (val == null || Number.isNaN(val)) return;
          const text = `${val >= 0 ? '+' : ''}${val.toFixed(0)}%`;
          if (isYoy) {
            drawLabelHalo(ctx, text, el.x, el.y - 10, color);
          } else {
            const bar = barMeta.data[i];
            if (!bar || bar.height < 20) return;
            const y = bar.y + bar.height * 0.52;
            drawLabelHalo(ctx, text, bar.x, y, color);
          }
        });
      });

      ctx.restore();
    },
  };
}

function exportComboChartCfg(labels, exports, qoq, yoy) {
  const maxExport = Math.max(...exports);
  const pctVals = [...qoq, ...yoy].filter(v => v != null && !Number.isNaN(v));
  const lo = pctVals.length ? Math.min(...pctVals) : -20;
  const hi = pctVals.length ? Math.max(...pctVals) : 140;
  const pad = Math.max(12, (hi - lo) * 0.12);

  return {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          type: 'bar',
          label: '분기 수출',
          data: exports,
          backgroundColor: '#3b82f688',
          borderColor: '#2563eb',
          borderWidth: 1,
          borderRadius: 3,
          yAxisID: 'y',
          order: 2,
        },
        {
          type: 'line',
          label: 'QoQ',
          data: qoq,
          borderColor: AMBER,
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 3,
          pointBackgroundColor: AMBER,
          tension: 0.2,
          yAxisID: 'y2',
          order: 1,
          spanGaps: true,
        },
        {
          type: 'line',
          label: 'YoY',
          data: yoy,
          borderColor: RED,
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 3,
          pointBackgroundColor: RED,
          tension: 0.2,
          yAxisID: 'y2',
          order: 0,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 28 } },
      plugins: {
        legend: {
          display: true,
          labels: { color: '#64748b', font: { size: 10 }, boxWidth: 12 },
        },
      },
      scales: {
        x: { ticks: { color: '#64748b', maxRotation: 45, minRotation: 45, font: { size: 9 } }, grid: { display: false } },
        y: {
          type: 'linear',
          position: 'left',
          min: 0,
          max: Math.ceil(maxExport * 1.15),
          ticks: { color: '#2563eb', font: { size: 10 }, callback: v => `$${v}B` },
          grid: { color: '#94a3b844' },
          title: { display: true, text: '수출 (USD)', color: '#2563eb', font: { size: 10 } },
        },
        y2: {
          type: 'linear',
          position: 'right',
          min: Math.floor(lo - pad),
          max: Math.ceil(hi + pad),
          ticks: { color: '#64748b', font: { size: 10 }, callback: v => `${v}%` },
          grid: { drawOnChartArea: false },
          title: { display: true, text: '증감률 (%)', color: '#64748b', font: { size: 10 } },
        },
      },
    },
    plugins: [exportComboLabelPlugin()],
  };
}

function mkExportChart(id, cfg) {
  const el = document.getElementById(id);
  if (!el) return;
  chartInstances.push(new Chart(el, cfg));
}

function renderExportCharts(E) {
  if (!E?.quarters?.length) return;
  const labels = E.quarters.map(r => r.q);
  const exports = E.quarters.map(r => r.exportB);
  const qoq = E.quarters.map(r => r.qoq);
  const yoy = E.quarters.map(r => r.yoy);
  const last = E.quarters[E.quarters.length - 1];

  mkExportChart('cHsExport', exportComboChartCfg(labels, exports, qoq, yoy));

  const latestExport = latestItemsHtml([
    { label: '최근 분기', val: last.exportB, color: BLUE, prefix: '$', dec: 2, unit: 'B' },
    { label: 'QoQ', val: last.qoq, color: AMBER, pct: true },
    { label: 'YoY', val: last.yoy, color: RED, pct: true },
  ]);
  const wrap = document.getElementById('exportLatest');
  if (wrap) wrap.innerHTML = latestExport;
}

function exportSectionHtml(E) {
  if (!E?.quarters?.length) return '';
  const summary = (E.summary || []).map(s => `<li>${s}</li>`).join('');
  const api = E.customsApi;
  const apiHint = api
    ? ` · API: data.go.kr HS${api.hsCode || '8542'} (${api.countryCount || 15}개국)`
    : '';
  const keyWarn = api && !api.keyConfigured
    ? ' · <strong style="color:#b45309">DATA_GO_KR_API_KEY 미설정</strong>'
    : '';
  const liveTag = E._live
    ? '<span style="color:#16a34a;font-weight:600">실시간 (관세청 API)</span>'
    : '<span style="color:#64748b">스냅샷 (CI)</span>';
  return `
<div class="export-note">
  <strong>⚠️ 주의</strong> ${E.note || ''} · 데이터: ${E.source || ''}${apiHint}${keyWarn} · 기준일: ${E.asOf || ''} · ${liveTag}
</div>
<div class="charts-grid">
  <div class="chart-card chart-card-wide">
    <div class="chart-title-wrap">
      <div class="chart-title">반도체(HS8542) 분기별 수출 및 증감률 <span class="chart-sub">· USD 막대 + QoQ/YoY · 수치 표시</span></div>
      <span class="chart-latest" id="exportLatest"></span>
    </div>
    <div class="chart-legend">
      <span class="leg"><span class="leg-dot" style="background:#2563eb"></span>분기 수출</span>
      <span class="leg"><span class="leg-dot" style="background:#f59e0b"></span>QoQ</span>
      <span class="leg"><span class="leg-dot" style="background:#dc2626"></span>YoY</span>
    </div>
    <div class="chart-wrap-tall"><canvas id="cHsExport"></canvas></div>
    ${summary ? `<div class="export-summary"><strong>해석 요약</strong><ul>${summary}</ul></div>` : ''}
  </div>
</div>`;
}

function renderCharts(D, exportData) {
  destroyCharts();
  mkChart('cKospi', D.charts.kospi.dates, [
    ds('KOSPI', D.charts.kospi.price, KOSPI_LINE, 'y', true, [], 1.5),
    ds('SMA50', D.charts.kospi.ma50, AMBER, 'y', false, [], 1.5),
    ds('SMA150', D.charts.kospi.ma150, CYAN, 'y', false, [], 1.5),
    ds('SMA200', D.charts.kospi.ma200, PURPLE, 'y', false, [], 1.5),
  ]);
  mkChart('cSamHyn', D.charts.sam_hyn.dates, [
    ds('삼성전자', D.charts.sam_hyn.sam, BLUE, 'y'),
    ds('SK하이닉스', D.charts.sam_hyn.hyn, RED, 'y'),
  ], '누적수익률(%)');
  mkChart('cSoxNvda', D.charts.sox_nvda.dates, [
    ds('SOX', D.charts.sox_nvda.sox, BLUE, 'y'),
    ds('NVIDIA', D.charts.sox_nvda.nvda, RED, 'y'),
  ], '누적수익률(%)');
  const vDates = D.charts.vix_tnx.dates;
  const tMap = Object.fromEntries(D.charts.vix_tnx.tnx_dates.map((d, i) => [d, D.charts.vix_tnx.tnx[i]]));
  mkChart('cVixTnx', vDates, [
    ds('VIX', D.charts.vix_tnx.vix, AMBER, 'y'),
    ds('10년물%', vDates.map(d => tMap[d] ?? null), CYAN, 'y2'),
  ], 'VIX', '금리(%)');
  const fDates = D.charts.fx_wti.dates;
  const wMap = Object.fromEntries(D.charts.fx_wti.wti_dates.map((d, i) => [d, D.charts.fx_wti.wti[i]]));
  mkChart('cFxWti', fDates, [
    ds('달러/원', D.charts.fx_wti.fx, BLUE, 'y'),
    ds('WTI', fDates.map(d => wMap[d] ?? null), RED, 'y2'),
  ], '달러/원', 'WTI($)');
  mkChart('cSoxMu', D.charts.sox_mu.dates, [
    ds('Micron', D.charts.sox_mu.mu, PURPLE, 'y'),
    ds('SOX', D.charts.sox_mu.sox, GRAY, 'y'),
  ], '누적수익률(%)');
  if (D.charts.adr.dates.length) {
    const adrRef = D.charts.adr.dates.map(() => 100);
    mkChart('cAdr', D.charts.adr.dates, [
      ds('KOSPI ADR', D.charts.adr.kospi, BLUE, 'y'),
      ds('KOSDAQ ADR', D.charts.adr.kosdaq, RED, 'y'),
      ds('기준 100', adrRef, GRAY, 'y', false, [6, 4]),
    ], 'ADR');
  }
  if (D.charts.skew.dates.length) {
    mkChart('cSkew', D.charts.skew.dates, [ds('SKEW', D.charts.skew.values, AMBER, 'y', true)], 'SKEW');
  }
  renderExportCharts(exportData);
}

function renderDashboard(D, exportData) {
  const kpi = D.kpi;
  const ex = D.exit;
  const alignedOk = ex.aligned === true;
  const vixOk = (kpi.vix.val || 0) <= 25;
  const tnxOk = (kpi.tnx.val || 0) <= 4.5;
  const adrTiers = [[100, '🟢', '정상', '#16a34a'], [75, '⚠️', '주의', '#ca8a04'], [0, '🔴', '약세', '#dc2626']];
  const ma50v = fmtVal(ex.ma50, '', 0);
  const ma150v = fmtVal(ex.ma150, '', 0);
  const ma200v = fmtVal(ex.ma200, '', 0);
  const ksv = fmtVal(kpi.ks11.val, '', 2);
  const ch = D.charts;
  const vDates = ch.vix_tnx.dates;
  const tMap = Object.fromEntries(ch.vix_tnx.tnx_dates.map((d, i) => [d, ch.vix_tnx.tnx[i]]));
  const fDates = ch.fx_wti.dates;
  const wMap = Object.fromEntries(ch.fx_wti.wti_dates.map((d, i) => [d, ch.fx_wti.wti[i]]));
  const lastVD = vDates.length ? vDates[vDates.length - 1] : null;
  const lastFD = fDates.length ? fDates[fDates.length - 1] : null;

  const latestAdr = latestItemsHtml([
    { label: 'KOSPI', val: lastVal(ch.adr.kospi), color: BLUE, dec: 1 },
    { label: 'KOSDAQ', val: lastVal(ch.adr.kosdaq), color: RED, dec: 1 },
  ]);
  const latestSkew = latestItemsHtml([{ label: 'SKEW', val: lastVal(ch.skew.values), color: AMBER, dec: 2 }]);
  const latestKospi = latestItemsHtml([
    { label: 'KOSPI', val: lastVal(ch.kospi.price), color: KOSPI_LINE, dec: 2 },
    { label: 'SMA50', val: lastVal(ch.kospi.ma50), color: AMBER, dec: 0 },
    { label: 'SMA150', val: lastVal(ch.kospi.ma150), color: CYAN, dec: 0 },
    { label: 'SMA200', val: lastVal(ch.kospi.ma200), color: PURPLE, dec: 0 },
  ]);
  const latestSamHyn = latestItemsHtml([
    { label: '삼성전자', val: kpi.sam.val, color: BLUE, unit: '원', dec: 0 },
    { label: 'SK하이닉스', val: kpi.hyn.val, color: RED, unit: '원', dec: 0 },
  ]);
  const latestSoxNvda = latestItemsHtml([
    { label: 'SOX', val: lastVal(ch.sox_nvda.sox), color: BLUE, pct: true },
    { label: 'NVIDIA', val: lastVal(ch.sox_nvda.nvda), color: RED, pct: true },
  ]);
  const latestVixTnx = latestItemsHtml([
    { label: 'VIX', val: lastVal(ch.vix_tnx.vix), color: AMBER, dec: 2 },
    { label: '10년물', val: lastVD ? (tMap[lastVD] ?? null) : lastVal(ch.vix_tnx.tnx), color: CYAN, dec: 2, unit: '%' },
  ]);
  const latestFxWti = latestItemsHtml([
    { label: '달러/원', val: lastVal(ch.fx_wti.fx), color: BLUE, dec: 1, unit: '원' },
    { label: 'WTI', val: lastFD ? (wMap[lastFD] ?? null) : lastVal(ch.fx_wti.wti), color: RED, dec: 2, prefix: '$' },
  ]);
  const latestSoxMu = latestItemsHtml([
    { label: 'Micron', val: lastVal(ch.sox_mu.mu), color: PURPLE, pct: true },
    { label: 'SOX', val: lastVal(ch.sox_mu.sox), color: GRAY, pct: true },
  ]);

  document.getElementById('updated').textContent =
    `업데이트: ${D.updated} · ${D._live ? '실시간 (Yahoo 현재가)' : '스냅샷 (CI)'}`;

  document.getElementById('app-content').innerHTML = `
<div class="exit-panel">
  <div class="exit-banner" style="background:${ex.color}22;border-color:${ex.color}">
    <div class="exit-title">${ex.badge} EXIT 레벨</div>
    <div class="exit-sub">${ex.desc}</div>
  </div>
  <div class="signals-card">
    <h3>🚦 Exit 신호 모니터링</h3>
    <table>
      <tr><th>신호</th><th style="text-align:center">상태</th><th>판정</th><th>상세</th></tr>
      ${sigRow('KOSPI 정배열 (curr>SMA50>150>200)', alignedOk, `KOSPI ${ksv} | SMA50 ${ma50v} | SMA150 ${ma150v} | SMA200 ${ma200v}`)}
      ${sigRow('VIX ≤ 25', vixOk, `현재 VIX ${fmtVal(kpi.vix.val, '', 2)}`)}
      ${sigRow('미국 10년물 ≤ 4.5%', tnxOk, `현재 ${fmtVal(kpi.tnx.val, '%', 2)}`)}
      ${tierRow('KOSPI ADR (등락비율)', kpi.adr_kospi.val, adrTiers, `현재 ${fmtVal(kpi.adr_kospi.val, '', 2)} · 100↑정상 / 75↓약세`)}
      ${tierRow('KOSDAQ ADR (등락비율)', kpi.adr_kosdaq.val, adrTiers, `현재 ${fmtVal(kpi.adr_kosdaq.val, '', 2)} · 100↑정상 / 75↓약세`)}
      ${skewTierRow(kpi.skew.val)}
      <tr>
        <td style="padding:6px 12px">VIX 횡보 (20~30, 3주↑)</td>
        <td style="padding:6px 12px;text-align:center">${ex.sideways ? '⚠️' : '🟢'}</td>
        <td style="padding:6px 12px;color:${ex.sideways ? '#ca8a04' : '#16a34a'};font-weight:600">${ex.sideways ? '감지됨' : '미감지'}</td>
        <td style="padding:6px 12px;color:#64748b;font-size:.85em">레버리지 베타 슬리피지 가속 구간</td>
      </tr>
    </table>
  </div>
</div>

<div class="kpi-grid">
  ${[
    ['삼성전자', fmtVal(kpi.sam.val, '원', 0), kpi.sam.pct],
    ['SK하이닉스', fmtVal(kpi.hyn.val, '원', 0), kpi.hyn.pct],
    ['KOSPI', fmtVal(kpi.ks11.val, '', 2), kpi.ks11.pct],
    ['KOSPI ADR', fmtVal(kpi.adr_kospi.val, '', 2), kpi.adr_kospi.pct],
    ['KOSDAQ ADR', fmtVal(kpi.adr_kosdaq.val, '', 2), kpi.adr_kosdaq.pct],
    ['CBOE SKEW', fmtVal(kpi.skew.val, '', 2), kpi.skew.pct],
    ['SOX', fmtVal(kpi.sox.val, '', 0), kpi.sox.pct],
    ['NVIDIA', '$' + fmtVal(kpi.nvda.val, '', 2), kpi.nvda.pct],
    ['VIX', fmtVal(kpi.vix.val, '', 2), kpi.vix.pct],
    ['미국 10년물', fmtVal(kpi.tnx.val, '%', 2), kpi.tnx.pct],
    ['달러/원', fmtVal(kpi.fx.val, '원', 1), kpi.fx.pct],
    ['WTI', '$' + fmtVal(kpi.wti.val, '', 2), kpi.wti.pct],
    ['Micron (DRAM)', '$' + fmtVal(kpi.mu.val, '', 2), kpi.mu.pct],
  ].map(([label, val, pct]) => `
    <div class="kpi-card"><div class="kpi-label">${label}</div><div class="kpi-val">${val}</div><div class="kpi-pct">${fmtPct(pct)}</div></div>
  `).join('')}
</div>

${exportSectionHtml(exportData)}

<div class="charts-grid">
  <div class="chart-card">
    <div class="chart-title-wrap">
      <div class="chart-title">ADR 지표 — KOSPI / KOSDAQ (3개월) <span class="chart-sub">· adrinfo.kr · 100=중립</span></div>
      ${latestAdr}
    </div>
    <div class="chart-legend"><span class="leg"><span class="leg-dot" style="background:#1d4ed8"></span>KOSPI ADR</span><span class="leg"><span class="leg-dot" style="background:#dc2626"></span>KOSDAQ ADR</span></div>
    <canvas id="cAdr"></canvas>
  </div>
  <div class="chart-card">
    <div class="chart-title-wrap">
      <div class="chart-title">CBOE SKEW 지수 (3개월) <span class="chart-sub">· 꼬리위험 프리미엄</span></div>
      ${latestSkew}
    </div>
    <div class="chart-legend"><span class="leg"><span class="leg-dot" style="background:#f59e0b"></span>SKEW</span></div>
    <canvas id="cSkew"></canvas>
  </div>
  <div class="chart-card">
    <div class="chart-title-wrap">
      <div class="chart-title">KOSPI 이평선 (1년)</div>
      ${latestKospi}
    </div>
    <div class="chart-legend">
      <span class="leg"><span class="leg-dot" style="background:#172554"></span>KOSPI</span>
      <span class="leg"><span class="leg-dot" style="background:#f59e0b"></span>SMA50</span>
      <span class="leg"><span class="leg-dot" style="background:#22d3ee"></span>SMA150</span>
      <span class="leg"><span class="leg-dot" style="background:#a78bfa"></span>SMA200</span>
    </div>
    <canvas id="cKospi"></canvas>
  </div>
  <div class="chart-card">
    <div class="chart-title-wrap">
      <div class="chart-title">삼성전자 vs SK하이닉스 수익률 (3개월)</div>
      ${latestSamHyn}
    </div>
    <canvas id="cSamHyn"></canvas>
  </div>
  <div class="chart-card">
    <div class="chart-title-wrap">
      <div class="chart-title">SOX vs NVIDIA 수익률 (3개월)</div>
      ${latestSoxNvda}
    </div>
    <canvas id="cSoxNvda"></canvas>
  </div>
  <div class="chart-card">
    <div class="chart-title-wrap">
      <div class="chart-title">VIX + 미국 10년물 (3개월)</div>
      ${latestVixTnx}
    </div>
    <canvas id="cVixTnx"></canvas>
  </div>
  <div class="chart-card">
    <div class="chart-title-wrap">
      <div class="chart-title">달러/원 + WTI (3개월)</div>
      ${latestFxWti}
    </div>
    <canvas id="cFxWti"></canvas>
  </div>
  <div class="chart-card">
    <div class="chart-title-wrap">
      <div class="chart-title">DRAM 프록시: Micron vs SOX (3개월)</div>
      ${latestSoxMu}
    </div>
    <canvas id="cSoxMu"></canvas>
  </div>
</div>`;

  renderCharts(D, exportData);
}

async function loadExportData() {
  const ts = Date.now();
  const errors = [];
  const onGhPages = location.hostname.includes('github.io');

  if (onGhPages) {
    try {
      const res = await fetchWithTimeout(`./semiconductor-export.json?t=${ts}`, 8000);
      if (res.ok) {
        const data = await res.json();
        data._live = false;
        return data;
      }
      errors.push(`semiconductor-export.json: ${res.status}`);
    } catch (e) {
      errors.push(`semiconductor-export.json: ${e.message}`);
    }
  }

  for (const base of API_CANDIDATES()) {
    const url = base
      ? `${base}/api/semiconductor-export?t=${ts}`
      : `/api/semiconductor-export?t=${ts}`;
    try {
      const res = await fetchWithTimeout(url, 15000);
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      data._live = true;
      return data;
    } catch (e) {
      errors.push(`${url}: ${e.message}`);
    }
  }

  if (!onGhPages) {
    try {
      const res = await fetchWithTimeout(`./semiconductor-export.json?t=${ts}`, 8000);
      if (res.ok) {
        const data = await res.json();
        data._live = false;
        return data;
      }
      errors.push(`semiconductor-export.json: ${res.status}`);
    } catch (e) {
      errors.push(`semiconductor-export.json: ${e.message}`);
    }
  }
  console.warn('수출 데이터 로드 실패:', errors.join(' | '));
  return null;
}

async function fetchLiveData() {
  const ts = Date.now();
  const errors = [];
  const onGhPages = location.hostname.includes('github.io');

  if (onGhPages) {
    try {
      const res = await fetchWithTimeout(`./data.json?t=${ts}`, 8000);
      if (res.ok) {
        const data = await res.json();
        data._live = false;
        return data;
      }
      errors.push(`data.json: ${res.status}`);
    } catch (e) {
      errors.push(`data.json: ${e.message}`);
    }
  }

  for (const base of API_CANDIDATES()) {
    const url = base ? `${base}/api/dashboard?t=${ts}` : `/api/dashboard?t=${ts}`;
    try {
      const res = await fetchWithTimeout(url, 15000);
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      data._live = true;
      return data;
    } catch (e) {
      errors.push(`${url}: ${e.message}`);
    }
  }

  if (!onGhPages) {
    try {
      const res = await fetchWithTimeout(`./data.json?t=${ts}`, 8000);
      if (res.ok) {
        const data = await res.json();
        data._live = false;
        return data;
      }
      errors.push(`data.json: ${res.status}`);
    } catch (e) {
      errors.push(`data.json: ${e.message}`);
    }
  }
  throw new Error(errors.join(' | '));
}

function showLoading() {
  document.getElementById('updated').textContent = '실시간 데이터 로딩 중...';
  document.getElementById('app-content').innerHTML = `
    <div class="loading-box"><div class="spinner"></div><p>시장 데이터 불러오는 중 (약 10~20초)</p></div>`;
}

function showError(msg) {
  document.getElementById('app-content').innerHTML = `
    <div class="loading-box error"><p>⚠️ 데이터 로드 실패</p><p class="err-detail">${msg}</p>
    <button type="button" onclick="loadDashboard()">다시 시도</button></div>`;
}

async function loadDashboard() {
  showLoading();
  try {
    await loadApiConfig();
    const [data, exportData] = await Promise.all([fetchLiveData(), loadExportData()]);
    renderDashboard(data, exportData);
  } catch (e) {
    showError(e.message);
  }
}

loadDashboard();
