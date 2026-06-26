const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36';

const EXIT_META = {
  0: ['🟢 L0', '정상 — 전종목 보유', '#16a34a'],
  1: ['🟡 L1', '경계 — 신규 매수 자제', '#ca8a04'],
  2: ['🟠 L2', '경고 — 30% 비중 축소', '#ea580c'],
  3: ['🔴 L3', '위험 — 추가 50% 축소', '#dc2626'],
  4: ['🚨 L4', '전량청산 — 즉시 매도', '#7f1d1d'],
};

function kstNow() {
  return new Date().toLocaleString('sv-SE', { timeZone: 'Asia/Seoul' }).replace('T', ' ').slice(0, 16) + ' KST';
}

function pctChg(values) {
  if (!values || values.length < 2) return null;
  const a = values[values.length - 1];
  const b = values[values.length - 2];
  return Math.round((a - b) / b * 10000) / 100;
}

function norm(values) {
  if (!values?.length || !values[0]) return [];
  const base = values[0];
  return values.map(v => Math.round((v - base) / base * 100000) / 1000);
}

function alignAndNorm(datesA, valsA, datesB, valsB) {
  const mapB = Object.fromEntries(datesB.map((d, i) => [d, valsB[i]]));
  const dates = [], a = [], b = [];
  datesA.forEach((d, i) => {
    if (d in mapB) { dates.push(d); a.push(valsA[i]); b.push(mapB[d]); }
  });
  return [dates, norm(a), norm(b)];
}

function rollingMa(vals, n) {
  return vals.map((_, i) => (i + 1 >= n ? Math.round(vals.slice(i + 1 - n, i + 1).reduce((s, v) => s + v, 0) / n * 100) / 100 : null));
}

function computeExit(kospiVals, vixVals, tnxVals) {
  if (!kospiVals || kospiVals.length < 210) return null;
  const arr = kospiVals;
  const ma50 = arr.slice(-50).reduce((s, v) => s + v, 0) / 50;
  const ma150 = arr.slice(-150).reduce((s, v) => s + v, 0) / 150;
  const ma200 = arr.slice(-200).reduce((s, v) => s + v, 0) / 200;
  const ma200_21 = arr.length >= 221 ? arr.slice(-221, -21).reduce((s, v) => s + v, 0) / 200 : ma200;
  const curr = arr[arr.length - 1];
  const aligned = curr > ma50 && ma50 > ma150 && ma150 > ma200 && ma200 > ma200_21;
  const vix = vixVals?.length ? vixVals[vixVals.length - 1] : null;
  const tnx = tnxVals?.length ? tnxVals[tnxVals.length - 1] : null;
  if (curr < ma200) return [4, ma50, ma150, ma200, aligned];
  if (curr < ma150) return [3, ma50, ma150, ma200, aligned];
  if (ma50 < ma150) return [2, ma50, ma150, ma200, aligned];
  if (vix && vix > 30) return [2, ma50, ma150, ma200, aligned];
  if (!aligned) return [1, ma50, ma150, ma200, aligned];
  if (vix && vix > 25) return [1, ma50, ma150, ma200, aligned];
  if (tnx && tnx > 4.5) return [1, ma50, ma150, ma200, aligned];
  return [0, ma50, ma150, ma200, aligned];
}

function vixSideways(vixVals) {
  if (!vixVals || vixVals.length < 15) return false;
  return vixVals.slice(-15).every(v => v >= 20 && v <= 30);
}

