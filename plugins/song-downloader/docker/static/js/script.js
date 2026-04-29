const el = (id) => document.getElementById(id);
const openLogs = new Set();
const RETAG_MEMORY_KEY = 'song-downloader-retag-memory-v1';
let librarySongs = [];
let lastHealth   = null;

// ── Health ─────────────────────────────────────────────────────────────────────

async function fetchHealth() {
  const res  = await fetch('/api/health', { cache: 'no-store' });
  const data = await res.json();
  lastHealth = data;
  el('healthStatus').textContent = `OK • v${data.version}`;
}

// ── Payload builders ───────────────────────────────────────────────────────────

function buildPayload(prefix = '') {
  return {
    song_name:    el(`${prefix}song_name`).value.trim(),
    artist_names: el(`${prefix}artist_names`).value.trim(),
    album_name:   el(`${prefix}album_name`).value.trim(),
    youtube_url:  el(`${prefix}youtube_url`).value.trim(),
    cookies_path: (el(`${prefix}cookies_path`)?.value || '').trim(),
    rename_to:    prefix ? '' : el('rename_to').value.trim(),
    auto_move:    prefix ? true : el('auto_move').checked,
    selected_file: prefix ? el('selected_file').value : '',
    album_art_url: prefix
      ? (el('retag_album_art_url')?.value.trim() || '')
      : (el('album_art_url')?.value.trim() || ''),
  };
}

function clearInputs(ids) {
  ids.forEach((id) => {
    const node = el(id);
    if (!node) return;
    if (node.type === 'checkbox') node.checked = true;
    else node.value = '';
  });
}

// ── Filename parser ────────────────────────────────────────────────────────────

function parseSongFilename(filePath) {
  const rawName = (filePath || '').split('/').pop() || '';
  const base    = rawName.replace(/\.mp3$/i, '').trim();
  if (!base) return { song_name: '', album_name: '', artist_names: '' };
  const normalized = base
    .replace(/[–—]/g, '-')
    .replace(/[，]/g, ',')
    .replace(/\s+-\s+/g, ' - ')
    .trim();
  const parts = normalized.split(' - ').map((p) => p.trim()).filter(Boolean);
  if (parts.length >= 3) return { song_name: parts[0], album_name: parts.slice(1, -1).join(' - '), artist_names: parts[parts.length - 1] };
  if (parts.length === 2) return { song_name: parts[0], album_name: '', artist_names: parts[1] };
  return { song_name: normalized, album_name: '', artist_names: '' };
}

// ── Retag memory ───────────────────────────────────────────────────────────────

function loadRetagMemory() {
  try { return JSON.parse(localStorage.getItem(RETAG_MEMORY_KEY) || '{}'); }
  catch { return {}; }
}

function saveRetagMemory(memory) {
  localStorage.setItem(RETAG_MEMORY_KEY, JSON.stringify(memory));
}

function storeCurrentRetagState() {
  const selected = el('selected_file')?.value;
  if (!selected) return;
  const memory     = loadRetagMemory();
  memory[selected] = {
    song_name:    el('retag_song_name')?.value    || '',
    artist_names: el('retag_artist_names')?.value || '',
    album_name:   el('retag_album_name')?.value   || '',
    youtube_url:  el('retag_youtube_url')?.value  || '',
    album_art_url: el('retag_album_art_url')?.value || '',
  };
  saveRetagMemory(memory);
}

function applyRetagStateForSelected() {
  const selected = el('selected_file').value;
  if (!selected) {
    ['retag_song_name','retag_album_name','retag_artist_names','retag_youtube_url','retag_album_art_url']
      .forEach((id) => { if (el(id)) el(id).value = ''; });
    return;
  }
  const memory     = loadRetagMemory();
  const remembered = memory[selected];
  if (remembered) {
    if (el('retag_song_name'))    el('retag_song_name').value    = remembered.song_name    || '';
    if (el('retag_album_name'))   el('retag_album_name').value   = remembered.album_name   || '';
    if (el('retag_artist_names')) el('retag_artist_names').value = remembered.artist_names || '';
    if (el('retag_youtube_url'))  el('retag_youtube_url').value  = remembered.youtube_url  || '';
    if (el('retag_album_art_url')) el('retag_album_art_url').value = remembered.album_art_url || '';
    return;
  }
  const parsed = parseSongFilename(selected);
  if (el('retag_song_name'))    el('retag_song_name').value    = parsed.song_name    || '';
  if (el('retag_album_name'))   el('retag_album_name').value   = parsed.album_name   || '';
  if (el('retag_artist_names')) el('retag_artist_names').value = parsed.artist_names || '';
  if (el('retag_youtube_url'))  el('retag_youtube_url').value  = '';
  if (el('retag_album_art_url')) el('retag_album_art_url').value = '';
}

