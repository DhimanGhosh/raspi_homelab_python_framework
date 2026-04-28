const byId = id => document.getElementById(id);
const statuses = ['All', 'Not Bought', 'Want to Read', 'Reading', 'Paused', 'Read'];
const columns = [
  ['title','Title'],['author','Author'],['genre','Genre'],['english_label','Complexity'],
  ['wow_score','WOW'],['emotional_score','Emotion'],['sadness_score','Sadness'],
  ['realism_score','Realism'],['personalized_score','Score'],['status','Status'],
  ['bookmark','Bookmark'],['buy','Buy'],['description','Description'],['actions','Actions']
];
const state = {
  books: [], settings: {}, stats: {}, editId: null, detailId: null,
  sortBy: 'personalized_score', sortDir: 'desc', bookmarkedOnly: false, page: 1
};

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, m =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m])
  );
}

async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  const txt = await r.text();
  let data = {};
  try { data = txt ? JSON.parse(txt) : {}; } catch {}
  if (!r.ok) throw new Error(data.detail || txt || ('HTTP ' + r.status));
  return data;
}

function showBusy(title, text) {
  byId('busyTitle').textContent = title;
  byId('busyText').textContent = text;
  byId('busyOverlay').classList.add('show');
}
function hideBusy() { byId('busyOverlay').classList.remove('show'); }

function slugStatus(v) { return 'status-' + String(v || '').toLowerCase().replace(/[^a-z0-9]+/g, '-'); }
function applyStatusClass(el) { if (!el) return; el.className = 'status-select ' + slugStatus(el.value); }
function badge(v, p = '') { return `<span class="pill score-${Number(v) || 0}">${esc(p)}${esc(v)}</span>`; }
function placeholderCover(title = 'Book') {
  const t = encodeURIComponent((title || 'Book').slice(0, 40));
  return `https://placehold.co/120x180/eef3f9/1f2f46?text=${t}`;
}
function coverHtml(url, cls, title) {
  const src = url || placeholderCover(title);
  return `<img class="${cls}" src="${esc(src)}" alt="cover" onerror="this.onerror=null;this.src='${placeholderCover(title)}'">`;
}
function titleMeta(b) {
  return [b.published_year, b.page_count ? `${b.page_count} pages` : '', b.language].filter(Boolean).join(' • ');
}
function statusSelect(b) {
  return `<select class="status-select ${slugStatus(b.status)}" onchange="changeStatus(${b.id},this.value,this)">${statuses.slice(1).map(s => `<option ${s === b.status ? 'selected' : ''}>${s}</option>`).join('')}</select>`;
}
function rowActions(id) {
  return `<div class="actions"><button class="secondary" onclick="openEdit(${id})">Edit</button><button class="secondary" onclick="refreshBook(${id})">Re-verify</button><button class="secondary" onclick="deleteBook(${id})">Delete</button></div>`;
}
function titleCell(b) {
  return `<div class="titlecell"><div class="clickable" onclick="openDetails(${b.id})">${coverHtml(b.cover_url, 'cover', b.title)}</div><div class="title-meta"><div class="clickable" onclick="openDetails(${b.id})"><b>${esc(b.title)}</b></div><div class="small">${esc(b.author || '')}</div><div class="small">${esc(titleMeta(b))}</div></div></div>`;
}

function setSort(k) {
  if (k === 'actions') return;
  state.sortDir = (state.sortBy === k && state.sortDir === 'desc') ? 'asc' : 'desc';
  state.sortBy = k;
  renderBooks();
}

