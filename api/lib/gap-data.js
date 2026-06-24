const fs = require('fs');
const path = require('path');

const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
const GAP_ROOT = path.join(__dirname, '../../gap_pipeline');
const TOP_N = 100;
const MONTHS = 6;
const BATCH_SIZE = 8;
const BATCH_DELAY_MS = 350;
const REVISION_MAX_DAYS = 31 * 3;

const FOREIGN_FIRM_KEYWORDS = [
  'jpmorgan', 'jp morgan', 'morgan stanley', 'goldman', 'citi', 'citigroup',
  'barclays', 'ubs', 'credit suisse', 'deutsche', 'nomura', 'macquarie',
  'hsbc', 'clsa', 'bernstein', 'jefferies', 'bofa', 'bank of america',
  'wells fargo', 'rbc', 'societe', 'bnp', 'ing ', 'instinet',
];
const DOMESTIC_KEYWORDS = [
  '증권', '투자', '금융', '리서치', '한국', '미래에셋', '삼성', 'kb', 'nh',
  '신한', '하나', '대신', '한화', '키움', '메리츠', 'sk ', 'im', '유진',
];
const FIRM_ALIASES = {
  jpmorgan: 'JP Morgan',
  'jp morgan': 'JP Morgan',
  'goldman sachs': 'Goldman Sachs',
  citigroup: 'Citi',
  citi: 'Citi',
  'nomura/instinet': 'Nomura',
  nomura: 'Nomura',
  'morgan stanley': 'Morgan Stanley',
  barclays: 'Barclays',
  ubs: 'UBS',
  clsa: 'CLSA',
  macquarie: 'Macquarie',
};
const SLUG_ALIASES = { 'sk-hynix': 'sk-hynix-inc' };

function kstNow() {
  return `${new Date().toLocaleString('sv-SE', { timeZone: 'Asia/Seoul' }).replace('T', ' ').slice(0, 16)} KST`;
}

function normalizeCode(ticker) {
  const t = String(ticker || '').trim();
  return /^\d+$/.test(t) ? t.padStart(6, '0') : t;
}

function readText(relPath) {
  return fs.readFileSync(path.join(GAP_ROOT, relPath), 'utf8');
}

function parseCsv(relPath) {
  let text = readText(relPath);
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  const lines = text.trim().split(/\r?\n/);
  const headers = lines[0].split(',').map(h => h.trim());
  return lines.slice(1).filter(Boolean).map(line => {
    const vals = line.split(',');
    const row = {};
    headers.forEach((h, i) => { row[h] = (vals[i] || '').trim(); });
    return row;
  });
}

function normalizeFirm(name) {
  const key = String(name || '').trim().toLowerCase();
  return FIRM_ALIASES[key] || String(name || '').trim();
}

function isForeignFirm(name) {
  const lower = String(name || '').toLowerCase();
  if (DOMESTIC_KEYWORDS.some(k => lower.includes(k))) {
    if (!FOREIGN_FIRM_KEYWORDS.some(k => lower.includes(k))) return false;
  }
  return FOREIGN_FIRM_KEYWORDS.some(k => lower.includes(k));
}

function parseInvestingDate(text) {
  const m = String(text || '').match(/(\d{4})\s*년?\s*(\d{1,2})\s*월?\s*(\d{1,2})/);
  if (!m) return null;
  return `${m[1]}-${String(m[2]).padStart(2, '0')}-${String(m[3]).padStart(2, '0')}`;
}

function parseNumber(text) {
  if (!text || text === '-' || text === 'N/A') return null;
  const cleaned = String(text).replace(/,/g, '').replace(/[^\d.-]/g, '');
  const n = parseFloat(cleaned);
  return Number.isFinite(n) ? n : null;
}

function parseReportDate(value) {
  if (!value) return null;
  const d = new Date(String(value).trim());
  return Number.isNaN(d.getTime()) ? null : d;
}

function calcPriceGapPct(current, target) {
  if (current == null || target == null || current === 0) return null;
  return Math.round(((target - current) / current) * 10000) / 100;
}

