/*
 * window.LiteLink: the LiteLink web API, for LiteOS running on a LiteLink thin client.
 * Runs in the page's own JavaScript world, only in frames of the session's LiteOS address (web.py writes the match
 * into this extension's manifest for LiteOS sessions only). It talks to relay.js (the extension's isolated world),
 * which talks to the device bridge (litelink-devices). Documented in docs/litelink-web-api.md.
 */
(() => {
  'use strict';
  // a sandboxed document (opaque origin) on the LiteOS address gets nothing
  if (window.LiteLink || self.origin !== location.origin) return;

  const REQ = 'litelink:req';
  const RES = 'litelink:res';
  const EVT = 'litelink:event';
  const READ_PIECE = 512 * 1024;
  const READ_MAX = 64 * 1024 * 1024;
  let seq = 0;
  const waiting = new Map(); // id -> { resolve, reject, progress }
  const listeners = new Map(); // event -> [fn]

  function request(op, args, progress) {
    const id = 'q' + (++seq) + Math.random().toString(36).slice(2, 7);
    const promise = new Promise((resolve, reject) => {
      waiting.set(id, { resolve, reject, progress });
      window.postMessage(Object.assign({}, args || {}, { type: REQ, id, op }), location.origin);
    });
    return { id, promise };
  }
  const call = (op, args) => request(op, args).promise;

  function emit(name, data) {
    (listeners.get(name) || []).slice().forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } });
  }

  window.addEventListener('message', (e) => {
    const m = e.data;
    if (e.source !== window || e.origin !== location.origin || !m) return;
    if (m.type === RES) {
      const w = waiting.get(m.id);
      if (!w) return;
      if (m.progress) { if (w.progress) w.progress(m.progress); return; }
      waiting.delete(m.id);
      // code 'cancelled': stopped by cancel() or by an eject
      if (m.error !== undefined) w.reject(Object.assign(new Error(m.error), m.code === 'cancelled' ? { cancelled: true } : {}));
      else w.resolve(m.result);
    } else if (m.type === EVT && typeof m.event === 'string') {
      if (m.event === 'disconnected') waiting.forEach((w) => w.reject(new Error('The connection to the LiteLink device was lost.')));
      if (m.event === 'disconnected') waiting.clear();
      emit(m.event, m.data);
    }
  });

  const bytesOf = (b64) => { const s = atob(b64); const out = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i); return out; };
  const cancelled = () => Object.assign(new Error('Cancelled.'), { cancelled: true });
  const base64Of = (bytes) => { let s = ''; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); return btoa(s); };

  const storage = Object.freeze({
    /** The USB drives plugged in: [{ id, kind: 'usb', name, label, model, size, fstype }] */
    list: () => call('list').then((r) => r.devices),
    /** A folder on a drive: [{ name, dir, size, mtime }] (mtime in seconds); the array's .free is the free space in bytes */
    browse: (device, path) => call('browse', { device, path: path || '/' })
      .then((r) => Object.defineProperty(r.entries, 'free', { value: r.free === undefined ? null : r.free })),
    /**
     * A file's contents as a Blob, read in pieces (up to 64 MB). opts: { type, onProgress({ received, total }), signal }
     */
    async read(device, path, opts) {
      opts = opts || {};
      const parts = [];
      let offset = 0;
      for (;;) {
        if (opts.signal && opts.signal.aborted) throw cancelled();
        const r = await call('read', { device, path, offset, length: READ_PIECE });
        if (r.size > READ_MAX) throw new Error('That file is larger than 64 MB. Use LiteLink.storage.upload for it.');
        const piece = bytesOf(r.data);
        parts.push(piece);
        offset += piece.length;
        if (opts.onProgress) opts.onProgress({ received: offset, total: r.size });
        if (r.eof || !piece.length) break;
      }
      return new Blob(parts, { type: opts.type || '' });
    },
    /**
     * Send a file from a drive straight to the LiteOS server: an HTTP PUT to url (on this LiteOS address) with the
     * raw file as the body; the bytes don't pass through the page. opts: { headers (Authorization, Content-Type,
     * X-…), onProgress({ sent, total }) }. -> { done: Promise<{ status, body }>, cancel() }
     */
    upload(device, path, url, opts) {
      opts = opts || {};
      let abs;
      try { abs = new URL(url, location.href); } catch (e) { return { done: Promise.reject(new Error('That address isn’t valid.')), cancel() {} }; }
      const req = request('upload', { device, path, url: abs.href, headers: opts.headers || {} }, opts.onProgress);
      let stopped = false;
      const done = req.promise.catch((e) => { throw stopped ? cancelled() : e; });
      return { done, cancel: () => { stopped = true; call('cancel', { transfer: req.id }).catch(() => {}); } };
    },

    // ---- writing (capabilities.storage.usb === 'write')
    /**
     * Write a Blob (or File, ArrayBuffer, string) to a file on a drive, in pieces through the browser (up to 512 MB).
     * The file appears only once it's complete. opts: { overwrite (default false), onProgress({ written, total }),
     * signal }. -> Promise<entry>
     */
    async write(device, path, data, opts) {
      opts = opts || {};
      const blob = data instanceof Blob ? data : new Blob([data]);
      const transfer = 'w' + Math.random().toString(36).slice(2, 10);
      let offset = 0;
      try {
        for (;;) {
          if (opts.signal && opts.signal.aborted) throw cancelled();
          const piece = new Uint8Array(await blob.slice(offset, offset + READ_PIECE).arrayBuffer());
          const final = offset + piece.length >= blob.size;
          const r = await call('write', { device, path, transfer, offset, data: base64Of(piece), final, overwrite: !!opts.overwrite });
          offset = r.written;
          if (opts.onProgress) opts.onProgress({ written: offset, total: blob.size });
          if (final) return r.entry;
        }
      } catch (e) {
        if (offset > 0 || e.cancelled) call('cancel', { transfer, device, path }).catch(() => {});
        throw e;
      }
    },
    /**
     * Copy a file from the LiteOS server onto a drive: LiteLink GETs url (on this LiteOS address) and writes it, the
     * bytes don't pass through the page (up to 512 MB). No cookies are sent: the server recognises the request from
     * the URL or a header (opts.headers, same rules as upload). opts: { headers, overwrite, onProgress({ received,
     * total }) }. -> { done: Promise<entry>, cancel() }
     */
    download(device, path, url, opts) {
      opts = opts || {};
      let abs;
      try { abs = new URL(url, location.href); } catch (e) { return { done: Promise.reject(new Error('That address isn’t valid.')), cancel() {} }; }
      const req = request('download', { device, path, url: abs.href, headers: opts.headers || {}, overwrite: !!opts.overwrite }, opts.onProgress);
      let stopped = false;
      const done = req.promise.then((r) => r.entry, (e) => { throw stopped ? cancelled() : e; });
      return { done, cancel: () => { stopped = true; call('cancel', { transfer: req.id }).catch(() => {}); } };
    },
    /** Make a folder on a drive. -> Promise<entry> */
    mkdir: (device, path) => call('mkdir', { device, path }).then((r) => r.entry),
    /** Unmount a drive so it's safe to pull out; it can't be used again until it's plugged in again. */
    eject: (device) => call('eject', { device }).then(() => undefined),
  });

  // ---- sound (capabilities.audio)
  const audio = Object.freeze({
    /** { volume, muted, output: { id, name }, outputs: [{ id, name, default }], input: { volume, muted, device, devices } } */
    state: () => call('audio.state'),
    /** Change any of { volume (0–100), muted, output (an outputs[].id), inputVolume, inputMuted, input }; -> the new state */
    set: (changes) => call('audio.set', { changes: Object.assign({}, changes) }),
  });

  // ---- network, read only (capabilities.network): joining, switching and turning things off stay in LiteLink
  const network = Object.freeze({
    /** { connected, kind: 'ethernet' | 'wifi' | 'none', name, ip, gateway, dns, mac, wifi: { present, enabled, network }, vpn: [{ name, active }] } */
    state: () => call('network.state'),
    /** Wi-Fi networks in range: [{ ssid, signal (0–100), security, active }] (scans; can take a few seconds) */
    wifi: () => call('network.wifi').then((r) => r.networks),
  });

  // ---- the device (capabilities.system)
  const system = Object.freeze({
    /** { os, version, build, device, processor, memory (bytes), hostname, ip, mac } */
    info: () => call('system.info'),
  });

  // ---- the LiteLink session (capabilities.session)
  const done = () => undefined;
  const session = Object.freeze({
    /** End this session, as the connection bar's End session does: back to LiteLink's desktop or kiosk screen. */
    end: () => call('session.end').then(done),
    /** Open LiteLink's Settings over the session (page: 'network', 'bluetooth', 'display', 'keyboard', 'sound', 'time', …). */
    openSettings: (page) => call('session.settings', { page: page || 'network' }).then(done),
  });

  // ---- power (capabilities.power): lock, restart, shut down (no admin password)
  const power = Object.freeze({
    /** LiteLink's lock screen over the session. */
    lock: () => call('system.lock').then(done),
    restart: () => call('power.restart').then(done),
    shutdown: () => call('power.shutdown').then(done),
  });

  // ---- Bluetooth (capabilities.bluetooth): no admin password
  const bluetooth = Object.freeze({
    /** { present, powered, devices: [{ mac, name, connected, kind }] } (paired devices) */
    state: () => call('bluetooth.state'),
    /** Nearby devices not paired yet: [{ mac, name }] (takes about 8 s) */
    scan: () => call('bluetooth.scan').then((r) => r.devices),
    /** Connect (or disconnect, on = false) a paired device, e.g. a headset */
    connect: (mac, on) => call('bluetooth.connect', { mac, on: on !== false }).then(done),
    pair: (mac) => call('bluetooth.pair', { mac }).then(done),
    remove: (mac) => call('bluetooth.remove', { mac }).then(done),
    setPower: (on) => call('bluetooth.power', { on: !!on }).then(done),
  });

  // ---- screens (capabilities.display). A new resolution is confirmed in LiteLink's own "Keep these display settings?".
  const display = Object.freeze({
    /** [{ output, current, preferred, modes: ['1920x1080', …], rate }] */
    state: () => call('display.state').then((r) => r.displays),
    /** -> { kept }: false when the person chose Revert or didn't answer in 15 s */
    set: (output, mode) => call('display.set', { output, mode }),
  });

  // ---- keyboard (capabilities.input): the layout for this session; LiteLink's saved layout returns after it
  const input = Object.freeze({
    /** { layout, variant, layouts: [{ id, name }] } */
    state: () => call('input.state'),
    setLayout: (layout, variant) => call('input.layout', { layout, variant: variant || '' }).then(done),
  });

  // ---- time (capabilities.time). The time zone changes the device (admin password).
  const time = Object.freeze({
    /** { timezone, ntp, synced } */
    state: () => call('time.state'),
    setZone: (zone) => call('time.zone', { zone }).then(done),
  });

  // ---- printing (capabilities.print): the printers set up in LiteLink's Settings › Printers; PDF only
  const PRINT_OPTIONS = ['title', 'copies', 'pages', 'duplex', 'color', 'media', 'orientation'];
  const printOptions = (o) => { const r = {}; PRINT_OPTIONS.forEach((k) => { if (o && o[k] !== undefined) r[k] = o[k]; }); return r; };
  const print = Object.freeze({
    /** [{ id, name, default, kind: 'usb' | 'network', state: 'idle' | 'printing' | 'stopped', message, duplex, color, media: [...], mediaDefault }] */
    printers: () => call('print.printers').then((r) => r.printers),
    /**
     * Print a PDF on printer (a printers()[].id). source: a Blob / ArrayBuffer / typed array (sent in pieces, up to
     * 100 MB), or { url, headers } on this LiteOS address (LiteLink fetches it; no cookies, as download()).
     * opts: { title, copies (1–99), pages ('1-3,5'), duplex: 'one' | 'long' | 'short', color, media (a printers()[].media
     * entry), orientation: 'portrait' | 'landscape', onProgress({ sent | received, total }) }.
     * -> { done: Promise<{ job }> (once the printer's queue has it), cancel() }
     */
    print(printer, source, opts) {
      opts = opts || {};
      const options = printOptions(opts);
      let stopped = false;
      if (source && typeof source === 'object' && typeof source.url === 'string' && !(source instanceof Blob)) {
        let abs;
        try { abs = new URL(source.url, location.href); } catch (e) { return { done: Promise.reject(new Error('That address isn’t valid.')), cancel() {} }; }
        const req = request('print.submit', { printer, options, url: abs.href, headers: source.headers || {} }, opts.onProgress);
        const done = req.promise.then((r) => ({ job: r.job }), (e) => { throw stopped ? cancelled() : e; });
        return { done, cancel: () => { stopped = true; call('cancel', { transfer: req.id }).catch(() => {}); } };
      }
      const blob = source instanceof Blob ? source : new Blob([source]);
      const transfer = 'p' + Math.random().toString(36).slice(2, 10);
      const done = (async () => {
        let offset = 0;
        try {
          for (;;) {
            if (stopped) throw cancelled();
            const piece = new Uint8Array(await blob.slice(offset, offset + READ_PIECE).arrayBuffer());
            const final = offset + piece.length >= blob.size;
            const r = await call('print.submit', { printer, options, transfer, offset, data: base64Of(piece), final });
            offset = r.sent;
            if (opts.onProgress) opts.onProgress({ sent: offset, total: blob.size });
            if (final) return { job: r.job };
          }
        } catch (e) {
          if (offset > 0 || e.cancelled) call('cancel', { transfer }).catch(() => {});
          throw stopped ? cancelled() : e;
        }
      })();
      return { done, cancel: () => { stopped = true; } };
    },
    /** This window's session's documents still printing or waiting: [{ job, printer, title, state: 'pending' | 'printing' | 'stopped', message }] */
    jobs: () => call('print.jobs').then((r) => r.jobs),
    /** Cancel one of this session's documents (a jobs()[].job, or what print() resolved with). */
    cancelJob: (job) => call('print.cancel', { job }).then(done),
    /** Printers found now, over USB and on the network: [{ uri, name, kind: 'usb' | 'network', driverless, added }].
     *  Only driverless ones can be added; added: already set up. Takes a few seconds. */
    find: () => call('print.find').then((r) => r.printers),
    /** Set a printer up on this device: a find()[].uri, or an address
     *  ('ipp://office.local', '192.168.1.50'). -> { printer (its id), name }. */
    add: (uri, name) => call('print.add', name === undefined ? { uri } : { uri, name }),
    /** Remove a printer from this device. */
    remove: (printer) => call('print.remove', { printer }).then(done),
  });

  let capabilities = {};
  // null when nothing answers in time: not on the session's exact LiteOS origin (another port), or the bridge is gone
  const READY_TIMEOUT = 8000;
  const ready = Promise.race([
    call('hello').then((r) => { capabilities = Object.freeze((r && r.capabilities) || {}); return capabilities; }, () => null),
    new Promise((resolve) => setTimeout(() => resolve(null), READY_TIMEOUT)),
  ]);

  const LiteLink = Object.freeze({
    version: 1,
    /** Resolves to the capabilities once LiteLink's device bridge answers, or null if it isn't available. */
    ready,
    /** What this device and connection allow, e.g. { storage: { usb: 'read' } }; {} until ready. */
    get capabilities() { return capabilities; },
    storage,
    audio,
    network,
    system,
    session,
    power,
    bluetooth,
    display,
    input,
    time,
    print,
    /**
     * Events: 'device-added' (a drive), 'device-removed' ({ id }), 'device-ejected' ({ id }), 'audio' (the new
     * audio state), 'network' (the new network state), 'printers' (the new printer list), 'print-job' ({ job, state:
     * 'pending' | 'printing' | 'stopped' | 'done' | 'cancelled', message }), 'disconnected'. 'audio' / 'network'
     * start once the page has asked for that state at least once; 'printers' / 'print-job' once it used printing.
     */
    on(name, fn) { if (typeof fn === 'function') listeners.set(name, (listeners.get(name) || []).concat(fn)); },
    off(name, fn) { listeners.set(name, (listeners.get(name) || []).filter((f) => f !== fn)); },
  });
  Object.defineProperty(window, 'LiteLink', { value: LiteLink, enumerable: false, configurable: false, writable: false });
})();
