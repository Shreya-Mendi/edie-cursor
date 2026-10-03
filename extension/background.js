// Relays content-script calls to the local server, so pages on any origin can
// reach http://localhost:8000 without CORS. No keys live in the extension.
const API = 'http://localhost:8000';

// Clicking the toolbar icon opens the side panel.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

function relay(promise, sendResponse) {
  promise
    .then(async (r) => sendResponse(r.ok ? { data: await r.json() } : { error: `${r.status} ${await r.text()}` }))
    .catch((e) => sendResponse({ error: String(e) }));
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg) return;
  if (msg.type === 'vc-api') {
    const init = msg.body
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(msg.body) }
      : undefined;
    relay(fetch(API + msg.path, init), sendResponse);
    return true; // keep the channel open for the async response
  }
  if (msg.type === 'vc-api-raw') { // audio clips arrive as base64
    const bytes = Uint8Array.from(atob(msg.b64), (c) => c.charCodeAt(0));
    relay(fetch(API + msg.path, { method: 'POST', headers: { 'Content-Type': msg.contentType || 'audio/webm' }, body: bytes }), sendResponse);
    return true;
  }
});
