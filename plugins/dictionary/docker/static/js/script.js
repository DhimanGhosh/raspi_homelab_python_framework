const byId = id => document.getElementById(id);
const esc = s => (s ?? '').toString().replace(/[&<>"']/g, m => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]
));

function suggestionPills(values) {
  return values.map(x => `<span class="linkish" data-word="${esc(x)}">${esc(x)}</span>`).join('');
}

async function go(forcedWord) {
  const q = (forcedWord || byId('q').value).trim();
  const out = byId('out');
  const btn = byId('searchBtn');
  if (!q) {
    out.innerHTML = '<div class="error">Please enter a word.</div>';
    return;
  }
  byId('q').value = q;
  btn.disabled = true;
  out.innerHTML = '<div class="small">Searching…</div>';
  try {
    const r = await fetch('/api/lookup?q=' + encodeURIComponent(q), {
      headers: { 'Accept': 'application/json' }
    });
    if (!r.ok) throw new Error('Lookup failed with HTTP ' + r.status);
    const d = await r.json();
    if (!d.found) {
      out.innerHTML =
        '<div>No result found.</div>' +
        ((d.suggestions || []).length
          ? `<div class="hint"><b>Did you mean:</b> ${suggestionPills(d.suggestions)}</div>`
          : '');
      bindSuggestionClicks();
      return;
    }
    out.innerHTML = `
      <h2 style="margin:0 0 6px 0">${esc(d.query)}</h2>
      <div><b>Synonyms:</b> ${d.synonyms.map(x => `<span class="pill">${esc(x)}</span>`).join('') || '<span class="small">None</span>'}</div>
      <div><b>Antonyms:</b> ${d.antonyms.map(x => `<span class="pill">${esc(x)}</span>`).join('') || '<span class="small">None</span>'}</div>
      ${d.meanings.map(m => `
        <div class="item">
          <div><b>${esc(m.part_of_speech)}</b></div>
          <div>${esc(m.definition)}</div>
          <div class="small">${m.examples.map(esc).join(' • ') || 'No examples available.'}</div>
          <div class="small">Related: ${m.lemmas.map(esc).join(', ')}</div>
        </div>
      `).join('')}
    `;
  } catch (err) {
    out.innerHTML = `<div class="error">${esc(err.message || 'Search failed.')}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function bindSuggestionClicks() {
  document.querySelectorAll('.linkish[data-word]').forEach(el => {
    el.addEventListener('click', () => go(el.dataset.word || ''));
  });
}

// Sync app name / version from health endpoint
fetch('/api/health').then(r => r.json()).then(data => {
  document.title = data.service || document.title;
  const n = byId('appName');
  const v = byId('appVersion');
  if (n && data.service) n.textContent = data.service;
  if (v && data.version) v.textContent = 'v' + data.version;
}).catch(() => {});

// Clear button
const q = byId('q');
const clearBtn = byId('clearQ');
function syncClear() { clearBtn.style.display = q.value ? 'block' : 'none'; }
clearBtn.addEventListener('click', () => { q.value = ''; syncClear(); q.focus(); });
q.addEventListener('input', syncClear);
syncClear();