function sortRows(rows) {
  const arr = [...rows];
  arr.sort((a, b) => {
    let av = a[state.sortBy], bv = b[state.sortBy];
    const an = Number(av), bn = Number(bv);
    if (!Number.isNaN(an) && !Number.isNaN(bn)) return state.sortDir === 'asc' ? an - bn : bn - an;
    av = String(av || '').toLowerCase(); bv = String(bv || '').toLowerCase();
    return state.sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  return arr;
}

function renderBooks() {
  const all = sortRows(state.books);
  const size = Number(byId('pageSize').value || 10);
  const total = all.length;
  const pages = size >= 99999 ? 1 : Math.max(1, Math.ceil(total / size));
  if (state.page > pages) state.page = pages;
  const start = size >= 99999 ? 0 : (state.page - 1) * size;
  const slice = size >= 99999 ? all : all.slice(start, start + size);
  byId('pageInfo').textContent = `Showing ${total ? start + 1 : 0}-${Math.min(start + slice.length, total)} of ${total}`;

  byId('thead').innerHTML = columns.map(([k, l]) =>
    `<th class="${state.sortBy === k ? 'sorting' : ''}">${k === 'actions' ? esc(l) : `<button class="linkbtn" onclick="setSort('${k}')">${esc(l)} ${state.sortBy === k ? (state.sortDir === 'asc' ? '↑' : '↓') : '↕'}</button>`}</th>`
  ).join('');

  byId('tbody').innerHTML = slice.map(b =>
    `<tr><td>${titleCell(b)}</td><td>${esc(b.author || '')}</td><td>${esc(b.genre || '')}</td><td>${esc(b.english_label || '')}</td><td>${badge(b.wow_score)}</td><td>${badge(b.emotional_score)}</td><td>${badge(b.sadness_score)}</td><td>${badge(b.realism_score)}</td><td class="nowrap"><b>${esc(b.personalized_score)}</b></td><td class="nowrap">${statusSelect(b)}</td><td class="nowrap">${b.bookmark_page ? `Page ${esc(b.bookmark_page)}` : (b.current_page ? `Page ${esc(b.current_page)}` : '—')}</td><td class="nowrap">${b.buy_link ? `<a href="${esc(b.buy_link)}" target="_blank">Buy</a>` : ''}</td><td class="descclip">${esc((b.description || '').slice(0, 140))}${(b.description || '').length > 140 ? '…' : ''}</td><td>${rowActions(b.id)}</td></tr>`
  ).join('');

  byId('mobileList').innerHTML = slice.map(b =>
    `<div class="book-card"><div class="book-top"><div style="min-width:0"><div class="clickable" onclick="openDetails(${b.id})"><b>${esc(b.title)}</b></div><div class="small">${esc(b.author || '')}</div><div class="small">${esc(titleMeta(b))}</div><div style="margin-top:8px">${badge(b.wow_score, 'WOW ')}${badge(b.emotional_score, 'Emotion ')}${badge(b.sadness_score, 'Sadness ')}${badge(b.realism_score, 'Realism ')}</div></div><div class="clickable" onclick="openDetails(${b.id})">${coverHtml(b.cover_url, 'book-cover', b.title)}</div></div><div class="row" style="margin-top:10px"><div><b>Genre</b><div class="small">${esc(b.genre || '')}</div></div><div><b>Score</b><div class="small">${esc(b.personalized_score)}</div></div></div><div style="margin-top:8px">${statusSelect(b)}</div><div class="small" style="margin-top:8px">${b.bookmark_page ? `Bookmark page ${esc(b.bookmark_page)}` : (b.current_page ? `Current page ${esc(b.current_page)}` : '')}</div><div class="row" style="margin-top:8px">${b.buy_link ? `<a href="${esc(b.buy_link)}" target="_blank">Buy link</a>` : ''}${b.info_link ? `<a href="${esc(b.info_link)}" target="_blank">Info</a>` : ''}</div><div style="margin-top:10px">${rowActions(b.id)}</div></div>`
  ).join('');

  document.querySelectorAll('.status-select').forEach(applyStatusClass);
}

async function loadGenres() {
  const genres = await api('/api/genres');
  byId('genre').innerHTML = '<option>All</option>' + genres.map(g => `<option>${esc(g)}</option>`).join('');
  byId('status').innerHTML = statuses.map(s => `<option>${esc(s)}</option>`).join('');
}

function syncFilterText() {
  const parts = [];
  const q = byId('q').value.trim();
  if (q) parts.push(q);
  if (byId('genre').value !== 'All') parts.push(`genre=${byId('genre').value}`);
  if (byId('status').value !== 'All') parts.push(`status=${byId('status').value}`);
  if (state.bookmarkedOnly) parts.push('bookmarked=true');
  byId('activeFilterText').textContent = parts.length ? `Active filter: ${parts.join(' | ')}` : 'No quick filter applied.';
}

async function loadBooks(extra = {}) {
  const p = new URLSearchParams();
  const q = (extra.q !== undefined ? extra.q : byId('q').value).trim();
  const genre = extra.genre !== undefined ? extra.genre : byId('genre').value;
  const status = extra.status !== undefined ? extra.status : byId('status').value;
  if (q) p.set('q', q);
  if (genre && genre !== 'All') p.set('genre', genre);
  if (status && status !== 'All') p.set('status', status);
  if (state.bookmarkedOnly) p.set('bookmarked', 'true');
  state.books = await api('/api/books?' + p.toString());
  state.page = 1;
  syncFilterText();
  renderBooks();
}

function sideBookCard(b, empty) {
  if (!b || !b.id) return `<div class="small">${esc(empty)}</div>`;
  return `<div class="book-top"><div style="min-width:0"><div class="clickable" onclick="openDetails(${b.id})"><b>${esc(b.title)}</b></div><div class="small">${esc(b.author || '')}</div><div class="small">${esc(b.genre || '')}</div><div class="small">Score: ${esc(b.personalized_score)} · ${esc(b.status)}</div><div class="small" style="margin-top:6px">${esc((b.description || '').slice(0, 220))}</div><div class="row" style="margin-top:8px">${b.buy_link ? `<a href="${esc(b.buy_link)}" target="_blank">Buy link</a>` : ''}</div></div><div class="clickable" onclick="openDetails(${b.id})">${coverHtml(b.cover_url, 'book-cover', b.title)}</div></div>`;
}

async function loadRecommendation() {
  const data = await api('/api/recommendation');
  const allowedStatuses = (data.allowed_statuses || []).join(', ') || 'None';
  const note = `No recommendation yet. Current rule: ${allowedStatuses}.`;
  byId('currentReading').innerHTML = sideBookCard(data.current, 'No book is currently marked as Reading.');
  byId('recommendation').innerHTML = sideBookCard(data.next, note) +
    `<div class="small" style="margin-top:8px">${esc(data.rule_label || 'Eligible statuses for automatic next recommendation')}: ${esc(allowedStatuses)}</div>`;
}

async function loadStats() {
  state.stats = await api('/api/stats');
  const items = [
    { label: 'Total',        value: state.stats.total,                          mode: 'all',       query: '' },
    { label: 'Not Bought',   value: state.stats.statuses['Not Bought'] || 0,    mode: 'status',    valueKey: 'Not Bought',   query: 'status=Not Bought' },
    { label: 'Want to Read', value: state.stats.statuses['Want to Read'] || 0,  mode: 'status',    valueKey: 'Want to Read', query: 'status=Want to Read' },
    { label: 'Reading',      value: state.stats.statuses['Reading'] || 0,       mode: 'status',    valueKey: 'Reading',      query: 'status=Reading' },
    { label: 'Paused',       value: state.stats.statuses['Paused'] || 0,        mode: 'status',    valueKey: 'Paused',       query: 'status=Paused' },
    { label: 'Read',         value: state.stats.statuses['Read'] || 0,          mode: 'status',    valueKey: 'Read',         query: 'status=Read' },
    { label: 'Bookmarked',   value: state.stats.bookmarked,                     mode: 'bookmarked', query: 'bookmarked=true' },
  ];
  byId('stats').innerHTML = items.map(it =>
    `<div class="stat" onclick='applyStatFilter(${JSON.stringify(it)})'><div class="small">${esc(it.label)}</div><div style="font-size:28px;font-weight:700">${esc(it.value)}</div></div>`
  ).join('');
  byId('topGenres').innerHTML = state.stats.top_genres.map(g =>
    `<div><a href="#" onclick="event.preventDefault(); applyGenreFilter('${esc(g.genre)}')">${esc(g.genre)}</a> <span style="float:right">${g.cnt}</span></div>`
  ).join('');
}

function applyGenreFilter(g) { byId('q').value = `genre=${g}`; byId('genre').value = 'All'; byId('status').value = 'All'; state.bookmarkedOnly = false; loadBooks({ q: `genre=${g}` }); }
function applyStatFilter(item) {
  byId('genre').value = 'All'; byId('status').value = 'All'; state.bookmarkedOnly = false;
  if (item.mode === 'all')       { byId('q').value = ''; loadBooks({ q: '', genre: 'All', status: 'All' }); return; }
  if (item.mode === 'status')    { byId('q').value = item.query; byId('status').value = item.valueKey; loadBooks({ q: item.query, genre: 'All', status: item.valueKey }); return; }
  if (item.mode === 'bookmarked') { state.bookmarkedOnly = true; byId('q').value = item.query; loadBooks({ q: 'bookmarked=true', genre: 'All', status: 'All' }); return; }
}

async function addBook() {
  const title = byId('title').value.trim();
  if (!title) return alert('Enter a book title');
  showBusy('Adding book…', 'Searching metadata and checking duplicates.');
  try {
    const res = await api('/api/books', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title, author: byId('author').value.trim(), isbn: byId('isbn').value.trim(), notes: byId('notes').value.trim() }) });
    ['title', 'author', 'isbn', 'notes'].forEach(id => byId(id).value = '');
    if (res && res._duplicate_skipped) alert(res._message || 'Duplicate skipped');
    await reloadAll();
  } catch (e) { alert(e.message); } finally { hideBusy(); }
}

