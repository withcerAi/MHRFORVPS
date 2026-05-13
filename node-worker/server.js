'use strict';

const http = require('http');
const https = require('https');
const dns = require('dns');
const net = require('net');
const zlib = require('zlib');
const { URL } = require('url');
const { EventEmitter } = require('events');

EventEmitter.defaultMaxListeners = Number(process.env.EVENT_MAX_LISTENERS || 80);

dns.setDefaultResultOrder('ipv4first');

// ===================== Config =====================
const PORT = Number(process.env.PORT || 8081);
const HOST = process.env.HOST || '0.0.0.0';

const AUTH_KEY = process.env.EXIT_NODE_PSK;
const HEALTH_KEY = process.env.HEALTH_KEY || 'MHR-Health-2026-Strong-Key';
if (!AUTH_KEY) {
  console.error('ERROR: EXIT_NODE_PSK is missing');
  process.exit(1);
}

const MAX_REQUEST_BODY = Number(process.env.MAX_REQUEST_BODY || 32 * 1024 * 1024);
const MAX_RESPONSE_BODY = Number(process.env.MAX_RESPONSE_BODY || 48 * 1024 * 1024);

const TIMEOUT_FAST_MS = Number(process.env.TIMEOUT_FAST_MS || 45000);
const TIMEOUT_SLOW_MS = Number(process.env.TIMEOUT_SLOW_MS || 90000);
const TIMEOUT_VIDEO_MS = Number(process.env.TIMEOUT_VIDEO_MS || 90000);

// Hard watchdog for one relay request.
// It must be slightly above video timeout. It prevents dead/stale requests
// from occupying MAX_INFLIGHT forever after Apps Script/client disconnects.
const REQUEST_HARD_TIMEOUT_MS = Number(
  process.env.REQUEST_HARD_TIMEOUT_MS ||
  Math.max(TIMEOUT_VIDEO_MS + 30000, 120000)
);

const MAX_SOCKETS = Number(process.env.MAX_SOCKETS || 64);
const MAX_FREE_SOCKETS = Number(process.env.MAX_FREE_SOCKETS || 4);
const MAX_INFLIGHT = Number(process.env.MAX_INFLIGHT || 48);

const MEMORY_GUARD_ENABLED = String(process.env.MEMORY_GUARD_ENABLED || '1') !== '0';
const MEMORY_SOFT_LIMIT_MB = Number(process.env.MEMORY_SOFT_LIMIT_MB || 420);
const MEMORY_HARD_LIMIT_MB = Number(process.env.MEMORY_HARD_LIMIT_MB || 620);
const MEMORY_CLEANUP_INTERVAL_MS = Number(process.env.MEMORY_CLEANUP_INTERVAL_MS || 15000);
const DESTROY_IDLE_SOCKETS_ON_PRESSURE = String(process.env.DESTROY_IDLE_SOCKETS_ON_PRESSURE || '1') !== '0';

let lastMemoryCleanupAt = 0;
let memoryCleanupCount = 0;

let inflight = 0;
let totalRequests = 0;
let totalErrors = 0;
let totalBytesOut = 0;
let totalBytesIn = 0;
const startedAt = Date.now();

// Tracks live relay jobs so aborted clients / stale upstream requests cannot
// keep inflight slots forever.
let requestSeq = 0;
const activeRequests = new Map();

// ===================== Headers =====================
const BAD_HEADERS = new Set([
  'host',
  'connection',
  'content-length',
  'transfer-encoding',
  'keep-alive',
  'te',
  'trailer',
  'upgrade',
  'proxy-connection',
  'proxy-authorization',
  'proxy-authenticate',
  'x-forwarded-for',
  'x-forwarded-host',
  'x-forwarded-proto',
  'x-forwarded-port',
  'x-real-ip',
  'forwarded',
  'via',
  'cf-connecting-ip'
]);

const HOP_BY_HOP_RESPONSE_HEADERS = new Set([
  'transfer-encoding',
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'upgrade',
  'content-length'
]);

const ALLOWED_METHODS = new Set([
  'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'
]);

// ===================== Host classes =====================
const SLOW_SITES = [
  'gemini.google.com',
  'ai.google.dev',
  'makersuite.google.com',
  'generativelanguage.googleapis.com',
  '.googleapis.com',
  'chatgpt.com',
  '.chatgpt.com',
  'openai.com',
  '.openai.com',
  'api.openai.com',
  '.oaistatic.com',
  '.oaiusercontent.com'
];

function hostMatches(hostname, rules) {
  if (!hostname) return false;
  const host = hostname.toLowerCase().replace(/\.$/, '');

  for (const rule of rules) {
    const r = String(rule || '').toLowerCase().trim();
    if (!r) continue;

    if (r.startsWith('.')) {
      if (host === r.slice(1) || host.endsWith(r)) return true;
    } else if (host === r) {
      return true;
    }
  }

  return false;
}