// ── Library song list ──────────────────────────────────────────────────────────

function renderLibrarySongOptions(songs, preferredValue = '') {
  const select       = el('selected_file');
  const currentValue = preferredValue || select.value;
  const search       = (el('library_song_search')?.value || '').trim().toLowerCase();
  const filtered     = songs.filter((s) =>
    !search || s.path.toLowerCase().includes(search) || (s.name || '').toLowerCase().includes(search)
  );
  select.innerHTML = '<option value="">Select a song from /mnt/nas/media/music</option>';
  filtered.forEach((song) => {
    const opt = document.createElement('option');
    opt.value       = song.path;
    opt.textContent = song.name || song.path;
    select.appendChild(opt);
  });
  if (currentValue && Array.from(select.options).some((o) => o.value === currentValue)) {
    select.value = currentValue;
  } else if (filtered.length === 1) {
    select.value = filtered[0].path;
  } else {
    select.value = '';
  }
  applyRetagStateForSelected();
}

// ── Job rendering ──────────────────────────────────────────────────────────────

function progressWidth(job) {
  return `${Math.max(0, Math.min(100, Number(job.progress || 0)))}%`;
}

function rememberOpenLogs() {
  document.querySelectorAll('.logs-box[data-job-id]').forEach((node) => {
    if (node.open) openLogs.add(node.dataset.jobId);
    else openLogs.delete(node.dataset.jobId);
  });
}