async function dedupeBooks() {
  if (!confirm('Remove newer duplicate rows and keep the oldest matching title + author?')) return;
  showBusy('Removing duplicates…', 'Keeping the older matching title + author entry.');
  try { const res = await api('/api/books/deduplicate', { method: 'POST' }); alert(`Removed ${res.removed_count} duplicate entr${res.removed_count === 1 ? 'y' : 'ies'}.`); await reloadAll(); }
  catch (e) { alert(e.message); } finally { hideBusy(); }
}

async function changeStatus(id, status, el) { await api(`/api/books/${id}/status`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) }); if (el) applyStatusClass(el); reloadAll(); }

async function refreshBook(id) {
  showBusy('Re-verifying book…', 'Refreshing metadata, cover image, genre and description from online sources.');
  try { await api(`/api/books/${id}/refresh`, { method: 'POST' }); await reloadAll(); }
  catch (e) { alert(e.message); } finally { hideBusy(); }
}

async function deleteBook(id) { if (!confirm('Delete this book?')) return; await api(`/api/books/${id}`, { method: 'DELETE' }); reloadAll(); }

async function openDetails(id) {
  const b = await api(`/api/books/${id}`);
  state.detailId = id;
  byId('d_cover').src = b.cover_url || placeholderCover(b.title);
  byId('d_title').textContent = b.title || '';
  byId('d_author').textContent = b.author || '';
  byId('d_meta').textContent = titleMeta(b);
  byId('d_scores').innerHTML = `${badge(b.wow_score, 'WOW ')}${badge(b.emotional_score, 'Emotion ')}${badge(b.sadness_score, 'Sadness ')}${badge(b.realism_score, 'Realism ')}`;
  byId('d_status').innerHTML = statuses.slice(1).map(s => `<option ${s === b.status ? 'selected' : ''}>${s}</option>`).join('');
  applyStatusClass(byId('d_status'));
  byId('d_bookmark').textContent = b.bookmark_page ? `Page ${b.bookmark_page}${b.bookmark_note ? ` · ${b.bookmark_note}` : ''}` : (b.current_page ? `Current page ${b.current_page}` : '—');
  byId('d_score').textContent = b.personalized_score || '';
  byId('d_genre').textContent = [b.genre, b.subgenres].filter(Boolean).join(' · ') || '—';
  byId('d_description').textContent = b.description || '—';
  byId('d_notes').textContent = b.notes || '—';
  byId('d_buy').style.display = b.buy_link ? 'inline' : 'none'; byId('d_buy').href = b.buy_link || '#';
  byId('d_info').style.display = b.info_link ? 'inline' : 'none'; byId('d_info').href = b.info_link || '#';
  byId('detailDialog').showModal();
}

