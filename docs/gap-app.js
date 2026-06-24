/* eslint-disable no-unused-vars */
let sortCol = -1;
let sortAsc = true;

function fmtNum(v, dec = 0) {
  if (v == null) return 'N/A';
  return Number(v).toLocaleString('en-US', {
    maximumFractionDigits: dec,
    minimumFractionDigits: dec,
  });
}

function fmtPct(v, signed = true) {
  if (v == null) return 'N/A';
  const n = Number(v);
  return signed ? `${n >= 0 ? '+' : ''}${n.toFixed(2)}%` : `${n.toFixed(2)}%`;
}

function gapBg(v) {
  if (v == null) return '#f8fafc';
  if (v >= 30) return '#86efac';
  if (v >= 10) return '#d9f99d';
  if (v >= 0) return '#fef9c3';
  if (v >= -10) return '#fde68a';
  return '#bfdbfe';
}

function gapColor(v) {
  if (v == null) return '#64748b';
  return v >= 0 ? '#dc2626' : '#2563eb';
}

function td(val, bg = '#f8fafc', align = 'right', style = '') {
  return `<td style="padding:7px 10px;background:${bg};text-align:${align};white-space:nowrap;color:#1e293b;${style}">${val ?? 'N/A'}</td>`;
}

function buildRow(row, rank) {
  const gap = row.gap;
  const gbg = gapBg(gap);
  const gcol = gapColor(gap);
  let revNote = '';
  if (row.revPct != null) {
    const sign = fmtPct(row.revPct);
    revNote = row.revCrossFirm && row.revBasis
      ? `${sign}* vs ${row.revBasis}`
      : row.revBasis ? `${sign} vs ${row.revBasis}` : sign;
  }
  return `<tr>
    ${td(rank, '#f8fafc', 'center', 'color:#94a3b8')}
    ${td(`<span style="background:#e8f4fc;color:#1e3a5f;padding:2px 8px;border-radius:4px;font-size:.75rem">${row.sector}</span>`, '#fff', 'left')}
    ${td(`<span style="color:#94a3b8;font-size:.8rem">${row.ticker}</span><br><b>${row.name}</b>`, '#fff', 'left')}
    ${td(fmtPct(gap, false), gbg, 'right', `color:${gcol};font-weight:700`)}
    ${td(revNote || '—', '#f8fafc', 'right', row.revCrossFirm ? 'color:#7b5c00;font-style:italic' : '')}
    ${td(`${fmtNum(row.price, 0)}원`)}
    ${td(`${fmtNum(row.target, 0)}원`)}
    ${td(row.firm, '#f8fafc', 'left')}
    ${td(row.reportDate, '#f8fafc', 'left')}
    ${td(fmtNum(row.reportCount6m, 0))}
  </tr>`;
}

const COLS = ['#', '산업', '종목명', '괴리율%', '목표가변동', '현재가', '최근목표가', '증권사', '발표일', '건수(6M)'];

function buildTable(rows) {
  const th = COLS.map((c, i) =>
    `<th onclick="sortTable(${i})" style="padding:8px 10px;text-align:right;background:#1e293b;color:#94a3b8;font-size:.75rem;border-bottom:2px solid #334155;white-space:nowrap;cursor:pointer;user-select:none">${c} <span style="opacity:.5;font-size:.7rem">⇅</span></th>`
  ).join('');
  const body = rows.length
    ? rows.map((r, i) => buildRow(r, i + 1)).join('')
    : '<tr><td colspan="10" style="text-align:center;padding:40px;color:#94a3b8">데이터 없음</td></tr>';
  return `<table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderGap(data) {
  document.getElementById('updated').textContent =
    `업데이트: ${data.updated} · ${data._live === false ? '스냅샷' : '최신'}`;
  document.getElementById('app-content').innerHTML = `
    <div class="info-bar">
      <div>
        <span class="badge">${data.count}종목</span>
        &nbsp; Investing.com 해외 기관 목표가 · Yahoo 현재가 · 괴리율 = (목표−현재)/현재
        &nbsp;|&nbsp; * = 타 기관 목표 대비
      </div>
      <div style="font-size:.78rem;color:#475569">괴리율 높은 순 · Top ${data.count}</div>
    </div>
    ${buildTable(data.rows)}`;
  sortCol = -1;
  sortAsc = true;
}

async function fetchGapData() {
  const ts = Date.now();
  const res = await fetch(`./gap.json?t=${ts}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`gap.json: ${res.status}`);
  const data = await res.json();
  data._live = false;
  return data;
}

function showLoading() {
  document.getElementById('updated').textContent = '데이터 로딩 중...';
  document.getElementById('app-content').innerHTML =
    '<div class="loading-box"><div class="spinner"></div><p>괴리율 데이터 불러오는 중</p></div>';
}

function showError(msg) {
  document.getElementById('app-content').innerHTML = `
    <div class="loading-box error"><p>⚠️ 로드 실패</p><p style="font-size:.8rem;color:#94a3b8">${msg}</p>
    <button type="button" class="refresh-btn" onclick="loadGap()">다시 시도</button></div>`;
}

async function loadGap() {
  showLoading();
  try {
    const data = await fetchGapData();
    renderGap(data);
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
    const na = parseFloat(ca.replace(/[%,원*vs+\s]/g, ''));
    const nb = parseFloat(cb.replace(/[%,원*vs+\s]/g, ''));
    const v = Number.isNaN(na) || Number.isNaN(nb) ? ca.localeCompare(cb, 'ko') : na - nb;
    return sortAsc ? v : -v;
  });
  rows.forEach((r, i) => {
    if (col === 0) {
      const c = r.querySelector('td');
      if (c) c.textContent = i + 1;
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

loadGap();
