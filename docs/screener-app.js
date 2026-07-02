/* eslint-disable no-unused-vars */
let sortCol = -1;
let sortAsc = true;

const API_CANDIDATES = () => {
  const q = new URLSearchParams(location.search).get('api');
  if (q) return [q.replace(/\/$/, '')];
  const fromCfg = window.__DASHBOARD_API_BASES || [];
  const list = [...fromCfg];
  if (location.hostname.includes('vercel.app')) list.unshift('');
  if (!list.includes('https://minervini.vercel.app')) list.push('https://minervini.vercel.app');
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

async function fetchWithTimeout(url, timeoutMs = 120000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { cache: 'no-store', signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

function lerpColor(v, lo, hi, cLo, cHi) {
  if (v == null || lo === hi) return '#f8fafc';
  const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  const parse = h => {
    const x = h.replace('#', '');
    return [parseInt(x.slice(0, 2), 16), parseInt(x.slice(2, 4), 16), parseInt(x.slice(4, 6), 16)];
  };
  const [r0, g0, b0] = parse(cLo);
  const [r1, g1, b1] = parse(cHi);
  const r = Math.round(r0 + t * (r1 - r0));
  const g = Math.round(g0 + t * (g1 - g0));
  const b = Math.round(b0 + t * (b1 - b0));
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
}

function colorPct(v, lo = 0, hi = 50) {
  if (v == null) return '#f8fafc';
  if (v < 0) return lerpColor(v, -30, 0, '#fca5a5', '#fef9c3');
  return lerpColor(v, lo, hi, '#fef9c3', '#86efac');
}

function colorNeutral(v, lo = 0, hi = 3) {
  if (v == null) return '#f8fafc';
  if (v < 0) return '#fca5a5';
  return lerpColor(v, lo, hi, '#fef9c3', '#86efac');
}

function colorRsi(v) {
  if (v == null) return '#f8fafc';
  if (v < 30) return '#bfdbfe';
  if (v > 70) return '#fca5a5';
  return lerpColor(v, 30, 70, '#fef9c3', '#86efac');
}

function colorMacd(v, colVals) {
  const numeric = colVals.filter(x => x != null);
  if (!numeric.length || v == null) return '#f8fafc';
  const lo = Math.min(...numeric);
  const hi = Math.max(...numeric);
  if (v < 0) {
    const loNeg = lo < 0 ? lo : -1e-9;
    return lerpColor(v, loNeg, 0, '#fca5a5', '#fef9c3');
  }
  const hiPos = hi > 0 ? hi : 1e-9;
  return lerpColor(v, 0, hiPos, '#fef9c3', '#86efac');
}

function colorSma(v) {
  if (v == null || v <= 0) return '#fef9c3';
  return lerpColor(v, 0, 300, '#fef9c3', '#86efac');
}

function td(val, bg = '#f8fafc', fmt = v => v, align = 'right') {
  const display = val != null ? fmt(val) : 'N/A';
  return `<td style="padding:7px 10px;background:${bg};text-align:${align};white-space:nowrap;color:#1e293b">${display}</td>`;
}

function buildRow(row, rank, mcdVals) {
  return `<tr>
    <td style="padding:7px 10px;text-align:center;color:#94a3b8;background:#f8fafc">${rank}</td>
    <td style="padding:7px 10px;background:#ffffff;white-space:nowrap"><span style="color:#94a3b8;font-size:.8rem">${row.ticker}</span><br><b style="font-size:.9rem;color:#1e293b">${(row.name || '').slice(0, 18)}</b></td>
    ${td(row.price, '#f8fafc', v => `${v.toLocaleString()}원`)}
    ${td(row.volume, '#f8fafc', v => v.toLocaleString())}
    ${td(row.ret3, colorPct(row.ret3, 0, 30), v => `${v.toFixed(1)}%`)}
    ${td(row.ret6, colorPct(row.ret6, 0, 50), v => `${v.toFixed(1)}%`)}
    ${td(row.vol6, '#f8fafc', v => `${v.toFixed(1)}%`)}
    ${td(row.sharpe, colorNeutral(row.sharpe), v => v.toFixed(2))}
    ${td(row.sortino, colorNeutral(row.sortino), v => v.toFixed(2))}
    ${td(row.sma50150, colorSma(row.sma50150), v => v.toLocaleString(undefined, { maximumFractionDigits: 0 }))}
    ${td(row.sma150200, colorSma(row.sma150200), v => v.toLocaleString(undefined, { maximumFractionDigits: 0 }))}
    ${td(row.rsi, colorRsi(row.rsi), v => v.toFixed(1))}
    ${td(row.macdHist, colorMacd(row.macdHist, mcdVals), v => v.toFixed(4))}
    <td style="padding:7px 10px;background:#f8fafc;text-align:center">${row.macdUp ? '✅' : '❌'}</td>
  </tr>`;
}

const COLS = ['#', '종목명', '현재가', '거래량', '3개월<br>수익률', '6개월<br>수익률',
  '6개월<br>변동성', '샤프지수', '소르티노', 'SMA50<br>-150', 'SMA150<br>-200', 'RSI', 'MACD<br>Hist', 'MACD↑'];

function buildTable(rows) {
  const mcdVals = rows.map(r => r.macdHist);
  const thRow = COLS.map((c, i) =>
    `<th onclick="sortTable(${i})" data-col="${i}" style="padding:8px 10px;text-align:right;background:#1e293b;color:#94a3b8;font-size:.75rem;border-bottom:2px solid #334155;white-space:nowrap;cursor:pointer;user-select:none">${c} <span style="opacity:.5;font-size:.7rem">⇅</span></th>`
  ).join('');
  const body = rows.length
    ? rows.map((r, i) => buildRow(r, i + 1, mcdVals)).join('')
    : '<tr><td colspan="14" style="text-align:center;padding:40px;color:#94a3b8">조건을 만족하는 ETF가 없습니다</td></tr>';
  return `<table><thead><tr>${thRow}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderScreener(data) {
  const live = data._live ? '실시간 (네이버 현재가)' : '스냅샷 (CI)';
  document.getElementById('updated').textContent = `업데이트: ${data.updated} · ${live}`;
  document.getElementById('app-content').innerHTML = `
    <div class="info-bar">
      <div>
        <span class="badge">${data.count}개 통과</span>
        &nbsp; 미너비니 6조건: 주가 &gt; SMA50/150/200 · SMA 정배열 · SMA200 상승추세
        &nbsp;|&nbsp; 기준 거래량: 30만주 이상
      </div>
      <div style="color:#475569;font-size:.78rem">6개월 수익률 높은 순</div>
    </div>
    ${buildTable(data.rows)}`;
  sortCol = -1;
  sortAsc = true;
}

async function fetchLiveData() {
  const ts = Date.now();
  const errors = [];
  for (const base of API_CANDIDATES()) {
    const url = base ? `${base}/api/screener?t=${ts}` : `/api/screener?t=${ts}`;
    try {
      const res = await fetchWithTimeout(url, 120000);
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      data._live = true;
      return data;
    } catch (e) {
      errors.push(`${url}: ${e.message}`);
    }
  }
  try {
    const res = await fetchWithTimeout(`./screener.json?t=${ts}`, 8000);
    if (res.ok) {
      const data = await res.json();
      data._live = false;
      return data;
    }
    errors.push(`screener.json: ${res.status}`);
  } catch (e) { errors.push(`screener.json: ${e.message}`); }
  throw new Error(errors.join(' | '));
}

function showLoading() {
  document.getElementById('updated').textContent = '실시간 데이터 로딩 중...';
  document.getElementById('app-content').innerHTML =
    '<div class="loading-box"><div class="spinner"></div><p>ETF 스크리닝 중 (실시간 API, 최대 2분)</p></div>';
}

function showError(msg) {
  document.getElementById('app-content').innerHTML = `
    <div class="loading-box error"><p>⚠️ 데이터 로드 실패</p><p class="err-detail">${msg}</p>
    <button type="button" onclick="loadScreener()">다시 시도</button></div>`;
}

async function loadScreener() {
  showLoading();
  try {
    await loadApiConfig();
    const data = await fetchLiveData();
    renderScreener(data);
  } catch (e) {
    showError(e.message);
  }
}

function sortTable(col) {
  const tbody = document.querySelector('tbody');
  if (!tbody) return;
  const rows = Array.from(tbody.querySelectorAll('tr'));
  if (rows.length === 1 && rows[0].querySelectorAll('td').length === 1) return;
  if (sortCol === col) sortAsc = !sortAsc;
  else { sortCol = col; sortAsc = true; }
  rows.sort((a, b) => {
    const ca = a.querySelectorAll('td')[col]?.textContent.trim() ?? '';
    const cb = b.querySelectorAll('td')[col]?.textContent.trim() ?? '';
    const na = parseFloat(ca.replace(/[%,원]/g, ''));
    const nb = parseFloat(cb.replace(/[%,원]/g, ''));
    const v = Number.isNaN(na) || Number.isNaN(nb) ? ca.localeCompare(cb, 'ko') : na - nb;
    return sortAsc ? v : -v;
  });
  rows.forEach((r, i) => {
    if (col === 0) {
      const rankCell = r.querySelector('td');
      if (rankCell) rankCell.textContent = i + 1;
    }
    tbody.appendChild(r);
  });
  document.querySelectorAll('thead th').forEach((th, i) => {
    const sp = th.querySelector('span');
    if (sp) {
      sp.textContent = i === col ? (sortAsc ? '▲' : '▼') : '⇅';
      sp.style.opacity = i === col ? '1' : '.5';
    }
  });
}

loadScreener();
