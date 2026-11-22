/*
 * LiteLink Kiosk: rules for a LiteLink web session (LiteOS or Web kiosk).
 * Settings come from config.js, written by LiteLink for each session:
 *   home         the connection's address
 *   allowed      hosts pages may be opened from (empty = any); the home host is included
 *   idleMinutes  after this long without input, clear the session and go home (0 = never)
 *   liteos       the LiteOS origin of a LiteOS session ("" otherwise): its pages get window.LiteLink
 * Only top-level pages are limited: LiteOS's Browser frames other sites by design.
 */
'use strict';
importScripts('config.js');
const C = self.LITELINK || { home: 'about:blank', allowed: [], idleMinutes: 0 };

async function applyRules() {
  const old = await chrome.declarativeNetRequest.getSessionRules();
  const addRules = [];
  if (C.allowed && C.allowed.length) {
    addRules.push({
      id: 1, priority: 1,
      action: { type: 'redirect', redirect: { extensionPath: '/blocked.html' } },
      condition: { resourceTypes: ['main_frame'] },
    }, {
      id: 2, priority: 2,
      action: { type: 'allow' },
      condition: { resourceTypes: ['main_frame'], requestDomains: C.allowed },
    });
  }
  await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: old.map((r) => r.id), addRules });
}

// Nobody has used the device for a while: forget this session and start again at home.
async function reset() {
  await chrome.browsingData.remove({ since: 0 }, {
    cookies: true, cache: true, localStorage: true, indexedDB: true, serviceWorkers: true,
    cacheStorage: true, fileSystems: true, history: true, formData: true, passwords: true,
  });
  const tabs = await chrome.tabs.query({});
  if (!tabs.length) { await chrome.tabs.create({ url: C.home }); return; }
  await chrome.tabs.update(tabs[0].id, { url: C.home, active: true });
  const rest = tabs.slice(1).map((t) => t.id);
  if (rest.length) await chrome.tabs.remove(rest);
}

function watchIdle() {
  if (!(C.idleMinutes > 0)) return;
  chrome.idle.setDetectionInterval(Math.max(15, Math.round(C.idleMinutes * 60)));
  chrome.idle.onStateChanged.addListener((state) => { if (state !== 'active') reset(); });
}

// LiteOS sessions: window.LiteLink (litelink-api.js) reaches the device through LiteLink's native host
// (litelink-devices). Every LiteOS frame's relay (relay.js) opens a port here; one native connection is shared by
// all of them, opened with the first and closed with the last. Requests are checked here and again in the host,
// which decides what the session may do.
const HOST = 'com.litelink.devices';
const OPS = new Set(['hello', 'list', 'browse', 'read', 'upload', 'write', 'download', 'mkdir', 'eject', 'cancel',
  'audio.state', 'audio.set', 'network.state', 'network.wifi', 'system.info', 'session.end',
  'system.lock', 'session.settings', 'power.restart', 'power.shutdown',
  'bluetooth.state', 'bluetooth.scan', 'bluetooth.connect', 'bluetooth.pair', 'bluetooth.remove', 'bluetooth.power',
  'display.state', 'display.set', 'input.state', 'input.layout', 'time.state', 'time.zone',
  'print.printers', 'print.submit', 'print.jobs', 'print.cancel', 'print.find', 'print.add', 'print.remove']);
const AUDIO_KEYS = { volume: 'number', muted: 'boolean', output: 'string', inputVolume: 'number', inputMuted: 'boolean', input: 'string' };
// print options a page may give, and their types (the host checks the values against the printer)
const PRINT_KEYS = { title: 'string', copies: 'number', pages: 'string', duplex: 'string', color: 'boolean', media: 'string', orientation: 'string' };
const bridge = { native: null, frames: new Map(), pending: new Map(), seq: 0 };

function valid(m) {
  if (!m || typeof m !== 'object' || typeof m.id !== 'string' || m.id.length > 40 || !OPS.has(m.op)) return false;
  const str = (v) => v === undefined || (typeof v === 'string' && v.length <= 4096);
  const int = (v) => v === undefined || (Number.isInteger(v) && v >= 0);
  const headers = m.headers === undefined || (m.headers && typeof m.headers === 'object' && !Array.isArray(m.headers) &&
    Object.keys(m.headers).length <= 16 && Object.values(m.headers).every((v) => typeof v === 'string' && v.length <= 4096));
  const bool = (v) => v === undefined || typeof v === 'boolean';
  const data = m.data === undefined || (typeof m.data === 'string' && m.data.length <= 720000); // 512 KiB as base64
  const changes = m.changes === undefined || (m.changes && typeof m.changes === 'object' && !Array.isArray(m.changes) &&
    Object.entries(m.changes).every(([k, v]) => typeof v === AUDIO_KEYS[k] && (typeof v !== 'number' || Number.isInteger(v)) && (typeof v !== 'string' || v.length <= 40)));
  const options = m.options === undefined || (m.options && typeof m.options === 'object' && !Array.isArray(m.options) &&
    Object.entries(m.options).every(([k, v]) => typeof v === PRINT_KEYS[k] && (typeof v !== 'number' || Number.isInteger(v)) && (typeof v !== 'string' || v.length <= 200)));
  return str(m.device) && str(m.path) && str(m.url) && str(m.transfer) && int(m.offset) && int(m.length) && headers &&
    data && bool(m.final) && bool(m.overwrite) && changes && options && bool(m.on) &&
    ['mac', 'page', 'output', 'mode', 'layout', 'variant', 'zone', 'printer'].every((k) => m[k] === undefined || (typeof m[k] === 'string' && m[k].length <= 80)) &&
    (m.job === undefined || (typeof m.job === 'string' && m.job.length <= 120)) &&
    (m.uri === undefined || (typeof m.uri === 'string' && m.uri.length <= 1024)) &&
    (m.name === undefined || (typeof m.name === 'string' && m.name.length <= 127));
}