function openCurrentEdit() { closeDialog('detailDialog'); if (state.detailId) openEdit(state.detailId); }
async function reverifyCurrentDetail() { if (!state.detailId) return; await refreshBook(state.detailId); await openDetails(state.detailId); }
async function changeDetailStatus(status, el) { if (!state.detailId) return; await changeStatus(state.detailId, status, el); await openDetails(state.detailId); }

async function openEdit(id) {
  state.editId = id;
  const b = state.books.find(x => x.id === id) || await api(`/api/books/${id}`);
  [['e_title','title'],['e_author','author'],['e_genre','genre'],['e_subgenres','subgenres'],['e_language','language'],['e_year','published_year'],['e_pages','page_count'],['e_complexity','english_ease_score'],['e_wow','wow_score'],['e_emotion','emotional_score'],['e_sadness','sadness_score'],['e_realism','realism_score'],['e_current','current_page'],['e_bookmark','bookmark_page'],['e_buy','buy_link'],['e_info','info_link'],['e_cover','cover_url'],['e_publisher','publisher'],['e_description','description'],['e_notes','notes'],['e_bookmark_note','bookmark_note'],['e_isbn','isbn']].forEach(([id, key]) => byId(id).value = b[key] ?? '');
  byId('e_status').innerHTML = statuses.slice(1).map(s => `<option ${s === b.status ? 'selected' : ''}>${s}</option>`).join('');
  const br = await api(`/api/books/${id}/score-breakdown`);
  byId('scoreBreakdown').innerHTML = `<div><b>${esc(br.formula)}</b></div>` + Object.entries(br.components).map(([k, v]) => `<div>${esc(k.replaceAll('_', ' '))}: ${esc(v)}</div>`).join('');
  byId('editDialog').showModal();
}