function isSlowHost(hostname) {
  return hostMatches(hostname, SLOW_SITES);
}

function isVideoLike(urlObj, headers) {
  const u = String(urlObj.href || '').toLowerCase();
  const path = String(urlObj.pathname || '').toLowerCase();
  const accept = String(headers.accept || headers.Accept || '').toLowerCase();
  const range = String(headers.range || headers.Range || '').toLowerCase();

  return (
    !!range ||
    u.includes('googlevideo.com') ||
    u.includes('videoplayback') ||
    u.includes('mime=video') ||
    u.includes('mime=audio') ||
    path.endsWith('.mp4') ||
    path.endsWith('.webm') ||
    path.endsWith('.m4s') ||
    path.endsWith('.m3u8') ||
    path.endsWith('.mpd') ||
    path.endsWith('.ts') ||
    accept.includes('video/') ||
    accept.includes('audio/')
  );
}

// ===================== Agents =====================
const httpAgent = new http.Agent({
  keepAlive: true,
  maxSockets: MAX_SOCKETS,
  maxFreeSockets: MAX_FREE_SOCKETS,
  timeout: 90000,
  scheduling: 'lifo'
});

const httpsAgent = new https.Agent({
  keepAlive: true,
  maxSockets: MAX_SOCKETS,
  maxFreeSockets: MAX_FREE_SOCKETS,
  timeout: 90000,
  scheduling: 'lifo'
});

function tuneSocketListeners(socket) {
  if (!socket || typeof socket.setMaxListeners !== 'function') return;

  const wanted = Number(process.env.SOCKET_MAX_LISTENERS || 80);
  try {
    if (!socket.getMaxListeners || socket.getMaxListeners() < wanted) {
      socket.setMaxListeners(wanted);
    }
  } catch {}
}

function tuneAgentSockets(agent) {
  if (!agent || typeof agent.on !== 'function') return;

  agent.on('free', socket => tuneSocketListeners(socket));
  agent.on('socket', socket => tuneSocketListeners(socket));
}

tuneAgentSockets(httpAgent);
tuneAgentSockets(httpsAgent);

// ===================== Security helpers =====================
function isPrivateIPv4(ip) {
  if (!net.isIPv4(ip)) return false;
  const p = ip.split('.').map(Number);
  const a = p[0], b = p[1];

  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    a >= 224
  );
}

function isPrivateIPv6(ip) {
  if (!net.isIPv6(ip)) return false;
  const n = ip.toLowerCase();

  return (
    n === '::1' ||
    n === '::' ||
    n.startsWith('fc') ||
    n.startsWith('fd') ||
    n.startsWith('fe80:') ||
    n.startsWith('::ffff:127.') ||
    n.startsWith('::ffff:10.') ||
    n.startsWith('::ffff:192.168.')
  );
}

function isBlockedHostname(hostname) {
  if (!hostname) return true;
  const host = hostname.toLowerCase().replace(/\.$/, '');

  if (host === 'localhost' || host === '0.0.0.0' || host === '::1') {
    return true;
  }

  if (net.isIPv4(host)) return isPrivateIPv4(host);
  if (net.isIPv6(host)) return isPrivateIPv6(host);

  return false;
}

function isPublicAddress(ip) {
  if (net.isIPv4(ip)) return !isPrivateIPv4(ip);
  if (net.isIPv6(ip)) return !isPrivateIPv6(ip);
  return false;
}

async function resolvePublicAddresses(hostname) {
  if (isBlockedHostname(hostname)) return [];

  try {
    const records = await dns.promises.lookup(hostname, {
      all: true,
      verbatim: false,
      family: 0
    });

    return records
      .filter(r => r && isPublicAddress(r.address))
      .sort((a, b) => {
        if (a.family === 4 && b.family !== 4) return -1;
        if (a.family !== 4 && b.family === 4) return 1;
        return 0;
      });
  } catch {
    return [];
  }
}

function makePinnedLookup(records) {
  return function lookup(_hostname, _opts, cb) {
    const r = records[0];
    if (!r) return cb(new Error('no_public_dns_record'));
    cb(null, r.address, r.family);
  };
}