function calcTargetRevisionPct(prev, target) {
  if (prev == null || target == null || prev === 0) return null;
  return Math.round(((target / prev) - 1) * 10000) / 100;
}

function filterOutlierTargets(rows) {
  const prices = rows.map(r => r.target_price).filter(v => v != null);
  if (prices.length < 2) return rows;
  const sorted = [...prices].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const floor = Math.max(median * 0.15, 1000);
  return rows.filter(r => (r.target_price || 0) >= floor);
}

function stripTdText(html) {
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

function parseConsensusHtml(html, ticker, stockName, slug) {
  const code = normalizeCode(ticker);
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - MONTHS * 31);
  const rows = [];
  const tableRe = /<table[^>]*class="[^"]*freeze-column-w-1[^"]*"[^>]*>([\s\S]*?)<\/table>/gi;
  let tableMatch = tableRe.exec(html);
  if (!tableMatch) {
    const alt = /<table[^>]*class="[^"]*w-full[^"]*border-collapse[^"]*"[^>]*>([\s\S]*?)<\/table>/gi;
    tableMatch = alt.exec(html);
  }
  const tables = [];
  if (tableMatch) tables.push(tableMatch[1]);
  else {
    const all = html.match(/<table[\s\S]*?<\/table>/gi) || [];
    tables.push(...all);
  }

  for (const tableHtml of tables) {
    const header = stripTdText((tableHtml.match(/<tr[^>]*>([\s\S]*?)<\/tr>/i) || [])[1] || '');
    if (!header.includes('평가') && !header.includes('목표')) continue;

    const trRe = /<tr[^>]*>([\s\S]*?)<\/tr>/gi;
    let trMatch;
    while ((trMatch = trRe.exec(tableHtml))) {
      const trHtml = trMatch[1];
      const tds = [];
      const tdRe = /<td[^>]*>([\s\S]*?)<\/td>/gi;
      let tdMatch;
      while ((tdMatch = tdRe.exec(trHtml))) tds.push(stripTdText(tdMatch[1]));
      if (tds.length < 6) continue;
      const firm = tds[0];
      if (!firm || firm.includes('평가')) continue;

      const target = parseNumber(tds[3]);
      if (target == null || target < 100) continue;
      const prevTarget = parseNumber(tds[5]);
      const reportDate = parseInvestingDate(tds[tds.length - 1]);
      if (reportDate) {
        const dt = parseReportDate(reportDate);
        if (dt && dt < cutoff) continue;
      }

      const region = isForeignFirm(firm) ? 'foreign' : 'domestic';
      rows.push({
        ticker: code,
        stock_name: stockName || '',
        securities_company: normalizeFirm(firm),
        report_date: reportDate,
        target_price: target,
        previous_target_price: prevTarget,
        previous_report_date: null,
        target_revision_pct: prevTarget && target
          ? Math.round((target / prevTarget - 1) * 10000) / 100
          : null,
        opinion: tds[2] || '',
        data_source: 'investing.com',
        source_region: region,
        report_nid: `investing_${slug}_${firm}_${reportDate}`,
        investing_slug: slug,
      });
    }
    if (rows.length) break;
  }
  return filterOutlierTargets(rows);
}

async function fetchInvestingConsensus(ticker, stockName, slug) {
  const resolved = SLUG_ALIASES[slug] || slug;
  const url = `https://kr.investing.com/equities/${resolved}-consensus-estimates`;
  const res = await fetch(url, {
    headers: {
      'User-Agent': UA,
      'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8',
      Referer: 'https://kr.investing.com/',
    },
  });
  if (!res.ok) throw new Error(`Investing ${res.status}`);
  const html = await res.text();
  return parseConsensusHtml(html, ticker, stockName, resolved);
}

