/*
 * LiteLink Kiosk: rules for a LiteLink web session (LiteOS or Web kiosk).
 * Settings come from config.js, written by LiteLink for each session:
 *   home         the connection's address
 *   allowed      hosts pages may be opened from (empty = any); the home host is included
 *   idleMinutes  after this long without input, clear the session and go home (0 = never)
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

chrome.runtime.onInstalled.addListener(applyRules);
chrome.runtime.onStartup.addListener(applyRules);
applyRules();
watchIdle();