// ===================== Header / body helpers =====================
function cleanHeaders(input, targetUrl) {
  const headers = {};
  const video = isVideoLike(targetUrl, input || {});

  if (input && typeof input === 'object') {
    for (const [key, value] of Object.entries(input)) {
      if (!key || typeof key !== 'string') continue;

      const lower = key.toLowerCase();
      if (BAD_HEADERS.has(lower)) continue;

      if (lower === 'accept-encoding') continue;

      if (Array.isArray(value)) {
        headers[key] = value.map(v => String(v));
      } else if (value !== undefined && value !== null) {
        headers[key] = String(value);
      }
    }
  }

  // For base64 relay and Range/video traffic, identity is safer.
  // This prevents mismatches between Node and Python.
  headers['Accept-Encoding'] = 'identity';

  return headers;
}

function responseHeadersClean(input) {
  const headers = {};

  for (const [key, value] of Object.entries(input || {})) {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP_RESPONSE_HEADERS.has(lower)) continue;
    headers[key] = value;
  }

  return headers;
}

function sendJson(res, httpStatus, obj) {
  if (res.writableEnded || res.headersSent) return;

  const body = Buffer.from(JSON.stringify(obj));

  res.writeHead(httpStatus, {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store',
    'Content-Length': body.length
  });

  res.end(body);
}

function relayError(status, message) {
  totalErrors++;
  return {
    s: status,
    h: { 'content-type': 'text/plain; charset=utf-8' },
    b: Buffer.from(String(message)).toString('base64')
  };
}

function nowMs() {
  return Date.now();
}

function safeStr(value, maxLen = 300) {
  return String(value === undefined || value === null ? '' : value).slice(0, maxLen);
}

function shortUrl(urlObj) {
  try {
    return `${urlObj.protocol}//${urlObj.hostname}${urlObj.pathname || '/'}`;
  } catch {
    return '-';
  }
}

function relayErrorDetailed(status, code, detail, meta = {}) {
  totalErrors++;

  const safeMeta = {};
  for (const [k, v] of Object.entries(meta || {})) {
    if (v === undefined || v === null) continue;
    safeMeta[k] = safeStr(v, 300);
  }

  const text = `${code}: ${detail || ''}\n${JSON.stringify(safeMeta)}`;

  return {
    s: status,
    h: {
      'content-type': 'text/plain; charset=utf-8',
      'x-exit-error-code': safeStr(code, 80),
      'x-exit-error-detail': safeStr(detail, 200)
    },
    b: Buffer.from(text).toString('base64')
  };
}

function relayLog(kind, fields = {}) {
  const parts = [`[relay] ${kind}`];
  for (const [k, v] of Object.entries(fields || {})) {
    if (v === undefined || v === null || v === '') continue;
    parts.push(`${k}=${safeStr(v, 220)}`);
  }
  console.log(parts.join(' '));
}

function relayWarn(kind, fields = {}) {
  const parts = [`[relay] ${kind}`];
  for (const [k, v] of Object.entries(fields || {})) {
    if (v === undefined || v === null || v === '') continue;
    parts.push(`${k}=${safeStr(v, 220)}`);
  }
  console.warn(parts.join(' '));
}

function beginInflightGuard(req, res) {
  const id = ++requestSeq;
  const started = Date.now();

  let released = false;
  let proxyReqRef = null;

  function ageMs() {
    return Date.now() - started;
  }

  function destroyProxy(reason) {
    if (!proxyReqRef || proxyReqRef.destroyed) return;
    try {
      proxyReqRef.destroy(new Error(reason || 'request_released'));
    } catch {}
  }

  function release(reason, destroy = false) {
    if (released) return false;

    released = true;
    activeRequests.delete(id);
    inflight = Math.max(0, inflight - 1);

    if (destroy) destroyProxy(reason);

    relayWarn('inflight_release', {
      id,
      reason,
      ageMs: ageMs(),
      inflight,
      active: activeRequests.size
    });

    return true;
  }

  const timer = setTimeout(() => {
    relayWarn('request_watchdog_timeout', {
      id,
      ageMs: ageMs(),
      inflight,
      maxInflight: MAX_INFLIGHT,
      hardTimeoutMs: REQUEST_HARD_TIMEOUT_MS
    });

    totalErrors++;
    release('request_watchdog_timeout', true);

    try {
      if (!res.writableEnded && !res.headersSent) {
        sendJson(res, 200, relayErrorDetailed(504, 'request_watchdog_timeout', 'stale_inflight_released', {
          ageMs: ageMs(),
          hardTimeoutMs: REQUEST_HARD_TIMEOUT_MS
        }));
      }
    } catch {}
  }, REQUEST_HARD_TIMEOUT_MS);

  if (timer && typeof timer.unref === 'function') timer.unref();

  activeRequests.set(id, {
    id,
    started,
    release,
    get ageMs() { return ageMs(); }
  });

  req.once('aborted', () => {
    release('client_request_aborted', true);
  });

  req.once('close', () => {
    // In Node.js, req.close can happen after the request body was read normally.
    // Do not destroy healthy relay jobs on normal close. req.aborted is the
    // reliable signal for a broken client upload.
    if (!released && req.aborted) {
      release('client_request_closed', true);
    }
  });

  res.once('close', () => {
    // Apps Script / client disconnected before we could reply.
    if (!released && !res.writableEnded) {
      release('client_response_closed', true);
    }
  });

  return {
    id,
    setProxyReq(proxyReq) {
      proxyReqRef = proxyReq;
    },
    release(reason, destroy = false) {
      try { clearTimeout(timer); } catch {}
      return release(reason, destroy);
    },
    get released() {
      return released;
    },
    get ageMs() {
      return ageMs();
    }
  };
}

