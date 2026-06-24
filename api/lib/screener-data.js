const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36';
const MIN_DAYS = 200;
const MIN_VOLUME = 300_000;
const BATCH_SIZE = 10;

function kstNow() {
  return `${new Date().toLocaleString('sv-SE', { timeZone: 'Asia/Seoul' }).replace('T', ' ').slice(0, 16)} KST`;
}

function normalizeCode(ticker) {
  const t = String(ticker).trim();
  return /^\d+$/.test(t) ? t.padStart(6, '0') : t;
}

function toYfTicker(code) {
  return `${normalizeCode(code)}.KS`;
}

function round(v, n = 2) {
  if (v == null || Number.isNaN(v)) return null;
  const m = 10 ** n;
  return Math.round(v * m) / m;
}

async function fetchEtfList(minVolume = MIN_VOLUME) {
  const res = await fetch('https://finance.naver.com/api/sise/etfItemList.nhn', {
    headers: { 'User-Agent': UA, Referer: 'https://finance.naver.com/sise/etf.nhn' },
  });
  if (!res.ok) throw new Error(`Naver ETF list: ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  const text = new TextDecoder('euc-kr').decode(buf);
  const json = JSON.parse(text);
  const items = json?.result?.etfItemList || [];
  return items
    .map(item => ({
      code: normalizeCode(item.itemcode || ''),
      name: String(item.itemname || '').trim(),
      volume: parseInt(item.quant, 10) || 0,
    }))
    .filter(e => e.code && e.volume >= minVolume);
}

async function yahooCloses(symbol) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1y&interval=1d`;
  const res = await fetch(url, { headers: { 'User-Agent': UA } });
  if (!res.ok) return null;
  const json = await res.json();
  const closes = json?.chart?.result?.[0]?.indicators?.quote?.[0]?.close || [];
  return closes.filter(c => c != null);
}

function ema(values, span) {
  const k = 2 / (span + 1);
  let prev = values[0];
  const out = [prev];
  for (let i = 1; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

function calcRsi(prices, window = 14) {
  if (prices.length < window + 1) return null;
  let gains = 0;
  let losses = 0;
  for (let i = prices.length - window; i < prices.length; i++) {
    const d = prices[i] - prices[i - 1];
    if (d > 0) gains += d;
    else losses -= d;
  }
  const avgGain = gains / window;
  const avgLoss = losses / window;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

function calcMacdHist(prices) {
  const ema12 = ema(prices, 12);
  const ema26 = ema(prices, 26);
  const macd = ema12.map((v, i) => v - ema26[i]);
  const signal = ema(macd, 9);
  const hist = macd.map((v, i) => v - signal[i]);
  return {
    hist: hist[hist.length - 1],
    prev: hist.length > 1 ? hist[hist.length - 2] : null,
  };
}

function calcReturnsSharpe(prices, months = 6) {
  const daily = [];
  for (let i = 1; i < prices.length; i++) {
    daily.push((prices[i] - prices[i - 1]) / prices[i - 1]);
  }
  const lookback = Math.floor(months * 21);
  const rec = daily.length > lookback ? daily.slice(-lookback) : daily;
  if (rec.length < 2) return { sharpe: null, ret: null, vol: null };
  const ret = rec.reduce((acc, r) => acc * (1 + r), 1) - 1;
  const mean = rec.reduce((s, r) => s + r, 0) / rec.length;
  const variance = rec.reduce((s, r) => s + (r - mean) ** 2, 0) / (rec.length - 1);
  const std = Math.sqrt(variance);
  const mu = mean * 252;
  const stdAnn = std * Math.sqrt(252);
  const sharpe = stdAnn > 0 ? mu / stdAnn : null;
  const vol = std * Math.sqrt(rec.length);
  return { sharpe, ret, vol };
}

function calcSortino(prices, months = 6) {
  const daily = [];
  for (let i = 1; i < prices.length; i++) {
    daily.push((prices[i] - prices[i - 1]) / prices[i - 1]);
  }
  const lookback = Math.floor(months * 21);
  const rec = daily.length > lookback ? daily.slice(-lookback) : daily;
  if (!rec.length) return null;
  const mean = rec.reduce((s, r) => s + r, 0) / rec.length;
  const mu = mean * 252;
  const downs = rec.filter(r => r < 0);
  if (!downs.length) return null;
  const dMean = downs.reduce((s, r) => s + r, 0) / downs.length;
  const dVar = downs.reduce((s, r) => s + (r - dMean) ** 2, 0) / (downs.length - 1 || 1);
  const dstd = Math.sqrt(dVar) * Math.sqrt(252);
  return dstd > 0 ? mu / dstd : null;
}

function rollingMeanAt(prices, n, idx) {
  if (idx + 1 < n) return null;
  const slice = prices.slice(idx + 1 - n, idx + 1);
  return slice.reduce((s, v) => s + v, 0) / n;
}

async function screenOneEtf(etf) {
  const ticker = toYfTicker(etf.code);
  const prices = await yahooCloses(ticker);
  if (!prices || prices.length < MIN_DAYS) return null;

  const idx = prices.length - 1;
  const curr = prices[idx];
  const ma50 = rollingMeanAt(prices, 50, idx);
  const ma150 = rollingMeanAt(prices, 150, idx);
  const ma200 = rollingMeanAt(prices, 200, idx);
  const ma200_20 = prices.length > 220 ? rollingMeanAt(prices, 200, idx - 21) : ma200;

  if (!ma50 || !ma150 || !ma200 || !ma200_20) return null;

  const conds = [
    curr > ma50,
    curr > ma150,
    curr > ma200,
    ma50 > ma150,
    ma150 > ma200,
    ma200 > ma200_20,
  ];
  if (!conds.every(Boolean)) return null;

  const rsi = round(calcRsi(prices));
  const { hist, prev } = calcMacdHist(prices);
  const { sharpe, ret: ret6, vol: vol6 } = calcReturnsSharpe(prices, 6);
  const { ret: ret3 } = calcReturnsSharpe(prices, 3);
  const sortino = calcSortino(prices, 6);

  return {
    ticker,
    name: etf.name,
    price: Math.round(curr),
    volume: etf.volume,
    sma50150: round(ma50 - ma150, 0),
    sma150200: round(ma150 - ma200, 0),
    ret3: ret3 != null ? round(ret3 * 100, 2) : null,
    ret6: ret6 != null ? round(ret6 * 100, 2) : null,
    vol6: vol6 != null ? round(vol6 * 100, 2) : null,
    sharpe: sharpe != null ? round(sharpe, 2) : null,
    sortino: sortino != null ? round(sortino, 2) : null,
    rsi,
    macdHist: round(hist, 4),
    macdUp: hist != null && prev != null && hist > prev,
  };
}

async function buildScreenerData() {
  const etfList = await fetchEtfList();
  if (!etfList.length) throw new Error('ETF 목록 없음');

  const rows = [];
  for (let i = 0; i < etfList.length; i += BATCH_SIZE) {
    const batch = etfList.slice(i, i + BATCH_SIZE);
    const results = await Promise.all(batch.map(screenOneEtf));
    results.forEach(r => { if (r) rows.push(r); });
  }

  rows.sort((a, b) => (b.ret6 ?? -Infinity) - (a.ret6 ?? -Infinity));

  return {
    updated: kstNow(),
    count: rows.length,
    rows,
  };
}

module.exports = { buildScreenerData };
