// Mock EHR page behaviour: what a real documentation screen does on its own,
// independent of the extension. Patient banner, allergy flag, save state,
// section counts, code boxes, the section in view, and the form's skip rules.
(() => {
  const form = document.getElementById('assessment');
  const $ = (id) => document.getElementById(id);

  // Patient banner: ?enc=gen-02, or the demo replay announcing a visit.
  function showVisit(id) {
    const v = (window.DEMO_VISITS || {})[id];
    if (!v) return;
    const d = new Date(`${v.date}T12:00:00`);
    for (const [el, val] of [['pt-name', v.name], ['pt-age', v.age], ['pt-sex', v.sex], ['pt-dob', v.dob], ['pt-mrn', v.mrn],
      ['pt-clin', v.clinician], ['me-name', v.clinician]]) $(el).textContent = val;
    $('pt-date').textContent = `Visit ${d.toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })}`;
  }
  const first = Object.keys(window.DEMO_VISITS || {})[0];
  showVisit(new URLSearchParams(location.search).get('enc') || first);
  addEventListener('message', (ev) => {
    if (ev.data && ev.data.type === 'vc-demo-visit') { form.reset(); refresh(); showVisit(ev.data.id); }
  });

  const groups = new Map();
  for (const el of form.querySelectorAll('input, select, textarea')) {
    if (!groups.has(el.name)) groups.set(el.name, []);
    groups.get(el.name).push(el);
  }
  const answered = (els) => els.some((x) => ((x.type === 'radio' || x.type === 'checkbox') ? x.checked : x.value.trim() !== ''));
  const holder = (name) => { const el = groups.get(name)[0]; return el.closest('[role="group"], fieldset'); };

  function refresh() {
    // Skip rules from the form spec, e.g. "No known allergies" skips allergens and reactions.
    for (const rule of window.SKIP_RULES || []) {
      const on = (groups.get(rule.if) || []).some((x) => (x.checked || x.tagName === 'SELECT') && x.value === rule.equals);
      for (const name of rule.skip) {
        for (const x of groups.get(name) || []) {
          x.disabled = on;
          if (on) { if (x.type === 'radio' || x.type === 'checkbox') x.checked = false; else x.value = ''; }
        }
        const h = holder(name);
        h.classList.toggle('skipped', on);
        h.title = on ? `Skipped: ${rule.note}` : '';
      }
    }
    // Allergy flag in the banner, the way EHRs surface it.
    const flag = $('pt-allergy');
    const status = (groups.get('ALLERGY_STATUS') || []).find((x) => x.checked);
    if (status) {
      const names = (groups.get('ALLERGENS') || []).filter((x) => x.checked).map((x) => x.getAttribute('aria-label'));
      flag.textContent = status.value === 'N' ? 'No known allergies' : `Allergies: ${names.join(', ') || 'see list'}`;
      flag.classList.toggle('ok', status.value === 'N');
    }
    const per = {};
    let done = 0;
    for (const [name, els] of groups) {
      const sec = els[0].closest('section.sec').dataset.sec;
      per[sec] = per[sec] || [0, 0];
      per[sec][1] += 1;
      if (answered(els)) { per[sec][0] += 1; done += 1; }
      const box = document.querySelector(`[data-code-for="${name}"]`);
      if (box) { const c = els.find((x) => x.checked); box.textContent = c ? c.value : ''; }
    }
    for (const [sec, [a, n]] of Object.entries(per)) {
      const el = document.querySelector(`[data-count="${sec}"]`);
      el.textContent = `${a}/${n}`;
      el.classList.toggle('done', a === n);
    }
    $('done-count').textContent = `${done} of ${groups.size} documented`;
  }

  let saveTimer = 0;
  function saved() {
    const s = $('save-state');
    s.textContent = 'Saving…';
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      s.textContent = `Draft · saved ${new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`;
    }, 600);
  }
  form.addEventListener('change', () => { refresh(); saved(); });
  form.addEventListener('input', () => { refresh(); saved(); });
  refresh();

  // Highlight the section in view.
  const links = new Map([...document.querySelectorAll('.tabs a')].map((a) => [a.dataset.sec, a]));
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) {
      for (const a of links.values()) a.classList.remove('active');
      links.get(en.target.dataset.sec).classList.add('active');
    }
  }, { rootMargin: '-30% 0px -65% 0px' });
  document.querySelectorAll('section.sec').forEach((s) => io.observe(s));
})();