function decodeBody(buffer, encoding) {
  const enc = String(encoding || '').trim().toLowerCase();
  if (!buffer || !buffer.length || !enc || enc === 'identity') return buffer;

  try {
    if (enc.includes('br')) return zlib.brotliDecompressSync(buffer);
    if (enc.includes('gzip')) return zlib.gunzipSync(buffer);
    if (enc.includes('deflate')) return zlib.inflateSync(buffer);
  } catch {
    return buffer;
  }

  return buffer;
}

function shouldDecodeResponse(headers, targetUrl) {
  if (isVideoLike(targetUrl, headers || {})) return false;
  const enc = String(headers['content-encoding'] || headers['Content-Encoding'] || '').toLowerCase();
  return !!enc && enc !== 'identity';
}



// ===================== Health auth =====================
function getClientIp(req) {
  return (
    req.socket?.remoteAddress ||
    req.connection?.remoteAddress ||
    ''
  );
}

function isLocalRequest(req) {
  const ip = getClientIp(req);
  return (
    ip === '127.0.0.1' ||
    ip === '::1' ||
    ip === '::ffff:127.0.0.1'
  );
}

function getHealthToken(req) {
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    return (
      req.headers['x-health-key'] ||
      req.headers['x-mhr-health-key'] ||
      url.searchParams.get('k') ||
      url.searchParams.get('key') ||
      ''
    );
  } catch {
    return req.headers['x-health-key'] || '';
  }
}

function isHealthAuthorized(req) {
  // localhost always allowed
  if (isLocalRequest(req)) return true;

  const token = String(getHealthToken(req) || '');
  return !!HEALTH_KEY && token === String(HEALTH_KEY);
}

// ===================== Memory guard =====================
function memorySnapshot() {
  const m = process.memoryUsage();
  return {
    rss: m.rss,
    heapUsed: m.heapUsed,
    heapTotal: m.heapTotal,
    external: m.external,
    arrayBuffers: m.arrayBuffers || 0,
    rssMB: Math.round(m.rss / 1024 / 1024),
    heapUsedMB: Math.round(m.heapUsed / 1024 / 1024),
    externalMB: Math.round(m.external / 1024 / 1024),
    arrayBuffersMB: Math.round((m.arrayBuffers || 0) / 1024 / 1024)
  };
}

function destroyIdleSockets(agent) {
  if (!agent || !agent.freeSockets) return 0;

  let destroyed = 0;
  for (const sockets of Object.values(agent.freeSockets)) {
    for (const socket of sockets || []) {
      try {
        socket.destroy();
        destroyed++;
      } catch {}
    }
  }
  return destroyed;
}

function runMemoryCleanup(reason) {
  if (!MEMORY_GUARD_ENABLED) return;

  const now = Date.now();
  if (now - lastMemoryCleanupAt < 5000) return;
  lastMemoryCleanupAt = now;

  let destroyed = 0;
  if (DESTROY_IDLE_SOCKETS_ON_PRESSURE) {
    destroyed += destroyIdleSockets(httpAgent);
    destroyed += destroyIdleSockets(httpsAgent);
  }

  if (global.gc) {
    try { global.gc(); } catch {}
  }

  memoryCleanupCount++;
  const m = memorySnapshot();
  console.warn(
    `[memory-guard] cleanup #${memoryCleanupCount} reason=${reason} rss=${m.rssMB}MB heap=${m.heapUsedMB}MB external=${m.externalMB}MB idleSocketsDestroyed=${destroyed}`
  );
}

function memoryPressureHigh() {
  if (!MEMORY_GUARD_ENABLED) return false;
  return memorySnapshot().rssMB >= MEMORY_SOFT_LIMIT_MB;
}

function memoryPressureCritical() {
  if (!MEMORY_GUARD_ENABLED) return false;
  return memorySnapshot().rssMB >= MEMORY_HARD_LIMIT_MB;
}