async function saveEdit() {
  const payload = { title: byId('e_title').value, author: byId('e_author').value, isbn: byId('e_isbn').value, genre: byId('e_genre').value, subgenres: byId('e_subgenres').value, language: byId('e_language').value, published_year: byId('e_year').value, page_count: +(byId('e_pages').value || 0), english_ease_score: +(byId('e_complexity').value || 3), wow_score: +(byId('e_wow').value || 3), emotional_score: +(byId('e_emotion').value || 3), sadness_score: +(byId('e_sadness').value || 2), realism_score: +(byId('e_realism').value || 3), current_page: +(byId('e_current').value || 0), bookmark_page: +(byId('e_bookmark').value || 0), buy_link: byId('e_buy').value, info_link: byId('e_info').value, cover_url: byId('e_cover').value, publisher: byId('e_publisher').value, description: byId('e_description').value, notes: byId('e_notes').value, bookmark_note: byId('e_bookmark_note').value, status: byId('e_status').value };
  await api(`/api/books/${state.editId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  closeDialog('editDialog'); reloadAll();
}

async function loadSettings() { state.settings = await api('/api/settings'); }

async function openSettings() {
  await loadSettings();
  Object.entries(state.settings).forEach(([k, v]) => { const el = byId('s_' + k); if (el) el.value = v; });
  const enabled = new Set(String(state.settings.recommendation_statuses || 'Want to Read, Paused').split(',').map(s => s.trim()).filter(Boolean));
  document.querySelectorAll('[data-rec-status]').forEach(el => { el.checked = enabled.has(el.dataset.recStatus); });
  const panels = [...document.querySelectorAll('#settingsDialog details.settings-accordion')];
  panels.forEach((el, i) => { el.open = i === 0; });
  byId('settingsDialog').showModal();
}

async function saveSettings() {
  const payload = {};
  ['english_weight','wow_weight','emotion_weight','sadness_weight','realism_weight','genre_bonus_weight','genre_bonus_value','genre_bonus_keywords','score_formula_label','recommendation_explain_label'].forEach(k => { const el = byId('s_' + k); if (el) payload[k] = el.type === 'number' ? Number(el.value) : el.value; });
  payload.recommendation_statuses = [...document.querySelectorAll('[data-rec-status]:checked')].map(el => el.dataset.recStatus).join(', ');
  await api('/api/settings', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  closeDialog('settingsDialog'); reloadAll();
}

function triggerImport(type) { byId(type === 'csv' ? 'importCsv' : 'importJson').click(); }

async function uploadImport(type, file) {
  showBusy(type === 'csv' ? 'Importing CSV…' : 'Importing JSON…', 'Matching titles, fetching metadata, checking duplicates and saving your library.');
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch(`/api/import/${type}`, { method: 'POST', body: fd });
    const txt = await r.text();
    if (!r.ok) throw new Error(txt);
    let data = {}; try { data = JSON.parse(txt); } catch {}
    alert(data.received !== undefined ? `Imported ${data.received} row(s). Inserted: ${data.inserted || 0}, Updated: ${data.updated || 0}, Skipped: ${data.skipped || 0}.` : 'Import complete.');
    await reloadAll();
  } catch (e) { alert(e.message); } finally { hideBusy(); byId(type === 'csv' ? 'importCsv' : 'importJson').value = ''; }
}

async function backupNow() { const r = await api('/api/backup', { method: 'POST' }); alert('Backup created: ' + r.backup_path); }

async function openBackups() {
  const data = await api('/api/backups');
  byId('backupList').innerHTML = (data.items || []).length
    ? data.items.map(b => `<div class="card" style="margin-bottom:8px"><div><b>${esc(b.name)}</b></div><div class="small">${esc(b.modified_at)} · ${Math.round((b.size || 0) / 1024)} KB</div><div class="row" style="margin-top:8px"><button class="secondary" onclick="restoreBackup('${esc(b.name)}')">Restore</button><button class="secondary" onclick="deleteBackup('${esc(b.name)}')">Remove</button></div></div>`).join('')
    : '<div class="small">No backups found.</div>';
  byId('backupDialog').showModal();
}

async function restoreBackup(name) {
  if (!confirm(`Restore backup ${name}? Current DB will be backed up first.`)) return;
  showBusy('Restoring backup…', 'Restoring the selected backup and reloading the app.');
  try { await api('/api/backups/restore', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }); closeDialog('backupDialog'); await reloadAll(); }
  catch (e) { alert(e.message); } finally { hideBusy(); }
}

async function deleteBackup(name) {
  if (!confirm(`Remove backup ${name}?`)) return;
  await api(`/api/backups/${encodeURIComponent(name)}`, { method: 'DELETE' });
  await openBackups();
}

function clearSearch() { byId('q').value = ''; state.bookmarkedOnly = false; byId('genre').value = 'All'; byId('status').value = 'All'; loadBooks({ q: '', genre: 'All', status: 'All' }); }
function closeDialog(id) { byId(id).close(); }
function downloadFile(url) { window.location = url; }
function prevPage() { if (state.page > 1) { state.page--; renderBooks(); } }
function nextPage() {
  const total = state.books.length;
  const size = Number(byId('pageSize').value || 10);
  const pages = size >= 99999 ? 1 : Math.max(1, Math.ceil(total / size));
  if (state.page < pages) { state.page++; renderBooks(); }
}

async function reloadAll() {
  await Promise.all([loadGenres(), loadSettings(), loadBooks(), loadRecommendation(), loadStats()]);
}

function setupSettingsAccordion() {
  document.querySelectorAll('#settingsDialog details.settings-accordion > summary').forEach(summary => {
    summary.onclick = e => {
      e.preventDefault();
      const details = summary.parentElement;
      const willOpen = !details.open;
      document.querySelectorAll('#settingsDialog details.settings-accordion').forEach(el => { el.open = false; });
      details.open = willOpen;
    };
  });
}

setupSettingsAccordion();
byId('q').addEventListener('input', () => loadBooks());
byId('genre').addEventListener('change', () => loadBooks());
byId('status').addEventListener('change', () => loadBooks());
byId('pageSize').addEventListener('change', () => { state.page = 1; renderBooks(); });
byId('importCsv').addEventListener('change', e => e.target.files[0] && uploadImport('csv', e.target.files[0]));
byId('importJson').addEventListener('change', e => e.target.files[0] && uploadImport('json', e.target.files[0]));
reloadAll();