function nativePort() {
  if (bridge.native) return bridge.native;
  const port = chrome.runtime.connectNative(HOST);
  bridge.native = port;
  port.onMessage.addListener((m) => {
    if (!m || typeof m !== 'object') return;
    if (typeof m.event === 'string') {
      // an event a window caused itself ("by": its request) goes to every other window; it has the reply
      const by = typeof m.by === 'string' ? m.by.split(':')[0] : null;
      bridge.frames.forEach((f, k) => { if (k !== by) f.postMessage({ event: m.event, data: m.data }); });
      return;
    }
    const p = bridge.pending.get(m.id);
    if (!p) return;
    if (m.progress) { p.port.postMessage({ id: p.id, progress: m.progress }); return; }
    bridge.pending.delete(m.id);
    p.port.postMessage(m.error !== undefined ? { id: p.id, error: String(m.error), code: m.code === 'cancelled' ? 'cancelled' : undefined }
      : { id: p.id, result: m.result });
  });
  port.onDisconnect.addListener(() => {
    void chrome.runtime.lastError;
    if (bridge.native !== port) return;
    bridge.native = null;
    bridge.pending.forEach((p) => p.port.postMessage({ id: p.id, error: 'LiteLink’s device bridge isn’t available.' }));
    bridge.pending.clear();
    bridge.frames.forEach((f) => f.postMessage({ event: 'disconnected' }));
  });
  return port;
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'litelink' || !C.liteos || !port.sender || port.sender.origin !== C.liteos) { port.disconnect(); return; }
  const key = 'f' + (++bridge.seq);
  const writes = new Map();   // this window's files and PDFs being sent in pieces: transfer -> { device, path } or {}
  bridge.frames.set(key, port);
  port.onDisconnect.addListener(() => {
    bridge.frames.delete(key);
    // the window went away: its uploads stop too (nobody is left to see them finish)
    bridge.pending.forEach((p, id) => {
      if (p.port !== port) return;
      bridge.pending.delete(id);
      // (a print fetched from a URL too: it isn't printed once its window is gone)
      if ((p.op === 'upload' || p.op === 'download' || p.fetch) && bridge.native) bridge.native.postMessage({ id: id + ':stop', op: 'cancel', transfer: id });
    });
    // and a file it was writing in pieces is deleted now, not left until eject or the end of the session
    if (bridge.native) writes.forEach((w, transfer) => bridge.native.postMessage(Object.assign({ id: key + ':drop:' + transfer, op: 'cancel', transfer }, w)));
    writes.clear();
    // The bridge stays connected when the last window goes: a LiteOS reload or sign-in has a moment
    // with no window, and a new bridge would forget print jobs, ejected drives and running copies.
    // It ends with Chromium, at the end of the session.
  });
  port.onMessage.addListener((m) => {
    if (!valid(m)) { port.postMessage({ id: m && m.id, error: 'That request isn’t valid.' }); return; }
    // End session: close every window; Chromium then quits normally and LiteLink goes back to its desktop
    // (or the kiosk screen), as with the connection bar's End session
    if (m.op === 'session.end') {
      port.postMessage({ id: m.id, result: { done: true } });
      chrome.windows.getAll().then((ws) => ws.forEach((w) => chrome.windows.remove(w.id).catch(() => {})));
      return;
    }
    const fwd = Object.assign({}, m, { id: key + ':' + m.id });
    // transfer keys are the page's own: kept apart per frame
    const pieces = m.op === 'write' || (m.op === 'print.submit' && m.url === undefined);
    if (m.op === 'cancel' || pieces) fwd.transfer = key + ':' + m.transfer;
    if (pieces) {
      if (m.final) writes.delete(fwd.transfer);
      else writes.set(fwd.transfer, m.op === 'write' ? { device: m.device, path: m.path } : {});
    }
    if (m.op === 'cancel') writes.delete(fwd.transfer);
    bridge.pending.set(fwd.id, { id: m.id, port, op: m.op, fetch: m.op === 'print.submit' && m.url !== undefined });
    nativePort().postMessage(fwd);
  });
});

chrome.runtime.onInstalled.addListener(applyRules);
chrome.runtime.onStartup.addListener(applyRules);
applyRules();
watchIdle();
