/*
 * Relay between window.LiteLink (litelink-api.js, the page's world) and this extension's background, which holds
 * the connection to LiteLink's device bridge. Runs in the extension's isolated world in frames of the session's
 * LiteOS host; config.js (loaded before it) says the exact origin. Match patterns can't name a port, so a frame on
 * the right host but another port (or a sandboxed document, whose origin is opaque) is answered "not here", which
 * makes LiteLink.ready resolve to null at once.
 */
(() => {
  'use strict';
  const C = self.LITELINK || {};
  const toPage = (m) => window.postMessage(m, location.origin);
  const isRequest = (e) => e.source === window && e.origin === location.origin && e.data && e.data.type === 'litelink:req' && typeof e.data.id === 'string';

  if (!C.liteos || self.origin !== C.liteos) {
    window.addEventListener('message', (e) => {
      if (isRequest(e)) toPage({ type: 'litelink:res', id: e.data.id, error: 'LiteLink isn’t available on this page.' });
    });
    return;
  }

  const FIELDS = ['device', 'path', 'url', 'transfer', 'offset', 'length', 'headers', 'data', 'final', 'overwrite', 'changes',
    'mac', 'on', 'page', 'output', 'mode', 'layout', 'variant', 'zone', 'printer', 'options', 'job', 'uri', 'name'];
  let port = null;

  function open() {
    port = chrome.runtime.connect({ name: 'litelink' });
    port.onMessage.addListener((r) => {
      if (typeof r.event === 'string') toPage({ type: 'litelink:event', event: r.event, data: r.data });
      else toPage(Object.assign({}, r, { type: 'litelink:res' }));
    });
    port.onDisconnect.addListener(() => { port = null; toPage({ type: 'litelink:event', event: 'disconnected' }); });
  }

  window.addEventListener('message', (e) => {
    if (!isRequest(e)) return;
    const m = e.data;
    const req = { id: m.id, op: m.op };
    FIELDS.forEach((k) => { if (m[k] !== undefined) req[k] = m[k]; });
    if (!port) open();
    port.postMessage(req);
  });
})();
