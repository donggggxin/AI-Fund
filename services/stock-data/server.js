import http from 'node:http';
import { StockSDK } from 'stock-sdk';

const port = Number(process.env.PORT || 3000);
const maxSymbols = Number(process.env.MAX_SYMBOLS || 200);
const cacheTtlMs = Number(process.env.QUOTE_CACHE_TTL_MS || 5000);
const sdk = new StockSDK({
  retry: { maxRetries: 2, baseDelay: 300 },
  providerPolicies: { tencent: { timeout: 8000 } },
});
const cache = new Map();

function send(res, status, value) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(value));
}

function normalizeSymbols(value) {
  if (!Array.isArray(value) || value.length > maxSymbols) {
    throw new Error(`symbols must be an array with at most ${maxSymbols} items`);
  }
  const symbols = [...new Set(value.map(String).map((v) => v.trim()))];
  if (symbols.some((v) => !/^(sh|sz|bj)?[0-9A-Za-z.]{5,16}$/i.test(v))) {
    throw new Error('symbols contains an invalid value');
  }
  return symbols;
}

async function readJson(req) {
  let body = '';
  for await (const chunk of req) {
    body += chunk;
    if (body.length > 64 * 1024) throw new Error('request body too large');
  }
  return JSON.parse(body || '{}');
}

async function getQuotes(symbols) {
  const key = [...symbols].sort().join(',');
  const cached = cache.get(key);
  if (cached && Date.now() - cached.createdAt < cacheTtlMs) return cached.value;
  const quotes = await sdk.quotes.cnSimple(symbols);
  const value = {
    source: 'stock-sdk/tencent',
    asOf: new Date().toISOString(),
    quotes: quotes.map((quote) => ({
      symbol: quote.marketId || quote.code,
      code: quote.code,
      name: quote.name,
      price: quote.price,
      changePercent: quote.changePercent,
    })),
  };
  cache.set(key, { createdAt: Date.now(), value });
  return value;
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === 'GET' && req.url === '/health') {
      return send(res, 200, { status: 'ok', sdk: 'stock-sdk', version: '2.4.1' });
    }
    if (req.method === 'POST' && req.url === '/quotes/cn') {
      const symbols = normalizeSymbols((await readJson(req)).symbols);
      return send(res, 200, await getQuotes(symbols));
    }
    return send(res, 404, { error: 'not_found' });
  } catch (error) {
    return send(res, 502, {
      error: 'market_data_error',
      message: error instanceof Error ? error.message : String(error),
    });
  }
});

server.listen(port, '0.0.0.0', () => {
  console.log(`stock-data service listening on :${port}`);
});
