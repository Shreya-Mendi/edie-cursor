// Edie Cursor panel: a view of the content script's state, plus buttons that send
// commands back. Runs as Chrome's side panel (extension page) or docked in the dev
// page (?dock=1), where it talks to the page through postMessage instead.
(() => {
  const DOCK = new URLSearchParams(location.search).has('dock');
  const IN_EXT = !DOCK && typeof chrome !== 'undefined' && !!(chrome.runtime && chrome.runtime.id);
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  let tabId = null;
  let S = null; // last state from the page

  async function send(cmd, arg) {
    const msg = { type: 'vc-cmd', cmd, arg };
    if (!IN_EXT) { parent.postMessage(msg, location.origin); return; }
    try {
      if (tabId == null) { const [t] = await chrome.tabs.query({ active: true, currentWindow: true }); tabId = t ? t.id : null; }
      await chrome.tabs.sendMessage(tabId, msg);
      $('nopage') && $('nopage').remove();
    } catch (e) { showNoPage(); }
  }

  function showNoPage() {
    if ($('nopage')) return;
    const d = document.createElement('div');
    d.id = 'nopage';
    d.className = 'nopage';
    d.textContent = 'Open a page with a form to start. For the demo: http://localhost:8000/mock/oasis.html';
    document.querySelector('header').after(d);
  }

  function onMsg(m) {
    if (!m) return;
    if (m.type === 'vc-state') render(m.state);
    if (m.type === 'vc-level') $('level').style.width = `${Math.min(100, m.level * 700)}%`;
    if (m.type === 'vc-show') showEvidence(m.id); // a chip's evidence was clicked on the page
  }

  // ---------- evidence: jump from a suggestion to the transcript line it came from ----------
  // Same rule as the server's gate (vc/fill.py): a line matches a quote when every word of the
  // quote (lowercase, punctuation stripped) appears in that one line. When several lines match,
  // take the latest patient/caregiver line, else the latest line. Each quote picks its own line.
  let SEL = null; // id of the suggestion whose evidence is highlighted; stays until another is clicked
  const words = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().split(' ').filter(Boolean);

  function evidenceLines(quotes) { // Map(index in the shown transcript -> [quote word lists])
    const hits = new Map();
    for (const q of quotes || []) {
      const qw = words(q);
      if (!qw.length || !S) continue;
      let latest = -1, latestPt = -1;
      S.transcript.forEach((t, i) => {
        const have = new Set(words(t.text));
        if (!qw.every((w) => have.has(w))) return;
        latest = i;
        if (t.role === 'patient' || t.role === 'caregiver') latestPt = i;
      });
      const i = latestPt >= 0 ? latestPt : latest;
      if (i >= 0) hits.set(i, [...(hits.get(i) || []), qw]);
    }
    return hits;
  }

  // The quoted words, wrapped in <mark>. The exact phrase when the line has it in order,
  // otherwise every occurrence of the quote's words (the gate accepts any word order).
  function lineHTML(text, qws) {
    const toks = [...text.matchAll(/[A-Za-z0-9]+/g)].map((m) => ({ w: m[0].toLowerCase(), a: m.index, b: m.index + m[0].length }));
    const spans = [];
    for (const qw of qws) {
      let found = false;
      for (let i = 0; i + qw.length <= toks.length; i++) {
        if (qw.every((w, k) => toks[i + k].w === w)) { spans.push([toks[i].a, toks[i + qw.length - 1].b]); found = true; i += qw.length - 1; }
      }
      if (!found) { const need = new Set(qw); for (const t of toks) if (need.has(t.w)) spans.push([t.a, t.b]); }
    }
    spans.sort((x, y) => x[0] - y[0]);
    const merged = [];
    for (const s of spans) {
      const last = merged[merged.length - 1];
      if (last && /^\s*$/.test(text.slice(last[1], s[0]))) last[1] = Math.max(last[1], s[1]);
      else merged.push([s[0], s[1]]);
    }
    let out = '', pos = 0;
    for (const [a, b] of merged) { out += `${esc(text.slice(pos, a))}<mark>${esc(text.slice(a, b))}</mark>`; pos = b; }
    return out + esc(text.slice(pos));
  }

  // Lines are appended, never rebuilt, so a state push cannot cancel a smooth scroll in flight.
  // Every line carries data-i, its index in the whole transcript (stable while the window slides).
  let logKeys = [];
  let jumpedAt = 0;
  function renderLog(s) {
    const log = $('log');
    const keys = s.transcript.map((t, i) => `${(s.tFrom || 0) + i}|${t.role}|${t.who}|${t.text}`);
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
    if (!(keys.length >= logKeys.length && logKeys.every((k, i) => k === keys[i]))) { log.innerHTML = ''; logKeys = []; }
    for (let i = logKeys.length; i < keys.length; i++) {
      const t = s.transcript[i];
      const d = document.createElement('div');
      d.dataset.i = String((s.tFrom || 0) + i);
      d.innerHTML = `<span class="who-${esc(t.role)}">${esc(t.who)}:</span> <span class="tx">${esc(t.text)}</span>`;
      log.appendChild(d);
    }
    logKeys = keys;
    const sel = SEL && s.suggestions.find((x) => x.id === SEL);
    const hits = sel ? evidenceLines(sel.quotes) : new Map();
    [...log.children].forEach((d, i) => {
      const qws = hits.get(i);
      const sig = qws ? JSON.stringify(qws) : '';
      if (d._hl === sig || (d._hl === undefined && !sig)) { d._hl = sig; return; }
      d._hl = sig;
      d.classList.toggle('evid', !!qws);
      d.lastElementChild.innerHTML = qws ? lineHTML(s.transcript[i].text, qws) : esc(s.transcript[i].text);
    });
    if (atBottom && Date.now() - jumpedAt > 900) log.scrollTop = log.scrollHeight;
  }

  function showEvidence(id) {
    if (!S || !S.suggestions.some((x) => x.id === id)) return;
    SEL = id;
    renderLog(S);
    renderSugg(S);
    const log = $('log');
    const el = log.querySelector('.evid'); // the first (topmost) highlighted line
    if (!el) { $('status').textContent = 'That evidence is not in the conversation shown here.'; return; }
    jumpedAt = Date.now();
    const lr = log.getBoundingClientRect(), er = el.getBoundingClientRect();
    const top = log.scrollTop + (er.top - lr.top - log.clientTop) - (log.clientHeight - er.height) / 2; // centered in the pane
    const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
    log.scrollTo({ top: Math.max(0, top), behavior: calm ? 'auto' : 'smooth' });
  }

  let suggKey = '';
  function renderSugg(s) {
    const items = [...s.suggestions].sort((a, b) => (a.status === b.status ? 0 : a.status === 'pending' ? -1 : 1));
    const pending = items.filter((x) => x.status === 'pending').length;
    $('count').textContent = String(pending);
    $('empty').hidden = items.length > 0;
    const key = JSON.stringify([SEL, items.map((x) => [x.id, x.label, x.display, x.status])]);
    if (key === suggKey) return; // nothing changed: keep the rows (and hover, clicks, scroll) as they are
    suggKey = key;
    $('sugg').innerHTML = items.map((x) => `
      <li data-id="${esc(x.id)}" class="${x.status === 'accepted' ? 'done' : ''}${x.id === SEL ? ' sel' : ''}" title="Show in conversation">
        <div class="meta"><span class="fid">${esc(x.id)}</span><span class="lbl">${esc(x.label)}</span></div>
        <div class="val">${esc(x.display)}</div>
        ${x.status === 'pending' ? '<button class="acc">Accept</button><button class="dis" aria-label="Dismiss">✕</button>' : ''}
      </li>`).join('');
  }

  if (IN_EXT) {
    chrome.runtime.onMessage.addListener((m, sender) => { if (sender.tab && sender.tab.id === tabId) onMsg(m); });
    chrome.tabs.onActivated.addListener(({ tabId: id }) => { tabId = id; send('hello'); });
  } else {
    addEventListener('message', (ev) => { if (ev.source === parent) onMsg(ev.data); });
  }

  function render(s) {
    S = s;
    const m = s.models || {};
    const ready = !!(m.router && m.filler);
    $('models').classList.toggle('on', ready);
    $('models').lastElementChild.textContent = ready ? 'Local models ready' : 'Models loading';
    $('models').title = [['Cursor steering', m.router], ['Value filling', m.filler], ['Speech to text', m.asr]]
      .map(([k, v]) => `${k}: ${v || 'not installed'}`).join('\n');

    for (const b of document.querySelectorAll('.seg button')) b.classList.toggle('on', b.dataset.mode === s.mode);
    $('live-ctl').hidden = s.mode !== 'live';
    $('replay-ctl').hidden = s.mode !== 'replay';

    const mic = $('mic');
    mic.textContent = s.live.on ? 'Stop listening' : 'Start listening';
    mic.classList.toggle('rec', s.live.on);
    mic.disabled = !m.asr;
    for (const b of document.querySelectorAll('.who button')) b.classList.toggle('on', b.dataset.s === s.live.speaker);

    const sel = $('visit');
    if (sel.options.length !== s.visits.length) {
      sel.innerHTML = s.visits.map((v) => `<option value="${esc(v.id)}">${esc(v.id)} · ${esc(v.title)}</option>`).join('');
    }
    if (s.visitId && s.visitId !== 'live') sel.value = s.visitId;
    $('play').textContent = s.playing ? 'Pause' : (s.turnIdx >= s.turnTotal && s.turnTotal ? 'Replay' : 'Play');
    $('speed').value = String(s.speed);
    $('prog').style.width = s.turnTotal ? `${(100 * s.turnIdx) / s.turnTotal}%` : '0%';

    const now = $('now');
    now.classList.toggle('idle', !s.agent);
    $('now-text').textContent = s.agent ? `Following: ${s.agent.label}` : 'Edie appears when the conversation starts.';

    if (SEL && !s.suggestions.some((x) => x.id === SEL)) SEL = null; // dismissed or reset: drop the highlight
    renderLog(s);
    renderSugg(s);

    $('status').textContent = s.status || '';
  }

  document.querySelector('.seg').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) send('mode', b.dataset.mode); });
  $('mic').addEventListener('click', () => send('mic', !(S && S.live.on)));
  document.querySelector('.who').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) send('speaker', b.dataset.s); });
  $('visit').addEventListener('change', (e) => send('visit', e.target.value));
  $('play').addEventListener('click', () => send(S && S.playing ? 'pause' : 'play'));
  $('speed').addEventListener('change', (e) => send('speed', e.target.value));
  $('reset').addEventListener('click', () => send('reset'));
  $('sweep').addEventListener('click', () => send('sweep'));
  $('acceptall').addEventListener('click', () => send('accept-all'));
  $('finishall').addEventListener('click', () => send('finish-all'));
  $('sugg').addEventListener('click', (e) => {
    const li = e.target.closest('li');
    if (!li) return;
    const id = li.dataset.id;
    if (e.target.closest('.acc')) send('accept', id);
    else if (e.target.closest('.dis')) send('dismiss', id);
    else { showEvidence(id); send('focus', id); }
  });

  send('hello');
})();