setInterval(() => {
  if (!MEMORY_GUARD_ENABLED) return;
  const m = memorySnapshot();

  if (m.rssMB >= MEMORY_SOFT_LIMIT_MB) {
    runMemoryCleanup(`interval_soft_limit_${m.rssMB}MB`);
  }

  if (m.rssMB >= MEMORY_HARD_LIMIT_MB) {
    console.error(`[memory-guard] hard limit reached rss=${m.rssMB}MB`);
  }
}, MEMORY_CLEANUP_INTERVAL_MS).unref();

// ===================== Server =====================
const server = http.createServer((req, res) => {
  if (req.method === 'GET') {
    if (!isHealthAuthorized(req)) {
      return sendJson(res, 403, { e: 'forbidden' });
    }

    return sendJson(res, 200, {
      ok: true,
      status: 'healthy',
      inflight,
      totalRequests,
      totalErrors,
      totalBytesIn,
      totalBytesOut,
      uptime_seconds: Math.floor((Date.now() - startedAt) / 1000),
      memory: memorySnapshot(),
      memoryLimits: {
        softLimitMB: MEMORY_SOFT_LIMIT_MB,
        hardLimitMB: MEMORY_HARD_LIMIT_MB,
        maxInflight: MAX_INFLIGHT,
        requestHardTimeoutMs: REQUEST_HARD_TIMEOUT_MS,
        activeRequests: activeRequests.size,
        oldestActiveMs: activeRequests.size
          ? Math.max(...Array.from(activeRequests.values()).map(x => x.ageMs || 0))
          : 0,
        maxSockets: MAX_SOCKETS,
        maxFreeSockets: MAX_FREE_SOCKETS,
        maxRequestBody: MAX_REQUEST_BODY,
        maxResponseBody: MAX_RESPONSE_BODY
      },
      agent: {
        httpSockets: Object.values(httpAgent.sockets || {}).reduce((n, a) => n + (a ? a.length : 0), 0),
        httpFreeSockets: Object.values(httpAgent.freeSockets || {}).reduce((n, a) => n + (a ? a.length : 0), 0),
        httpsSockets: Object.values(httpsAgent.sockets || {}).reduce((n, a) => n + (a ? a.length : 0), 0),
        httpsFreeSockets: Object.values(httpsAgent.freeSockets || {}).reduce((n, a) => n + (a ? a.length : 0), 0)
      },
      memoryCleanupCount
    });
  }

  if (req.method !== 'POST') {
    return sendJson(res, 405, { e: 'method_not_allowed' });
  }

  if (memoryPressureHigh()) {
    runMemoryCleanup('soft_before_request');
  }

  if (memoryPressureCritical()) {
    const m = memorySnapshot();
    runMemoryCleanup('critical_before_request');
    return sendJson(res, 503, relayErrorDetailed(503, 'exit_node_memory_pressure', 'critical_before_request', {
      rssMB: m.rssMB,
      heapUsedMB: m.heapUsedMB,
      externalMB: m.externalMB,
      arrayBuffersMB: m.arrayBuffersMB,
      hardLimitMB: MEMORY_HARD_LIMIT_MB
    }));
  }

  if (memoryPressureHigh() && inflight >= Math.max(4, Math.floor(MAX_INFLIGHT / 4))) {
    const m = memorySnapshot();
    return sendJson(res, 503, relayErrorDetailed(503, 'exit_node_memory_pressure', 'soft_limit_backpressure', {
      rssMB: m.rssMB,
      heapUsedMB: m.heapUsedMB,
      externalMB: m.externalMB,
      arrayBuffersMB: m.arrayBuffersMB,
      softLimitMB: MEMORY_SOFT_LIMIT_MB,
      inflight,
      maxInflight: MAX_INFLIGHT
    }));
  }

  if (inflight >= MAX_INFLIGHT) {
    return sendJson(res, 503, relayErrorDetailed(503, 'exit_node_busy', 'max_inflight_reached', {
      inflight,
      maxInflight: MAX_INFLIGHT
    }));
  }

  inflight++;
  totalRequests++;

  const life = beginInflightGuard(req, res);

  function finishEarly(status, obj) {
    life.release('finish_early');
    return sendJson(res, status, obj);
  }

  let totalSize = 0;
  const bodyParts = [];
  let requestTooLarge = false;

  req.on('data', chunk => {
    totalSize += chunk.length;

    if (totalSize > MAX_REQUEST_BODY) {
      requestTooLarge = true;
      req.destroy();
      return;
    }

    bodyParts.push(chunk);
  });

  req.on('error', () => {
    if (requestTooLarge) {
      return finishEarly(413, { e: 'request_too_large' });
    }

    return finishEarly(400, { e: 'request_error' });
  });

  req.on('end', async () => {
    try {
      if (requestTooLarge) {
        return finishEarly(413, { e: 'request_too_large' });
      }

      if (totalSize <= 0) {
        return finishEarly(400, { e: 'empty_body' });
      }

      totalBytesIn += totalSize;

      let data;
      try {
        data = JSON.parse(Buffer.concat(bodyParts, totalSize).toString('utf8'));
      } catch {
        return finishEarly(400, { e: 'bad_json' });
      }

      if (!data || typeof data !== 'object') {
        return finishEarly(400, { e: 'bad_json' });
      }

      if (String(data.k || '') !== AUTH_KEY) {
        return finishEarly(401, { e: 'unauthorized' });
      }

      if (!data.u || typeof data.u !== 'string') {
        return finishEarly(400, { e: 'missing_url' });
      }

      let targetUrl;
      try {
        targetUrl = new URL(data.u);
      } catch {
        return finishEarly(400, { e: 'bad_url' });
      }

      if (targetUrl.protocol !== 'http:' && targetUrl.protocol !== 'https:') {
        return finishEarly(400, { e: 'bad_protocol' });
      }

      const method = String(data.m || 'GET').toUpperCase();
      if (!ALLOWED_METHODS.has(method)) {
        return finishEarly(400, { e: 'bad_method' });
      }

      const hostname = targetUrl.hostname;

      const dnsStarted = nowMs();
      const records = await resolvePublicAddresses(hostname);
      const dnsMs = nowMs() - dnsStarted;

      if (!records.length) {
        relayWarn('blocked_or_dns_failed', {
          host: hostname,
          url: shortUrl(targetUrl),
          dnsMs
        });

        return finishEarly(200, relayErrorDetailed(403, 'blocked_or_dns_failed', 'no_public_dns_record', {
          host: hostname,
          url: shortUrl(targetUrl),
          dnsMs
        }));
      }

      const headers = cleanHeaders(data.h || {}, targetUrl);
      const isHttps = targetUrl.protocol === 'https:';
      const video = isVideoLike(targetUrl, headers);
      const slow = isSlowHost(hostname);
      const timeoutMs = video ? TIMEOUT_VIDEO_MS : (slow ? TIMEOUT_SLOW_MS : TIMEOUT_FAST_MS);

      let payload = null;

      if (data.b && method !== 'GET' && method !== 'HEAD') {
        try {
          payload = Buffer.from(String(data.b), 'base64');
        } catch {
          return finishEarly(400, { e: 'bad_base64' });
        }

        if (payload.length > MAX_REQUEST_BODY) {
          return finishEarly(413, { e: 'payload_too_large' });
        }
      }

      const reqStarted = nowMs();
      let socketAssignedAt = 0;
      let connectedAt = 0;
      let responseStartedAt = 0;
      let responseEndedAt = 0;
      let responseSizeSeen = 0;

      const options = {
        method,
        headers,
        agent: isHttps ? httpsAgent : httpAgent,
        timeout: timeoutMs,
        lookup: makePinnedLookup(records),
        family: records[0].family || 4
      };

      let finished = false;

      function done(httpStatus, obj) {
        if (finished || life.released) return;
        finished = true;
        life.release('done');

        try {
          const bytes = Buffer.byteLength(JSON.stringify(obj || {}));
          totalBytesOut += bytes;
        } catch {}

        return sendJson(res, httpStatus, obj);
      }

      const proxyReq = (isHttps ? https : http).request(targetUrl, options, proxyRes => {
        responseStartedAt = nowMs();

        relayLog('response_start', {
          status: proxyRes.statusCode || 0,
          method,
          host: hostname,
          url: shortUrl(targetUrl),
          ip: records[0].address,
          family: records[0].family,
          dnsMs,
          ttfbMs: responseStartedAt - reqStarted,
          timeoutMs,
          video,
          slow
        });

        let responseHeaders = responseHeadersClean(proxyRes.headers);
        const contentLength = Number(proxyRes.headers['content-length'] || 0);

        if (contentLength > MAX_RESPONSE_BODY) {
          relayWarn('response_too_large_header', {
            host: hostname,
            url: shortUrl(targetUrl),
            contentLength,
            maxResponseBody: MAX_RESPONSE_BODY,
            ip: records[0].address,
            note: 'GAS_JSON_BASE64_LIMIT_PROTECTION'
          });

          proxyReq.destroy();
          return done(200, relayErrorDetailed(502, 'response_too_large', 'content_length_exceeds_limit', {
            host: hostname,
            url: shortUrl(targetUrl),
            contentLength,
            maxResponseBody: MAX_RESPONSE_BODY,
            note: 'Apps Script + JSON/base64 relay cannot safely carry very large bodies'
          }));
        }

        let responseSize = 0;
        const chunks = [];

        proxyRes.on('data', chunk => {
          if (finished) return;

          responseSize += chunk.length;
          responseSizeSeen = responseSize;

          if (responseSize > MAX_RESPONSE_BODY) {
            relayWarn('response_too_large_stream', {
              host: hostname,
              url: shortUrl(targetUrl),
              responseSize,
              maxResponseBody: MAX_RESPONSE_BODY,
              elapsedMs: nowMs() - reqStarted,
              note: 'GAS_JSON_BASE64_LIMIT_PROTECTION'
            });

            proxyReq.destroy();
            return done(200, relayErrorDetailed(502, 'response_too_large', 'stream_exceeds_limit', {
              host: hostname,
              url: shortUrl(targetUrl),
              responseSize,
              maxResponseBody: MAX_RESPONSE_BODY,
              elapsedMs: nowMs() - reqStarted,
              note: 'Apps Script + JSON/base64 relay cannot safely carry very large bodies'
            }));
          }

          if (MEMORY_GUARD_ENABLED && memoryPressureCritical()) {
            const m = memorySnapshot();
            relayWarn('memory_pressure_during_response', {
              host: hostname,
              url: shortUrl(targetUrl),
              responseSize,
              rssMB: m.rssMB,
              hardLimitMB: MEMORY_HARD_LIMIT_MB,
              elapsedMs: nowMs() - reqStarted
            });

            proxyReq.destroy();
            return done(200, relayErrorDetailed(503, 'exit_node_memory_pressure', 'critical_during_response', {
              host: hostname,
              url: shortUrl(targetUrl),
              responseSize,
              rssMB: m.rssMB,
              hardLimitMB: MEMORY_HARD_LIMIT_MB
            }));
          }

          chunks.push(chunk);
        });

        proxyRes.on('end', () => {
          if (finished) return;

          responseEndedAt = nowMs();

          relayLog('response_done', {
            status: proxyRes.statusCode || 0,
            method,
            host: hostname,
            url: shortUrl(targetUrl),
            bytes: responseSize,
            totalMs: responseEndedAt - reqStarted,
            bodyMs: responseStartedAt ? responseEndedAt - responseStartedAt : '',
            ip: records[0].address
          });

          let body = Buffer.concat(chunks, responseSize);

          if (shouldDecodeResponse(responseHeaders, targetUrl)) {
            body = decodeBody(body, responseHeaders['content-encoding']);
            delete responseHeaders['content-encoding'];
            delete responseHeaders['Content-Encoding'];
          }

          delete responseHeaders['content-length'];
          delete responseHeaders['Content-Length'];

          const result = done(200, {
            s: proxyRes.statusCode || 502,
            h: responseHeaders,
            b: body.toString('base64')
          });

          chunks.length = 0;
          body = null;

          if (memoryPressureHigh()) {
            runMemoryCleanup('after_large_response');
          }

          return result;
        });

        proxyRes.on('error', err => {
          relayWarn('response_error', {
            host: hostname,
            url: shortUrl(targetUrl),
            code: err.code || '',
            message: err.message,
            elapsedMs: nowMs() - reqStarted,
            bytes: responseSizeSeen
          });

          return done(200, relayErrorDetailed(502, 'response_error', err.message, {
            host: hostname,
            url: shortUrl(targetUrl),
            code: err.code || '',
            elapsedMs: nowMs() - reqStarted,
            bytes: responseSizeSeen
          }));
        });
      });

      life.setProxyReq(proxyReq);

      proxyReq.setTimeout(timeoutMs, () => {
        const elapsed = nowMs() - reqStarted;
        const phase = responseStartedAt
          ? 'body_timeout_after_response_started'
          : connectedAt
            ? 'ttfb_timeout_after_connect'
            : socketAssignedAt
              ? 'connect_or_tls_timeout'
              : 'socket_not_assigned_timeout';

        relayWarn('target_timeout', {
          phase,
          method,
          host: hostname,
          url: shortUrl(targetUrl),
          elapsedMs: elapsed,
          dnsMs,
          connectedMs: connectedAt ? connectedAt - reqStarted : '',
          ttfbMs: responseStartedAt ? responseStartedAt - reqStarted : '',
          bytes: responseSizeSeen,
          ip: records[0].address,
          family: records[0].family,
          video,
          slow,
          timeoutMs
        });

        proxyReq.destroy();
        return done(200, relayErrorDetailed(504, 'target_timeout', phase, {
          method,
          host: hostname,
          url: shortUrl(targetUrl),
          elapsedMs: elapsed,
          dnsMs,
          connectedMs: connectedAt ? connectedAt - reqStarted : '',
          ttfbMs: responseStartedAt ? responseStartedAt - reqStarted : '',
          bytes: responseSizeSeen,
          ip: records[0].address,
          family: records[0].family,
          video,
          slow,
          timeoutMs
        }));
      });

      proxyReq.on('error', err => {
        if (finished) return;

        relayWarn('request_error', {
          method,
          host: hostname,
          url: shortUrl(targetUrl),
          code: err.code || '',
          syscall: err.syscall || '',
          message: err.message,
          elapsedMs: nowMs() - reqStarted,
          dnsMs,
          ip: records[0].address
        });

        return done(200, relayErrorDetailed(502, 'relay_error', err.message, {
          method,
          host: hostname,
          url: shortUrl(targetUrl),
          code: err.code || '',
          syscall: err.syscall || '',
          elapsedMs: nowMs() - reqStarted,
          dnsMs,
          ip: records[0].address
        }));
      });

      proxyReq.on('socket', socket => {
        socketAssignedAt = nowMs();
        tuneSocketListeners(socket);

        const onConnect = () => {
          connectedAt = nowMs();
          relayLog('socket_connect', {
            host: hostname,
            url: shortUrl(targetUrl),
            ip: records[0].address,
            connectMs: connectedAt - reqStarted
          });
        };

        const onSecureConnect = () => {
          connectedAt = nowMs();
          relayLog('socket_tls', {
            host: hostname,
            url: shortUrl(targetUrl),
            ip: records[0].address,
            tlsMs: connectedAt - reqStarted
          });
        };

        const onTimeout = () => {
          relayWarn('socket_timeout', {
            host: hostname,
            url: shortUrl(targetUrl),
            elapsedMs: nowMs() - reqStarted,
            connectedMs: connectedAt ? connectedAt - reqStarted : '',
            ip: records[0].address
          });
        };

        const onSocketError = err => {
          relayWarn('socket_error', {
            host: hostname,
            url: shortUrl(targetUrl),
            code: err.code || '',
            syscall: err.syscall || '',
            message: err.message,
            ip: records[0].address
          });
        };

        // Do not attach connect/tls listeners to already-reused keep-alive sockets.
        // Otherwise listeners accumulate on long-lived sockets.
        if (socket.connecting) {
          socket.once('connect', onConnect);
          socket.once('secureConnect', onSecureConnect);
        }

        socket.once('timeout', onTimeout);
        socket.once('error', onSocketError);

        const cleanupSocketListeners = () => {
          try { socket.removeListener('connect', onConnect); } catch {}
          try { socket.removeListener('secureConnect', onSecureConnect); } catch {}
          try { socket.removeListener('timeout', onTimeout); } catch {}
          try { socket.removeListener('error', onSocketError); } catch {}
        };

        proxyReq.once('close', cleanupSocketListeners);
        proxyReq.once('finish', cleanupSocketListeners);
      });

      if (payload && payload.length > 0) {
        proxyReq.write(payload);
      }

      proxyReq.end();

    } catch (err) {
      return finishEarly(200, relayError(500, 'server_error: ' + err.message));
    }
  });
});