async function yahooPrice(ticker, marketHint) {
  const code = normalizeCode(ticker);
  const markets = marketHint === 'KOSDAQ' ? ['KQ', 'KS'] : ['KS', 'KQ'];
  for (const mkt of markets) {
    const symbol = `${code}.${mkt}`;
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=5d&interval=1d`;
    try {
      const res = await fetch(url, { headers: { 'User-Agent': UA } });
      if (!res.ok) continue;
      const json = await res.json();
      const meta = json?.chart?.result?.[0]?.meta;
      const price = meta?.regularMarketPrice ?? meta?.previousClose;
      if (price != null) return Number(price);
      const closes = json?.chart?.result?.[0]?.indicators?.quote?.[0]?.close || [];
      const last = [...closes].reverse().find(c => c != null);
      if (last != null) return Number(last);
    } catch (_) { /* try next market */ }
  }
  return null;
}

function loadSlugCache() {
  return JSON.parse(readText('data/raw/investing_slug_cache.json'));
}

function loadCachedReports() {
  const p = path.join(GAP_ROOT, 'data/raw/sector_investing_reports.csv');
  if (!fs.existsSync(p)) return [];
  return parseCsv('data/raw/sector_investing_reports.csv').map(r => ({
    ticker: normalizeCode(r.ticker),
    stock_name: r.stock_name || '',
    securities_company: r.securities_company || '',
    report_date: r.report_date || null,
    target_price: parseNumber(r.target_price),
    previous_target_price: parseNumber(r.previous_target_price),
    previous_report_date: r.previous_report_date || null,
    target_revision_pct: parseNumber(r.target_revision_pct),
    opinion: r.opinion || '',
    data_source: r.data_source || 'investing.com',
    source_region: r.source_region || 'foreign',
    report_nid: r.report_nid || '',
    investing_slug: r.investing_slug || '',
  }));
}

function mergeReports(freshRows, cachedRows, refreshedTickers) {
  const kept = cachedRows.filter(r => !refreshedTickers.has(r.ticker));
  const merged = [...kept, ...freshRows];
  const seen = new Set();
  return merged.filter(r => {
    const key = `${r.ticker}|${r.securities_company}|${r.report_date}|${r.target_price}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

async function mapInBatches(items, batchSize, fn) {
  const out = [];
  for (let i = 0; i < items.length; i += batchSize) {
    const chunk = items.slice(i, i + batchSize);
    const part = await Promise.all(chunk.map(fn));
    out.push(...part);
    if (i + batchSize < items.length) {
      await new Promise(r => setTimeout(r, BATCH_DELAY_MS));
    }
  }
  return out;
}

function filterWindow(reports, months) {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - months * 31);
  return reports.filter(r => {
    const dt = parseReportDate(r.report_date);
    return dt && dt >= cutoff && r.target_price != null;
  });
}

function resolveTargetRevision(tickerReports, latestRow) {
  const empty = { revPct: null, revBasis: '', revCrossFirm: false };
  const latestFirm = String(latestRow.securities_company || '');
  const latestDt = parseReportDate(latestRow.report_date);
  const latestTarget = latestRow.target_price;
  if (!latestDt || latestTarget == null) return empty;

  const sub = [...tickerReports]
    .map(r => ({ ...r, _dt: parseReportDate(r.report_date) }))
    .filter(r => r._dt)
    .sort((a, b) => a._dt - b._dt);

  const same = sub.filter(r => r.securities_company === latestFirm);
  if (same.length >= 2) {
    const prev = same[same.length - 2];
    const days = (latestDt - prev._dt) / (1000 * 60 * 60 * 24);
    if (days <= REVISION_MAX_DAYS) {
      return {
        revPct: calcTargetRevisionPct(prev.target_price, latestTarget),
        revBasis: latestFirm,
        revCrossFirm: false,
      };
    }
  }

  const prevTarget = latestRow.previous_target_price;
  const prevDate = parseReportDate(latestRow.previous_report_date);
  if (prevTarget != null && prevDate) {
    const days = (latestDt - prevDate) / (1000 * 60 * 60 * 24);
    if (days <= REVISION_MAX_DAYS) {
      return {
        revPct: calcTargetRevisionPct(prevTarget, latestTarget),
        revBasis: latestFirm,
        revCrossFirm: false,
      };
    }
  }

  const others = sub.filter(r => r.securities_company !== latestFirm && r._dt < latestDt);
  if (!others.length) return empty;
  const ref = others[others.length - 1];
  return {
    revPct: calcTargetRevisionPct(ref.target_price, latestTarget),
    revBasis: ref.securities_company,
    revCrossFirm: true,
  };
}

function buildStockSummary(reports, priceMap, leaders) {
  const sectorMap = {};
  const nameMap = {};
  leaders.forEach(l => {
    const code = normalizeCode(l.ticker);
    sectorMap[code] = l.sector;
    nameMap[code] = l.stock_name;
  });

  const windowed = filterWindow(reports, MONTHS);
  const rows = [];
  const byTicker = new Map();
  windowed.forEach(r => {
    const code = normalizeCode(r.ticker);
    if (!byTicker.has(code)) byTicker.set(code, []);
    byTicker.get(code).push(r);
  });

  for (const [code, grp] of byTicker) {
    if (!sectorMap[code]) continue;
    const foreign = grp.filter(r => r.source_region === 'foreign');
    if (!foreign.length) continue;

    const withDt = foreign.map(r => ({ ...r, _dt: parseReportDate(r.report_date) }))
      .filter(r => r._dt)
      .sort((a, b) => a._dt - b._dt || a.target_price - b.target_price);
    if (!withDt.length) continue;

    const targets = withDt.map(r => r.target_price);
    const latest = withDt[withDt.length - 1];
    const current = priceMap[code];
    const rev = resolveTargetRevision(withDt, latest);

    rows.push({
      sector: sectorMap[code] || '',
      name: nameMap[code] || latest.stock_name || '',
      ticker: code,
      price: current,
      target: latest.target_price,
      avgTarget6m: Math.round((targets.reduce((s, v) => s + v, 0) / targets.length) * 100) / 100,
      highTarget6m: Math.max(...targets),
      lowTarget6m: Math.min(...targets),
      gap: calcPriceGapPct(current, latest.target_price),
      revPct: rev.revPct,
      revBasis: rev.revBasis,
      revCrossFirm: rev.revCrossFirm,
      firm: latest.securities_company,
      reportDate: latest.report_date,
      reportCount6m: withDt.length,
    });
  }
  return rows;
}

function topGapStocks(summary, n) {
  return summary
    .filter(r => r.gap != null)
    .sort((a, b) => b.gap - a.gap)
    .slice(0, n);
}

async function buildGapData() {
  const leaders = parseCsv('data/processed/sector_leaders.csv');
  const slugCache = loadSlugCache();
  const cachedReports = loadCachedReports();

  const targets = leaders
    .map(l => ({
      ticker: normalizeCode(l.ticker),
      stockName: l.stock_name,
      market: l.market,
      slug: slugCache[normalizeCode(l.ticker)],
    }))
    .filter(l => l.slug);

  const freshRows = [];
  const refreshedTickers = new Set();
  const errors = [];

  await mapInBatches(targets, BATCH_SIZE, async ({ ticker, stockName, slug }) => {
    try {
      const rows = await fetchInvestingConsensus(ticker, stockName, slug);
      if (rows.length) {
        freshRows.push(...rows);
        refreshedTickers.add(ticker);
      }
    } catch (e) {
      errors.push(`${ticker}:${e.message}`);
    }
    return null;
  });

  const allReports = mergeReports(freshRows, cachedReports, refreshedTickers);

  const priceMap = {};
  const leaderTickers = [...new Set(leaders.map(l => normalizeCode(l.ticker)))];
  await mapInBatches(leaderTickers, BATCH_SIZE, async code => {
    const leader = leaders.find(l => normalizeCode(l.ticker) === code);
    priceMap[code] = await yahooPrice(code, leader?.market);
    return null;
  });

  const summary = buildStockSummary(allReports, priceMap, leaders);
  const top = topGapStocks(summary, TOP_N);

  return {
    updated: kstNow(),
    count: top.length,
    title: `목표주가 괴리율 Top ${top.length} (Investing 해외)`,
    refreshedTickers: refreshedTickers.size,
    totalTargets: targets.length,
    errors: errors.length ? errors.slice(0, 5) : undefined,
    rows: top,
    _live: true,
  };
}

module.exports = { buildGapData };
