// Edie Cursor content script: two cursors on a web form.
// The agent's cursor (light blue) follows the conversation and leaves suggestions.
// Your cursor (amber "You" box) can click any question to ask for its value.
// All logic lives here, because this script can see the page. The panel (Chrome's
// side panel, or a docked frame on the dev page) only shows state and sends commands.
// Nothing is filled until you accept, and nothing is ever submitted.
(() => {
  if (document.getElementById('vc-host')) return;

  const IN_EXT = typeof chrome !== 'undefined' && !!(chrome.runtime && chrome.runtime.id);
  const WORDS_PER_SEC = 2.6; // conversational pace at 1x
  const USER_IDLE_MS = 2500; // only auto-scroll after the user has been idle this long
  const PANEL_W = innerWidth < 1100 ? 300 : 360; // dev dock only; Chrome sizes the real side panel
  const FONT_URL = IN_EXT ? chrome.runtime.getURL('fonts/MonaSans-latin.woff2')
    : '/ext/fonts/MonaSans-latin.woff2';

  // ---------- local server calls ----------
  async function api(path, body) {
    if (IN_EXT) {
      const res = await chrome.runtime.sendMessage({ type: 'vc-api', path, body });
      if (!res || res.error) throw new Error(res ? res.error : 'no response from background');
      return res.data;
    }
    const init = body
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : undefined;
    const r = await fetch(path, init); // dev page is served by the same local server
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
  }

  const blobToBase64 = (blob) => new Promise((ok) => {
    const fr = new FileReader();
    fr.onload = () => ok(String(fr.result).split(',')[1]);
    fr.readAsDataURL(blob);
  });

  async function apiAudio(path, blob) {
    if (IN_EXT) { // extension messages carry JSON only, so audio travels as base64
      const res = await chrome.runtime.sendMessage({ type: 'vc-api-raw', path, b64: await blobToBase64(blob), contentType: blob.type });
      if (!res || res.error) throw new Error(res ? res.error : 'no response from background');
      return res.data;
    }
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': blob.type || 'audio/webm' }, body: blob });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
  }

  // ---------- read the form (standard markup only) ----------
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const textOf = (el) => clean(el && el.textContent);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

  function optionLabel(input) {
    if (input.getAttribute('aria-label')) return clean(input.getAttribute('aria-label'));
    if (input.id) {
      const l = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
      if (l) return textOf(l);
    }
    return textOf(input.closest('label'));
  }

  function questionFor(container) {
    const fs = container.closest('fieldset');
    const legend = fs && fs.querySelector(':scope > legend');
    const parts = legend ? [textOf(legend)] : [];
    const ref = container.getAttribute('aria-labelledby') || container.getAttribute('aria-describedby');
    if (ref) parts.push(ref.split(/\s+/).map((id) => textOf(document.getElementById(id))).join(' '));
    else if (container.getAttribute('aria-label')) parts.push(clean(container.getAttribute('aria-label')));
    return parts.filter(Boolean).join(' | ');
  }

  function readForm() {
    const inputs = [...document.querySelectorAll('input, select, textarea')]
      .filter((i) => !['hidden', 'submit', 'button', 'reset', 'image'].includes(i.type));
    const groups = new Map();
    for (const el of inputs) {
      const key = el.name || el.id;
      if (!key) continue;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(el);
    }
    return [...groups].map(([id, els]) => {
      const first = els[0];
      const type = first.tagName === 'SELECT' ? 'select' : first.tagName === 'TEXTAREA' ? 'textarea' : first.type;
      const container = first.closest('[role="group"], fieldset') || first.parentElement;
      const f = { id, type, question: questionFor(container) || optionLabel(first), els, container };
      if (type === 'number') f.step = first.getAttribute('step') || '1';
      if (type === 'radio' || type === 'checkbox') f.options = els.map((e) => ({ value: e.value, label: optionLabel(e) }));
      if (type === 'select') f.options = [...first.options].filter((o) => o.value).map((o) => ({ value: o.value, label: clean(o.text) }));
      return f;
    });
  }

  const FIELDS = readForm();
  const byId = new Map(FIELDS.map((f) => [f.id, f]));
  const SCHEMA = FIELDS.map(({ id, type, question, options, step }) => ({ id, type, question, ...(options ? { options } : {}), ...(step ? { step } : {}) }));
  const legendOf = (f) => f.question.split(' | ')[0];
  // The form's own skip rules: a disabled field, or (on pages that publish their rules) a field
  // that the pending answer to another question would skip, like allergens after "No known allergies".
  const RULES = window.SKIP_RULES || [];
  const skipped = (f) => f.els.every((e) => e.disabled) || RULES.some((r) => r.skip.includes(f.id)
    && state.sugg.get(r.if) && state.sugg.get(r.if).status !== 'dismissed' && state.sugg.get(r.if).value === r.equals);

  // ---------- overlay (shadow DOM, so page styles cannot touch it) ----------
  // Shadow roots cannot declare @font-face, so the font is registered on the page
  // under a private family name that cannot clash with the page's own fonts.
  const fontStyle = document.createElement('style');
  fontStyle.textContent = `@font-face{font-family:"VC Mona";src:url("${FONT_URL}") format("woff2");font-weight:400 700;font-display:swap}`;
  document.head.appendChild(fontStyle);

  const CSS = `
    :host { all: initial; }
    * { font-family: "VC Mona", -apple-system, "Segoe UI", sans-serif; font-feature-settings: "tnum", "zero", "cv11"; box-sizing: border-box; -webkit-font-smoothing: antialiased; }
    .ring, .point { position: fixed; border-radius: 12px; pointer-events: none; transition: opacity .25s; }
    .ring { border: 2px solid #38BDF8; box-shadow: 0 0 0 6px rgba(56,189,248,.18); background: rgba(56,189,248,.05); border-radius: 20px; }
    .point { border: 2px dashed #F59E0B; border-radius: 20px; }
    .ptag { position: absolute; left: 14px; bottom: -12px; background: #F59E0B; color: #451A03; padding: 3px 8px;
      border-radius: 999px; font-size: 11.5px; font-weight: 600; }
    .rtag { position: absolute; left: 14px; top: -12px; background: #0369A1; color: #fff; padding: 3px 8px;
      border-radius: 999px; font-size: 11.5px; font-weight: 600; }
    .cursor { position: fixed; left: 0; top: 0; will-change: transform; pointer-events: none; transition: opacity .25s; }
    .cursor svg { width: 28px; height: 28px; filter: drop-shadow(0 3px 6px rgba(3,105,161,.35)); }
    .chip { position: fixed; left: 0; top: 0; pointer-events: auto; display: flex; gap: 6px; align-items: center; cursor: pointer;
      background: #F0F9FF; border: 1px solid #7DD3FC; color: #075985; border-radius: 999px; padding: 4px 5px 4px 12px;
      font-size: 13px; max-width: 470px; box-shadow: 0 6px 18px rgba(3,105,161,.14); }
    .chip .k { color: #0369A1; }
    .chip b { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .chip button { border: 0; cursor: pointer; font: inherit; font-weight: 600; border-radius: 999px; }
    .chip .acc { background: #0369A1; color: #fff; padding: 4px 12px; }
    .chip .dis { background: transparent; color: #0369A1; padding: 3px 7px; font-size: 14px; line-height: 1; }
    .chip.done { background: #ECFDF3; border-color: #86EFAC; color: #166534; padding-right: 12px; }
    .pop { position: fixed; left: 0; top: 0; pointer-events: none; max-width: 420px; background: #fff; color: #1c2530;
      border: 1px solid #BAE6FD; border-radius: 14px; padding: 10px 12px; font-size: 13px; line-height: 1.45;
      box-shadow: 0 12px 30px rgba(3,105,161,.16); opacity: 0; transition: opacity .15s; }
    .pop .h { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #0369A1; margin-bottom: 3px; }
    .pop q { display: block; font-style: italic; margin: 2px 0; cursor: pointer; }
    .pop.on { opacity: 1; pointer-events: auto; }
    .pop .t { font-size: 11px; color: #6b7385; margin-top: 4px; }
  `;
  const ARROW = '<svg viewBox="0 0 24 24"><path d="M4 2 L4 20 L9 15.5 L12.5 22.5 L15.5 21 L12 14 L19 14 Z" fill="#38BDF8" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';

  const host = document.createElement('div');
  host.id = 'vc-host';
  host.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647;';
  document.documentElement.appendChild(host);
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `<style>${CSS}</style>
    <div class="ring" style="opacity:0"><span class="rtag">Edie</span></div>
    <div class="point" style="opacity:0"><span class="ptag">You</span></div>
    <div class="cursor" style="opacity:0">${ARROW}</div>
    <div class="chips"></div>
    <div class="pop"></div>`;
  const $ = (s) => root.querySelector(s);
  const ring = $('.ring'), pointEl = $('.point'), cursorEl = $('.cursor'), chipsEl = $('.chips'), popEl = $('.pop');

  const state = {
    models: {}, mode: 'replay', visits: [], enc: null, idx: 0, playing: false, speed: 2, transcript: [],
    agent: null, pointed: null, sugg: new Map(), chips: new Map(), dismissed: new Map(), status: '',
    lastUser: 0, cur: { x: innerWidth - 260, y: innerHeight - 160 }, fillReady: false, topInset: 0,
  };

  // ---------- panel bridge ----------
  let dock = null;
  function toPanel(msg) {
    if (IN_EXT) { try { chrome.runtime.sendMessage(msg).catch(() => {}); } catch (e) { /* panel closed */ } }
    else if (dock && dock.contentWindow) dock.contentWindow.postMessage(msg, location.origin);
  }
  function snapshot() {
    return {
      models: state.models, mode: state.mode, visits: state.visits, visitId: state.enc ? state.enc.id : null,
      playing: state.playing, speed: state.speed, turnIdx: state.idx, turnTotal: state.enc ? state.enc.turns.length : 0,
      live: { on: live.on, speaker: live.speaker }, transcript: state.transcript.slice(-40), tFrom: Math.max(0, state.transcript.length - 40),
      agent: state.agent ? { id: state.agent.id, label: legendOf(state.agent) } : null,
      suggestions: [...state.sugg.values()].map((s) => ({ id: s.id, label: legendOf(byId.get(s.id)), display: s.display, quotes: s.quotes, status: s.status })),
      status: state.status, busy: fillBusy,
    };
  }
  let pushTimer = 0;
  function push() {
    if (pushTimer) return;
    pushTimer = setTimeout(() => { pushTimer = 0; toPanel({ type: 'vc-state', state: snapshot() }); }, 60);
  }
  const status = (s) => { state.status = s; push(); };

  // ---------- where the page's own sticky header ends ----------
  function measureTopInset() {
    let inset = 0;
    for (const y of [2, 40, 80]) {
      for (let el of document.elementsFromPoint(innerWidth * 0.5, y)) {
        for (; el && el !== document.body; el = el.parentElement) {
          if (el === host || el === dock) break;
          const pos = getComputedStyle(el).position;
          if (pos === 'fixed' || pos === 'sticky') {
            const r = el.getBoundingClientRect();
            if (r.top <= 1 + inset && r.height < innerHeight / 3) inset = Math.max(inset, r.bottom);
          }
        }
      }
    }
    state.topInset = inset;
  }
  measureTopInset();
  addEventListener('resize', measureTopInset);
  setInterval(measureTopInset, 1500);

  // ---------- agent cursor ----------
  function moveAgentTo(id) {
    const f = byId.get(id);
    if (!f) return;
    state.agent = f;
    cursorEl.style.opacity = 1;
    ring.style.opacity = 1;
    const r = f.container.getBoundingClientRect();
    const visible = r.top >= state.topInset && r.bottom <= innerHeight - 10;
    if (!visible && Date.now() - state.lastUser > USER_IDLE_MS) f.container.scrollIntoView({ behavior: 'smooth', block: 'center' });
    push();
  }

  function place(el, r, pad) {
    el.style.left = `${r.left - pad}px`; el.style.top = `${r.top - pad}px`;
    el.style.width = `${r.width + 2 * pad}px`; el.style.height = `${r.height + 2 * pad}px`;
  }

  function frame() {
    if (state.agent) {
      const r = state.agent.container.getBoundingClientRect();
      // Never draw over the page's own sticky header: clip the ring at its bottom edge.
      const top = Math.max(r.top, state.topInset + 8);
      place(ring, { left: r.left, top, width: r.width, height: Math.max(0, r.bottom - top) }, 6);
      ring.style.visibility = r.bottom < state.topInset + 24 ? 'hidden' : 'visible';
      const tx = Math.max(4, r.left - 22);
      const ty = Math.min(Math.max(state.topInset + 6, r.top + 8), innerHeight - 44);
      state.cur.x += (tx - state.cur.x) * 0.14;
      state.cur.y += (ty - state.cur.y) * 0.14;
      cursorEl.style.transform = `translate(${state.cur.x}px, ${state.cur.y}px)`;
    }
    if (state.pointed) place(pointEl, state.pointed.container.getBoundingClientRect(), 10);
    // Short rows (a table row, one blank) get the chip on the row; tall items get it on the top edge.
    // Chips never overlap: one that would touch a chip above it moves down below that chip.
    const placed = [];
    const rows = [...state.chips].map(([id, chip]) => ({ chip, r: byId.get(id).container.getBoundingClientRect() }))
      .sort((a, b) => a.r.top - b.r.top);
    for (const { chip, r } of rows) {
      const w = chip.offsetWidth, h = chip.offsetHeight || 30;
      const x = Math.max(8, Math.min(r.right - w - 12, innerWidth - w - 12));
      let y = Math.max(r.height < 64 ? r.top + (r.height - h) / 2 : r.top - 14, state.topInset + 6);
      for (const p of placed) if (x < p.x + p.w && p.x < x + w && y < p.y + p.h + 4) y = Math.max(y, p.y + p.h + 4);
      const hidden = r.bottom < state.topInset + 30 || r.top > innerHeight;
      if (!hidden) placed.push({ x, y, w, h });
      chip.style.transform = `translate(${x}px, ${y}px)`;
      chip.style.visibility = hidden ? 'hidden' : 'visible';
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // ---------- routing: where is the conversation? ----------
  let routeSeq = 0;
  async function routeOn(utterance) {
    const seq = ++routeSeq;
    const t0 = performance.now();
    try {
      const r = await api('/api/route', { fields: SCHEMA, utterance, current: state.agent ? state.agent.id : null });
      if (seq !== routeSeq) return;
      const ms = Math.round(performance.now() - t0);
      state.lastHits = (r.fields || []).map((h) => h.id);
      let target = r.field_id;
      // A stated measurement belongs in a number field, and the sentence holding the number decides
      // which one: "Let me get your vitals. Your blood pressure is one thirty-eight over eighty-six"
      // should land on blood pressure (not on "High blood pressure" in the history, or on height).
      const measured = utterance.split(/(?<=[.!?])\s+/).filter((x) => !x.trim().endsWith('?') && NUMWORDS.test(x)).join(' ');
      if (measured) {
        const rm = measured === utterance ? r
          : await api('/api/route', { fields: SCHEMA, utterance: measured, current: state.agent ? state.agent.id : null });
        if (seq !== routeSeq) return;
        const nums = (rm.fields || []).filter((h) => { const f = byId.get(h.id); return f && f.type === 'number'; });
        if (nums.length) target = nums[0].id;
        for (const h of nums) requestFill(byId.get(h.id), 'follow');
      }
      if (target) { moveAgentTo(target); status(`Cursor moved to ${target} in ${ms} ms`); }
      else status(`Cursor stayed (best match ${r.best_id} too weak)`);
    } catch (e) { status(`Couldn't reach the local server: ${e.message}`); }
  }

  const NUMWORDS = /\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\b/i;

  // ---------- filling: one request in flight, one waiting slot PER FIELD ----------
  // The local model handles one fill at a time. Every field the conversation touched keeps
  // its own slot, so a slow fill never drops another field's answer (it used to: a single
  // shared slot meant a fast conversation could skip the birth date entirely).
  // A waiting slot always uses the newest transcript when it runs. Clicks jump the line.
  let fillBusy = false;
  const fillQueue = new Map(); // field id -> 'point' | 'follow', in arrival order
  function requestFill(f, why) {
    if (!state.fillReady) { if (why === 'point') status(`${f.id}: the filling model isn't installed`); return; }
    if (skipped(f)) { if (why === 'point') status(`${f.id} is skipped on this form`); return; }
    if (why === 'point') { fillQueue.delete(f.id); fillQueue.set(f.id, 'point'); }
    else if (why === 'follow' && fillQueue.get(f.id) === 'sweep') fillQueue.set(f.id, 'follow');
    else if (!fillQueue.has(f.id)) fillQueue.set(f.id, why);
    pumpFills();
  }

  function nextFill() {
    for (const want of ['point', 'follow', 'sweep', 'finish']) {
      for (const [id, why] of fillQueue) if (why === want) return id; // your clicks first, the sweep last
    }
    return undefined;
  }

  async function pumpFills() {
    if (fillBusy || !fillQueue.size) return;
    const id = nextFill();
    const why = fillQueue.get(id);
    fillQueue.delete(id);
    const f = byId.get(id);
    fillBusy = true;
    if (why === 'sweep') moveAgentTo(id); // the cursor visits each field it sweeps
    push();
    const field = SCHEMA.find((s) => s.id === id);
    const turns = state.transcript.map(({ who, role, text }) => ({ who, role, text }));
    const gen = state.gen;
    if (why === 'point') status(`Asking the agent about ${id}…`);
    try {
      const r = await api('/api/fill', { field, turns, visit: state.enc ? state.enc.visit : null, form: document.title });
      if (gen !== state.gen) { /* the visit was reset while this was running */ }
      else if (r.status === 'ok' && !skipped(f)) { setPending(f, r); if (why === 'point') status(`${id}: suggestion ready in ${(r.ms / 1000).toFixed(1)} s`); }
      else if (why === 'point') status(`${id}: no suggestion yet (${r.reason})`);
    } catch (e) { status(`Couldn't fill ${id}: ${e.message}`); }
    fillBusy = false;
    push();
    pumpFills();
  }

  function chipFor(id) {
    let chip = state.chips.get(id);
    if (!chip) {
      chip = document.createElement('div');
      chipsEl.appendChild(chip);
      state.chips.set(id, chip);
      chip.addEventListener('mouseenter', () => showPop(id, chip));
      chip.addEventListener('mouseleave', hidePopSoon);
      // Click the chip (anywhere but its buttons) to see where the evidence is in the conversation.
      chip.addEventListener('click', (e) => { if (!e.target.closest('button')) toPanel({ type: 'vc-show', id }); });
    }
    return chip;
  }

  // The popup stays open while the mouse crosses from the chip to it, so its quotes can be clicked.
  let popTimer = 0, popId = null;
  function hidePop() { clearTimeout(popTimer); popEl.classList.remove('on'); }
  function hidePopSoon() { clearTimeout(popTimer); popTimer = setTimeout(hidePop, 250); }
  popEl.addEventListener('mouseenter', () => clearTimeout(popTimer));
  popEl.addEventListener('mouseleave', hidePopSoon);
  popEl.addEventListener('click', () => { if (popId) toPanel({ type: 'vc-show', id: popId }); });

  function setPending(f, r) {
    const key = JSON.stringify(r.value);
    if (state.dismissed.get(f.id) === key) return; // you already said no to this exact value
    const cur = state.sugg.get(f.id);
    if (cur && cur.status === 'accepted' && JSON.stringify(cur.value) === key) return;
    state.sugg.set(f.id, { id: f.id, value: r.value, display: r.display, quotes: r.quotes || [], status: 'pending' });
    for (const rule of RULES) if (rule.if === f.id && r.value === rule.equals) { // e.g. "No known allergies"
      for (const sid of rule.skip) {
        if (state.sugg.has(sid) && state.sugg.get(sid).status === 'pending') {
          state.sugg.delete(sid);
          const c = state.chips.get(sid);
          if (c) { c.remove(); state.chips.delete(sid); }
        }
      }
    }
    const chip = chipFor(f.id);
    chip.className = 'chip';
    chip.innerHTML = `<span class="k">Suggested</span><b>${esc(r.display)}</b><button class="acc">Accept</button><button class="dis" aria-label="Dismiss">✕</button>`;
    chip.querySelector('.acc').onclick = () => accept(f.id);
    chip.querySelector('.dis').onclick = () => dismiss(f.id);
    push();
  }

  function showPop(id, chip) {
    const s = state.sugg.get(id);
    if (!s || !s.quotes.length) return;
    clearTimeout(popTimer);
    popId = id;
    popEl.innerHTML = `<div class="h">Heard in the conversation</div>${s.quotes.map((q) => `<q>${esc(q)}</q>`).join('')}<div class="t">Click to show it in the conversation</div>`;
    const r = chip.getBoundingClientRect();
    popEl.style.transform = `translate(${Math.max(8, Math.min(r.left, innerWidth - 430))}px, ${r.bottom + 6}px)`;
    popEl.classList.add('on');
  }

  function applyValue(f, value) {
    if (f.type === 'radio') {
      const el = f.els.find((e) => e.value === String(value));
      if (el && !el.checked) el.click();
    } else if (f.type === 'checkbox') {
      const want = new Set((Array.isArray(value) ? value : [value]).map(String));
      for (const el of f.els) if (el.checked !== want.has(el.value)) el.click();
    } else {
      const el = f.els[0];
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, String(value)); // works with framework-controlled inputs
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function accept(id) {
    const s = state.sugg.get(id);
    if (!s || s.status !== 'pending') return;
    applyValue(byId.get(id), s.value);
    s.status = 'accepted';
    const chip = chipFor(id);
    chip.className = 'chip done';
    chip.innerHTML = `<span>✓ Filled by agent</span><b>${esc(s.display)}</b>`;
    status(`${id} filled. Check it; the page is never submitted.`);
  }

  function dismiss(id) {
    const s = state.sugg.get(id);
    if (!s || s.status !== 'pending') return;
    state.dismissed.set(id, JSON.stringify(s.value));
    state.sugg.delete(id);
    const chip = state.chips.get(id);
    if (chip) { chip.remove(); state.chips.delete(id); }
    hidePop();
    status(`${id} suggestion dismissed`);
  }

  // ---------- your cursor: point to ask, keys to accept or dismiss ----------
  const fieldAt = (node) => FIELDS.find((f) => f.container.contains(node));
  function pointAt(f, ask) {
    state.pointed = f;
    pointEl.style.opacity = 1;
    if (ask) requestFill(f, 'point');
  }
  for (const ev of ['wheel', 'mousedown', 'keydown', 'touchstart']) {
    addEventListener(ev, () => { state.lastUser = Date.now(); }, { capture: true, passive: true });
  }
  document.addEventListener('click', (e) => {
    if (e.target === host || e.target.closest('input, label, select, textarea')) return; // answering, not pointing
    const f = fieldAt(e.target);
    if (f) pointAt(f, true);
  }, true);
  document.addEventListener('focusin', (e) => {
    const f = FIELDS.find((x) => x.els.includes(e.target));
    if (f && ['text', 'textarea', 'number', 'date'].includes(f.type) && !e.target.value) pointAt(f, true);
  });
  document.addEventListener('keydown', (e) => {
    const isAccept = e.altKey && e.key === 'Enter';
    if (!isAccept && e.key !== 'Escape') return;
    const pending = (f) => f && state.sugg.has(f.id) && state.sugg.get(f.id).status === 'pending';
    const mine = FIELDS.find((f) => f.container.contains(document.activeElement) && pending(f));
    const target = mine || (pending(state.agent) ? state.agent : null);
    if (!target) return;
    e.preventDefault();
    e.stopPropagation();
    if (isAccept) accept(target.id); else dismiss(target.id);
  }, true);


  // An answer fills the cursor's field and any other field of the same group that the last
  // question touched ("Any allergies?" -> allergy status and the reactions box).
  function fillAfterAnswer() {
    if (!state.agent) return;
    requestFill(state.agent, 'follow');
    const group = state.agent.container.closest('fieldset');
    for (const id of state.lastHits || []) {
      const f = byId.get(id);
      if (f && f !== state.agent && group && f.container.closest('fieldset') === group) requestFill(f, 'follow');
    }
  }

  // End of visit: suggest every field that is still empty (your click starts it).
  function sweep(kind = 'sweep') {
    let n = 0;
    for (const f of FIELDS) {
      const s = state.sugg.get(f.id);
      const filled = f.els.some((e) => ((e.type === 'radio' || e.type === 'checkbox') ? e.checked : e.value));
      if (!s && !filled && !skipped(f)) { requestFill(f, kind); n += 1; }
    }
    status(n ? `Suggesting ${n} remaining fields…` : 'Every field already has a value or a suggestion.');
  }

  // Finish all: the whole conversation at once, then a suggestion for every empty field.
  // No cursor tour, so everything lands on screen together for explaining.
  function finishAll() {
    if (!state.enc || state.enc.id === 'live') return;
    state.playing = false;
    while (state.idx < state.enc.turns.length) state.transcript.push(state.enc.turns[state.idx++]);
    push();
    sweep('finish');
  }

  // ---------- replay ----------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const turnMs = (t) => Math.max(1200, (t.text.split(/\s+/).length / WORDS_PER_SEC) * 1000) + 400;

  function clearSuggestions() {
    for (const chip of state.chips.values()) chip.remove();
    state.chips.clear(); state.sugg.clear(); state.dismissed.clear();
    fillQueue.clear(); state.lastHits = []; state.gen = (state.gen || 0) + 1;
    state.agent = null; state.pointed = null;
    ring.style.opacity = 0; pointEl.style.opacity = 0; cursorEl.style.opacity = 0; hidePop();
  }

  async function selectEncounter(id) {
    state.playing = false;
    clearSuggestions();
    state.enc = await api(`/api/encounters/${id}`);
    state.idx = 0;
    state.transcript = [];
    window.postMessage({ type: 'vc-demo-visit', id }, '*'); // demo pages update their patient banner
    status(`${state.enc.turns.length} turns · visit on ${state.enc.visit.weekday}`);
  }

  async function play() {
    if (!state.enc || state.enc.id === 'live') return;
    state.playing = true;
    push();
    while (state.playing && state.idx < state.enc.turns.length) {
      const t = state.enc.turns[state.idx++];
      state.transcript.push(t);
      push();
      if (t.role === 'clinician') routeOn(t.text);
      else fillAfterAnswer();
      await sleep(turnMs(t) / state.speed);
    }
    if (state.idx >= state.enc.turns.length && state.playing) {
      state.playing = false;
      status('Visit finished. Review the suggestions, then accept the ones that are right.');
    }
  }

  // ---------- live voice ----------
  // Recorder pattern adapted from QuietSky voice-glider/frontend/src/hooks/useRecorder.js:
  // watch mic level, cut the recording at silence, send the clip for transcription.
  const SPEECH_RMS = 0.02, SILENCE_RMS = 0.012, SILENCE_MS = 800, MAX_CLIP_MS = 20000;
  const live = { on: false, stream: null, ctx: null, analyser: null, rec: null, chunks: null, spoke: false, quietSince: 0, startedAt: 0, raf: 0, speaker: 'clinician', lastLevel: 0 };

  function todayVisit() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return { date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, weekday: d.toLocaleDateString('en-US', { weekday: 'long' }), discipline: 'live' };
  }

  function newClip() {
    const chunks = [];
    const rec = new MediaRecorder(live.stream);
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.start();
    Object.assign(live, { rec, chunks, spoke: false, quietSince: 0, startedAt: performance.now() });
  }

  function cutClip(send) {
    const { rec, chunks, spoke } = live;
    const speaker = live.speaker;
    rec.onstop = () => { if (send && spoke) transcribeClip(new Blob(chunks, { type: rec.mimeType || 'audio/webm' }), speaker); };
    rec.stop();
    if (live.on) newClip();
  }

  function listen() {
    if (!live.on) return;
    const buf = new Uint8Array(live.analyser.fftSize);
    live.analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) { const n = (buf[i] - 128) / 128; sum += n * n; }
    const rms = Math.sqrt(sum / buf.length);
    const now = performance.now();
    if (now - live.lastLevel > 100) { live.lastLevel = now; toPanel({ type: 'vc-level', level: rms }); }
    if (rms > SPEECH_RMS) { live.spoke = true; live.quietSince = 0; }
    else if (rms < SILENCE_RMS && live.spoke) {
      if (!live.quietSince) live.quietSince = now;
      else if (now - live.quietSince > SILENCE_MS) cutClip(true);
    }
    if (now - live.startedAt > MAX_CLIP_MS) cutClip(live.spoke);
    live.raf = requestAnimationFrame(listen);
  }

  async function transcribeClip(blob, speaker) {
    try {
      const r = await apiAudio('/api/transcribe', blob);
      if (!r.text) return;
      const role = speaker === 'auto' ? 'speaker' : speaker;
      const t = { who: { clinician: 'Clinician', patient: 'Patient', speaker: 'Speaker' }[role], role, text: r.text };
      state.transcript.push(t);
      status(`Heard in ${(r.ms / 1000).toFixed(1)} s`);
      if (role === 'clinician') routeOn(t.text);
      else if (role === 'patient') fillAfterAnswer();
      else { await routeOn(t.text); fillAfterAnswer(); }
    } catch (e) { status(`Couldn't transcribe: ${e.message}`); }
  }

  async function startMic() {
    try {
      live.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) { status(`Microphone blocked: ${e.message}`); return; }
    state.playing = false;
    live.ctx = new AudioContext();
    live.analyser = live.ctx.createAnalyser();
    live.analyser.fftSize = 2048;
    live.ctx.createMediaStreamSource(live.stream).connect(live.analyser);
    live.on = true;
    clearSuggestions();
    state.enc = { id: 'live', title: 'Live visit', visit: todayVisit(), turns: [] };
    state.transcript = [];
    newClip();
    listen();
    status('Listening. Set who is talking, then speak.');
  }

  function stopMic() {
    if (!live.on) return;
    live.on = false;
    cancelAnimationFrame(live.raf);
    if (live.rec && live.rec.state !== 'inactive') cutClip(true); // send the last utterance
    live.stream.getTracks().forEach((t) => t.stop());
    live.ctx.close();
    toPanel({ type: 'vc-level', level: 0 });
    status('Mic off.');
  }

  // ---------- commands from the panel ----------
  function onCommand(m) {
    const a = m.arg;
    switch (m.cmd) {
      case 'hello': toPanel({ type: 'vc-state', state: snapshot() }); break;
      case 'mode':
        state.mode = a;
        if (a === 'live') { state.playing = false; } else { stopMic(); if (!state.enc || state.enc.id === 'live') selectEncounter(state.visits[0].id); }
        push();
        break;
      case 'visit': selectEncounter(a); break;
      case 'play':
        if (state.playing) break;
        if (state.enc && state.enc.id !== 'live' && state.idx >= state.enc.turns.length) selectEncounter(state.enc.id).then(play);
        else play();
        break;
      case 'pause':
        state.playing = false;
        for (const [id, why] of fillQueue) if (why === 'sweep' || why === 'finish') fillQueue.delete(id); // stops "Suggest the rest"
        push();
        break;
      case 'speed': state.speed = Number(a) || 1; push(); break;
      case 'reset': if (state.enc && state.enc.id !== 'live') selectEncounter(state.enc.id); break;
      case 'mic': if (a) startMic(); else stopMic(); break;
      case 'speaker': live.speaker = a; push(); break;
      case 'accept': accept(a); break;
      case 'dismiss': dismiss(a); break;
      case 'sweep': sweep(); break;
      case 'finish-all': finishAll(); break;
      case 'accept-all': // one click for every pending suggestion; still your click, never a submit
        for (const [id, sg] of state.sugg) if (sg.status === 'pending') accept(id);
        status('Accepted every suggestion. Check them; the page is never submitted.');
        push();
        break;
      case 'focus': {
        const f = byId.get(a);
        if (f) { f.container.scrollIntoView({ behavior: 'smooth', block: 'center' }); pointAt(f, false); }
        break;
      }
      default: break;
    }
  }

  if (IN_EXT) {
    chrome.runtime.onMessage.addListener((m) => { if (m && m.type === 'vc-cmd') onCommand(m); });
  } else {
    // Dev page: the same panel, docked on the right like Chrome's side panel.
    dock = document.createElement('iframe');
    dock.src = '/ext/sidepanel/panel.html?dock=1';
    dock.title = 'Edie Cursor panel';
    dock.style.cssText = `position:fixed;top:0;right:0;width:${PANEL_W}px;height:100vh;border:0;border-left:1px solid #e7eaf1;z-index:2147483646;background:#fff`;
    document.documentElement.appendChild(dock);
    document.documentElement.style.marginRight = `${PANEL_W}px`; // the page gets narrower, nothing is covered
    addEventListener('message', (ev) => {
      if (ev.source === dock.contentWindow && ev.data && ev.data.type === 'vc-cmd') onCommand(ev.data);
    });
  }

  (async () => {
    try {
      const h = await api('/api/health');
      state.models = h;
      state.fillReady = !!h.filler;
      const demoForm = document.querySelector('meta[name="demo-form"]');
      const q = demoForm ? `?form=${encodeURIComponent(demoForm.content)}` : '';
      state.visits = (await api(`/api/encounters${q}`)).map((v) => ({ id: v.id, title: v.title }));
      const asked = new URLSearchParams(location.search).get('enc');
      await selectEncounter(state.visits.some((v) => v.id === asked) ? asked : state.visits[0].id);
    } catch (e) {
      status(`Cannot reach the local server at localhost:8000 (${e.message}). Start it first.`);
    }
  })();

  window.__vc = { SCHEMA, state, moveAgentTo, busy: () => fillBusy || fillQueue.size > 0 }; // inspection hook (dev page only)
})();