// ===================== Server settings =====================
server.keepAliveTimeout = 30000;
server.headersTimeout = 35000;
server.requestTimeout = 0;
server.maxRequestsPerSocket = 100;

server.on('clientError', (err, socket) => {
  try {
    socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');
  } catch {}
});

server.listen(PORT, HOST, () => {
  console.log(`Secure optimized exit node running on ${HOST}:${PORT}`);
  console.log(`MAX_REQUEST_BODY=${MAX_REQUEST_BODY}`);
  console.log(`MAX_RESPONSE_BODY=${MAX_RESPONSE_BODY}`);
  console.log(`MAX_INFLIGHT=${MAX_INFLIGHT}`);
  console.log(`MAX_SOCKETS=${MAX_SOCKETS}`);
  console.log(`TIMEOUT_FAST_MS=${TIMEOUT_FAST_MS}`);
  console.log(`TIMEOUT_SLOW_MS=${TIMEOUT_SLOW_MS}`);
  console.log(`TIMEOUT_VIDEO_MS=${TIMEOUT_VIDEO_MS}`);
  console.log(`REQUEST_HARD_TIMEOUT_MS=${REQUEST_HARD_TIMEOUT_MS}`);
  console.log(`MEMORY_GUARD_ENABLED=${MEMORY_GUARD_ENABLED}`);
  console.log(`MEMORY_SOFT_LIMIT_MB=${MEMORY_SOFT_LIMIT_MB}`);
  console.log(`MEMORY_HARD_LIMIT_MB=${MEMORY_HARD_LIMIT_MB}`);
  console.log(`HEALTH_AUTH=enabled`);
});