function escapeHtml(text) {
  return String(text || '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

function renderJobSummary(jobs) {
  const node = el('jobsSummary');
  if (!node) return;
  const c = { total: jobs.length, queued: 0, running: 0, completed: 0, failed: 0, aborted: 0 };
  jobs.forEach((job) => { c[job.status] = (c[job.status] || 0) + 1; });
  node.innerHTML = `
    <strong>Total:</strong> ${c.total}
    &nbsp;•&nbsp;<strong>Queued:</strong> ${c.queued}
    &nbsp;•&nbsp;<strong>Running:</strong> ${c.running}
    &nbsp;•&nbsp;<strong>Completed:</strong> ${c.completed}
    &nbsp;•&nbsp;<strong>Failed:</strong> ${c.failed}
    &nbsp;•&nbsp;<strong>Aborted:</strong> ${c.aborted}
  `;
}

function renderJobs(jobs) {
  rememberOpenLogs();
  renderJobSummary(jobs);
  const container = el('jobsContainer');
  container.innerHTML = '';
  if (!jobs.length) {
    container.innerHTML = '<div class="empty-state">No jobs yet.</div>';
    return;
  }
  jobs.forEach((job) => {
    const song    = job.payload?.song_name || job.payload?.selected_file || '—';
    const artists = job.payload?.artist_names || '—';
    const album   = job.payload?.album_name || 'Unknown';
    const youtube = job.payload?.youtube_url || 'Search mode';
    const jobType = job.payload?.job_type || 'download';

    const card = document.createElement('article');
    card.className = 'job-card';
    card.innerHTML = `
      <div class="job-top">
        <div>
          <div class="job-status ${escapeHtml(job.status)}">${escapeHtml(job.status)}</div>
          <div class="job-time">${escapeHtml(job.updated_at || job.created_at || '')}</div>
        </div>
        <div class="job-actions-top">
          <div class="job-id">${escapeHtml(job.id.slice(0, 8))}</div>
          ${(job.status === 'running' || job.status === 'queued')
            ? `<button type="button" class="ghost-btn danger abort-job-btn" data-job-id="${escapeHtml(job.id)}">Abort</button>`
            : ''}
        </div>
      </div>
      <div class="job-main">
        <div><strong>Type:</strong> ${escapeHtml(jobType)}</div>
        <div><strong>Song:</strong> ${escapeHtml(song)}</div>
        <div><strong>Artists:</strong> ${escapeHtml(artists)}</div>
        <div><strong>Album:</strong> ${escapeHtml(album)}</div>
        <div><strong>YouTube:</strong> ${escapeHtml(youtube)}</div>
        <div><strong>Final file:</strong> ${escapeHtml(job.final_file || '—')}</div>
        <div><strong>Error:</strong> ${escapeHtml(job.error || '—')}</div>
      </div>
      <div class="progress-wrap">
        <div class="progress-bar"><span style="width:${progressWidth(job)}"></span></div>
        <div class="progress-label">${Number(job.progress || 0)}%</div>
      </div>
      <details class="logs-box" data-job-id="${escapeHtml(job.id)}">
        <summary>Logs</summary>
        <pre>${escapeHtml((job.logs || []).join('\n'))}</pre>
      </details>
    `;
    container.appendChild(card);
    const details = card.querySelector('.logs-box');
    if (openLogs.has(job.id)) details.open = true;
    details.addEventListener('toggle', (e) => {
      if (e.currentTarget.open) openLogs.add(job.id);
      else openLogs.delete(job.id);
    });
    const abortBtn = card.querySelector('.abort-job-btn');
    if (abortBtn) {
      abortBtn.addEventListener('click', async () => {
        abortBtn.disabled = true;
        const res  = await fetch(`/api/jobs/${job.id}/abort`, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!data.ok) alert(data.error || 'Failed to abort job');
        fetchJobs();
      });
    }
  });
}

// ── Fetch helpers ──────────────────────────────────────────────────────────────

async function fetchJobs() {
  const res  = await fetch('/api/jobs', { cache: 'no-store' });
  const data = await res.json();
  renderJobs(data.jobs || []);
}

async function fetchLibrarySongs() {
  const res  = await fetch('/api/library-songs', { cache: 'no-store' });
  const data = await res.json();
  librarySongs = data.songs || [];
  renderLibrarySongOptions(librarySongs, el('selected_file')?.value || '');
}

// ── Form submissions ───────────────────────────────────────────────────────────

async function submitDownload(event) {
  event.preventDefault();
  const payload = buildPayload('');
  if (!payload.youtube_url && (!payload.song_name || !payload.artist_names)) {
    return alert('Provide either a YouTube link or at least song name + artist names.');
  }
  const res  = await fetch('/api/download', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const data = await res.json();
  if (!data.ok) return alert(data.error || 'Failed to queue download');
  clearInputs(['song_name','artist_names','album_name','youtube_url','album_art_url','rename_to']);
  fetchJobs();
}

async function submitRetag(event) {
  event.preventDefault();
  const payload = buildPayload('retag_');
  if (!payload.selected_file) return alert('Select a downloaded song to retag.');
  if (!payload.youtube_url && (!payload.song_name || !payload.artist_names)) {
    return alert('Provide either a YouTube link or at least song name + artist names.');
  }
  const res  = await fetch('/api/retag', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const data = await res.json();
  if (!data.ok) return alert(data.error || 'Failed to queue retag job');
  storeCurrentRetagState();
  fetchLibrarySongs();
  fetchJobs();
}

// -- Batch download (paste) --
async function submitBatchDownload(event) {
  event.preventDefault();

  const delaySeconds = parseInt(el('batch_delay')?.value || '10', 10);
  const file = el('batch_json_file')?.files?.[0];

  // File upload takes priority over pasted JSON
  if (file) {
    const form = new FormData();
    form.append('file', file);
    form.append('delay_seconds', String(delaySeconds));
    const res  = await fetch('/api/download-batch-file', { method: 'POST', body: form });
    const data = await res.json();
    if (!data.ok) return alert(data.error || 'Failed to queue batch from file');
    alert(`Queued ${data.job_ids?.length || 0} songs (delay: ${data.delay_seconds}s between each). Check the Jobs panel.`);
    el('batch_json_file').value = '';
    el('batch_file_name').textContent = 'No file chosen';
    fetchJobs();
    return;
  }

  // Pasted JSON
  const raw = el('batch_json').value.trim();
  if (!raw) return alert('Paste a JSON payload or upload a .json file.');
  let payload;
  try { payload = JSON.parse(raw); } catch { return alert('Invalid JSON — check the format and try again.'); }

  const body = { songs: payload, delay_seconds: delaySeconds };
  const res  = await fetch('/api/download-batch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json();
  if (!data.ok) return alert(data.error || 'Failed to queue multi song download');
  alert(`Queued ${data.job_ids?.length || 0} songs (delay: ${data.delay_seconds}s between each). Check the Jobs panel.`);
  clearInputs(['batch_json']);
  fetchJobs();
}

// -- Retag from JSON --
async function submitRetagFromJson(event) {
  event.preventDefault();

  const delaySeconds = parseInt(el('retag_json_delay')?.value || '10', 10);
  const file = el('retag_json_file')?.files?.[0];

  if (file) {
    const form = new FormData();
    form.append('file', file);
    form.append('delay_seconds', String(delaySeconds));
    const res  = await fetch('/api/retag-from-json-file', { method: 'POST', body: form });
    const data = await res.json();
    if (!data.ok) return alert(data.error || 'Failed to queue retag from file');
    alert(`Retag from JSON queued (job: ${data.job_id?.slice(0, 8)}). Check the Jobs panel.`);
    el('retag_json_file').value = '';
    el('retag_file_name').textContent = 'No file chosen';
    fetchJobs();
    return;
  }

  const raw = el('retag_json').value.trim();
  if (!raw) return alert('Paste a JSON payload or upload a .json file.');
  let payload;
  try { payload = JSON.parse(raw); } catch { return alert('Invalid JSON — check the format and try again.'); }

  const body = { songs: payload, delay_seconds: delaySeconds };
  const res  = await fetch('/api/retag-from-json', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json();
  if (!data.ok) return alert(data.error || 'Failed to queue retag from JSON');
  alert(`Retag from JSON queued (job: ${data.job_id?.slice(0, 8)}). Check the Jobs panel.`);
  clearInputs(['retag_json']);
  fetchJobs();
}

// -- Misc --
async function submitRetagAll() {
  const res  = await fetch('/api/retag-all', { method: 'POST' });
  const data = await res.json();
  if (!data.ok) return alert(data.error || 'Failed to queue retag all');
  fetchJobs();
}

async function clearJobs() {
  openLogs.clear();
  await fetch('/api/jobs/clear', { method: 'POST' });
  fetchJobs();
}

async function abortAllJobs() {
  const res  = await fetch('/api/jobs/abort-all', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) return alert(data.error || 'Failed to abort all jobs');
  fetchJobs();
}

async function refreshJobsAndClearInputs() {
  clearInputs([
    'song_name','artist_names','album_name','youtube_url','album_art_url','rename_to',
    'batch_json','retag_json',
    'retag_song_name','retag_artist_names','retag_album_name','retag_youtube_url',
    'retag_album_art_url','selected_file','library_song_search',
  ]);
  await clearJobs();
  await fetchJobs();
}

// ── Boot ───────────────────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded', () => {

  // Generic clear buttons (data-target)
  document.querySelectorAll('.clear-btn[data-target]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = el(btn.dataset.target);
      if (!target) return;
      target.value = '';
      if (target.id === 'library_song_search') renderLibrarySongOptions(librarySongs, '');
      if (target.id === 'selected_file') applyRetagStateForSelected();
      if (target.id.startsWith('retag_') && target.id !== 'retag_json' && target.id !== 'retag_json_delay') storeCurrentRetagState();
    });
  });

  // Retag field memory
  ['retag_song_name','retag_artist_names','retag_album_name','retag_youtube_url','retag_album_art_url']
    .forEach((id) => { const n = el(id); if (n) n.addEventListener('input', storeCurrentRetagState); });

  // File input display names
  el('batch_json_file')?.addEventListener('change', () => {
    el('batch_file_name').textContent = el('batch_json_file').files?.[0]?.name || 'No file chosen';
  });
  el('retag_json_file')?.addEventListener('change', () => {
    el('retag_file_name').textContent = el('retag_json_file').files?.[0]?.name || 'No file chosen';
  });

  // Clear file inputs
  el('clearBatchFileBtn')?.addEventListener('click', () => {
    el('batch_json_file').value = '';
    el('batch_file_name').textContent = 'No file chosen';
  });
  el('clearRetagFileBtn')?.addEventListener('click', () => {
    el('retag_json_file').value = '';
    el('retag_file_name').textContent = 'No file chosen';
  });

  // Form submissions
  el('downloadForm').addEventListener('submit', submitDownload);
  el('retagForm').addEventListener('submit', submitRetag);
  el('batchDownloadForm').addEventListener('submit', submitBatchDownload);
  el('retagFromJsonForm').addEventListener('submit', submitRetagFromJson);

  // Buttons
  el('refreshJobsBtn').addEventListener('click', refreshJobsAndClearInputs);
  el('clearJobsBtn').addEventListener('click', clearJobs);
  el('abortAllJobsBtn')?.addEventListener('click', abortAllJobs);
  el('refreshLibraryBtn').addEventListener('click', fetchLibrarySongs);
  el('retagAllBtn')?.addEventListener('click', submitRetagAll);

  // Song select
  el('selected_file').addEventListener('change', applyRetagStateForSelected);
  el('library_song_search').addEventListener('input', () => {
    renderLibrarySongOptions(librarySongs, el('selected_file')?.value || '');
  });

  // Initial load
  fetchHealth();
  fetchJobs();
  fetchLibrarySongs();
  setInterval(fetchJobs, 1500);
});