async function yahooChart(symbol, range) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=${range}&interval=1d`;
  const res = await fetch(url, { headers: { 'User-Agent': UA } });
  if (!res.ok) throw new Error(`Yahoo ${symbol}: ${res.status}`);
  const json = await res.json();
  const result = json?.chart?.result?.[0];
  if (!result) throw new Error(`Yahoo ${symbol}: empty`);
  const meta = result.meta || {};
  const ts = result.timestamp || [];
  const closes = result.indicators?.quote?.[0]?.close || [];
  const dates = [];
  const values = [];
  ts.forEach((t, i) => {
    const c = closes[i];
    if (c != null) {
      dates.push(new Date(t * 1000).toISOString().slice(0, 10));
      values.push(Math.round(c * 10000) / 10000);
    }
  });

  const livePrice = meta.regularMarketPrice ?? meta.previousClose ?? meta.chartPreviousClose;
  const liveTs = meta.regularMarketTime || meta.currentTradingPeriod?.regular?.end;
  if (livePrice != null && dates.length) {
    const liveDate = liveTs
      ? new Date(liveTs * 1000).toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' })
      : dates[dates.length - 1];
    const rounded = Math.round(livePrice * 10000) / 10000;
    if (liveDate >= dates[dates.length - 1]) {
      if (liveDate === dates[dates.length - 1]) values[values.length - 1] = rounded;
      else { dates.push(liveDate); values.push(rounded); }
    }
  }

  return { dates, values, livePrice };
}

function parseAdrEmbed(html, name) {
  const re = new RegExp(`const ${name}=\\[(.*?)\\];`, 's');
  const m = html.match(re);
  if (!m) return [[], []];
  const pairs = [...m[1].matchAll(/\[(\d+),\s*([\d.]+)\]/g)];
  const dates = pairs.map(p => new Date(parseInt(p[1], 10)).toISOString().slice(0, 10));
  const values = pairs.map(p => Math.round(parseFloat(p[2]) * 100) / 100);
  return [dates, values];
}

async function fetchAdr(days = 63) {
  const res = await fetch('http://adrinfo.kr/chart', { headers: { 'User-Agent': UA } });
  if (!res.ok) throw new Error(`ADR: ${res.status}`);
  const html = await res.text();
  const [ksD, ksV] = parseAdrEmbed(html, 'kospi_adr');
  const [kqD, kqV] = parseAdrEmbed(html, 'kosdaq_adr');
  const kqMap = Object.fromEntries(kqD.map((d, i) => [d, kqV[i]]));
  const dates = [], kospi = [], kosdaq = [];
  ksD.forEach((d, i) => {
    if (d in kqMap) { dates.push(d); kospi.push(ksV[i]); kosdaq.push(kqMap[d]); }
  });
  const take = Math.min(days, dates.length);
  if (!take) return [[], [], []];
  return [dates.slice(-take), kospi.slice(-take), kosdaq.slice(-take)];
}

async function buildDashboardData() {
  const [
    sam, hyn, ks11, sox, nvda, vix, tnx, fx, wti, mu, skew, ks11_2y, adrPack,
  ] = await Promise.all([
    yahooChart('005930.KS', '3mo'),
    yahooChart('000660.KS', '3mo'),
    yahooChart('^KS11', '1y'),
    yahooChart('^SOX', '3mo'),
    yahooChart('NVDA', '3mo'),
    yahooChart('^VIX', '3mo'),
    yahooChart('^TNX', '3mo'),
    yahooChart('USDKRW=X', '3mo'),
    yahooChart('CL=F', '3mo'),
    yahooChart('MU', '3mo'),
    yahooChart('^SKEW', '3mo'),
    yahooChart('^KS11', '2y'),
    fetchAdr().catch(() => [[], [], []]),
  ]);

  const [adrD, adrKospi, adrKosdaq] = adrPack;
  const exitResult = computeExit(ks11_2y.values, vix.values, tnx.values);
  let exitLv = null, ma50 = null, ma150 = null, ma200 = null, aligned = null;
  if (exitResult) [exitLv, ma50, ma150, ma200, aligned] = exitResult;
  const sideways = vixSideways(vix.values);
  const [elBadge, elDesc, elColor] = EXIT_META[exitLv] ?? ['⚪', '데이터 부족', '#94a3b8'];

  const [samHynDates, samNorm, hynNorm] = alignAndNorm(sam.dates, sam.values, hyn.dates, hyn.values);
  const [soxNvdaDates, soxNorm, nvdaNorm] = alignAndNorm(sox.dates, sox.values, nvda.dates, nvda.values);
  const [soxMuDates, soxNorm2, muNorm] = alignAndNorm(sox.dates, sox.values, mu.dates, mu.values);

  const ksDates = ks11_2y.dates.slice(-252);
  const ksVals = ks11_2y.values.slice(-252);

  const latest = (v) => v?.length ? v[v.length - 1] : null;

  return {
    updated: kstNow(),
    exit: {
      level: exitLv,
      badge: elBadge,
      desc: elDesc,
      color: elColor,
      sideways,
      ma50, ma150, ma200, aligned,
    },
    kpi: {
      sam:  { val: latest(sam.values),  pct: pctChg(sam.values) },
      hyn:  { val: latest(hyn.values),  pct: pctChg(hyn.values) },
      ks11: { val: latest(ks11.values), pct: pctChg(ks11.values) },
      sox:  { val: latest(sox.values),  pct: pctChg(sox.values) },
      nvda: { val: latest(nvda.values), pct: pctChg(nvda.values) },
      vix:  { val: latest(vix.values),  pct: pctChg(vix.values) },
      tnx:  { val: latest(tnx.values),  pct: pctChg(tnx.values) },
      fx:   { val: latest(fx.values),   pct: pctChg(fx.values) },
      wti:  { val: latest(wti.values),  pct: pctChg(wti.values) },
      mu:   { val: latest(mu.values),   pct: pctChg(mu.values) },
      skew: { val: latest(skew.values), pct: pctChg(skew.values) },
      adr_kospi:  { val: latest(adrKospi),  pct: pctChg(adrKospi) },
      adr_kosdaq: { val: latest(adrKosdaq), pct: pctChg(adrKosdaq) },
    },
    charts: {
      kospi: { dates: ksDates, price: ksVals, ma50: rollingMa(ksVals, 50), ma150: rollingMa(ksVals, 150), ma200: rollingMa(ksVals, 200) },
      sam_hyn: { dates: samHynDates, sam: samNorm, hyn: hynNorm },
      sox_nvda: { dates: soxNvdaDates, sox: soxNorm, nvda: nvdaNorm },
      vix_tnx: { dates: vix.dates, vix: vix.values, tnx: tnx.values, tnx_dates: tnx.dates },
      fx_wti: { dates: fx.dates, fx: fx.values, wti: wti.values, wti_dates: wti.dates },
      sox_mu: { dates: soxMuDates, sox: soxNorm2, mu: muNorm },
      adr: { dates: adrD, kospi: adrKospi, kosdaq: adrKosdaq },
      skew: { dates: skew.dates, values: skew.values },
    },
  };
}

module.exports = { buildDashboardData, kstNow };
