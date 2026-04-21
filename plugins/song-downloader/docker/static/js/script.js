const el = (id) => document.getElementById(id);
const openLogs = new Set();
let librarySongs = [];

async function fetchHealth() {
  const res = await fetch('/api/health', { cache: 'no-store' });
  const data = await res.json();
  el('healthStatus').textContent = `OK • v${data.version}`;
}

function buildPayload(prefix = '') {
  return {
    song_name: el(`${prefix}song_name`).value.trim(),
    artist_names: el(`${prefix}artist_names`).value.trim(),
    album_name: el(`${prefix}album_name`).value.trim(),
    youtube_url: el(`${prefix}youtube_url`).value.trim(),
    rename_to: prefix ? '' : el('rename_to').value.trim(),
    auto_move: prefix ? true : el('auto_move').checked,
    selected_file: prefix ? el('selected_file').value : '',
    album_art_url: prefix ? (el('retag_album_art_url')?.value.trim() || '') : (el('album_art_url')?.value.trim() || ''),
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


function parseSongFilename(filePath) {
  const rawName = (filePath || '').split('/').pop() || '';
  const base = rawName.replace(/\.mp3$/i, '').trim();
  if (!base) {
    return { song_name: '', album_name: '', artist_names: '' };
  }

  const normalized = base
    .replace(/[–—]/g, '-')
    .replace(/[，]/g, ',')
    .replace(/\s+-\s+/g, ' - ')
    .trim();

  const parts = normalized.split(' - ').map((part) => part.trim()).filter(Boolean);
  if (parts.length >= 3) {
    return {
      song_name: parts[0],
      album_name: parts.slice(1, -1).join(' - '),
      artist_names: parts[parts.length - 1],
    };
  }
  if (parts.length === 2) {
    return {
      song_name: parts[0],
      album_name: '',
      artist_names: parts[1],
    };
  }
  return { song_name: normalized, album_name: '', artist_names: '' };
}

function autofillRetagFieldsFromSelection() {
  const selected = el('selected_file').value;
  if (!selected) {
    if (el('retag_song_name')) el('retag_song_name').value = '';
    if (el('retag_album_name')) el('retag_album_name').value = '';
    if (el('retag_artist_names')) el('retag_artist_names').value = '';
    return;
  }
  const parsed = parseSongFilename(selected);

  if (el('retag_song_name')) {
    el('retag_song_name').value = parsed.song_name || '';
  }
  if (el('retag_album_name')) {
    el('retag_album_name').value = parsed.album_name || '';
  }
  if (el('retag_artist_names')) {
    el('retag_artist_names').value = parsed.artist_names || '';
  }
}

function renderLibrarySongOptions(songs, preferredValue = '') {
  const select = el('selected_file');
  const search = (el('library_song_search')?.value || '').trim().toLowerCase();
  const filtered = songs.filter((song) => !search || song.path.toLowerCase().includes(search) || (song.name || '').toLowerCase().includes(search));

  select.innerHTML = '<option value=>Select a song from /mnt/nas/media/music</option>';
  filtered.forEach((song) => {
    const option = document.createElement('option');
    option.value = song.path;
    option.textContent = song.name || song.path;
    select.appendChild(option);
  });

  const desiredValue = preferredValue || select.value;
  if (desiredValue && Array.from(select.options).some((option) => option.value === desiredValue)) {
    select.value = desiredValue;
  } else if (filtered.length === 1) {
    select.value = filtered[0].path;
  } else {
    select.value = '';
  }

  autofillRetagFieldsFromSelection();
}

function progressWidth(job) {
  const value = Number(job.progress || 0);
  return `${Math.max(0, Math.min(100, value))}%`;
}

function rememberOpenLogs() {
  document.querySelectorAll('.logs-box[data-job-id]').forEach((node) => {
    const id = node.dataset.jobId;
    if (node.open) openLogs.add(id);
    else openLogs.delete(id);
  });
}

function renderJobs(jobs) {
  rememberOpenLogs();
  const container = el('jobsContainer');
  container.innerHTML = '';
  if (!jobs.length) {
    container.innerHTML = '<div class="empty-state">No jobs yet.</div>';
    return;
  }

  jobs.forEach((job) => {
    const song = job.payload?.song_name || job.payload?.selected_file || '—';
    const artists = job.payload?.artist_names || '—';
    const album = job.payload?.album_name || 'Unknown';
    const youtube = job.payload?.youtube_url || 'Search mode';
    const jobType = job.payload?.job_type || 'download';
    const card = document.createElement('article');
    card.className = 'job-card';
    card.innerHTML = `
      <div class="job-top">
        <div>
          <div class="job-status ${job.status}">${job.status}</div>
          <div class="job-time">${job.updated_at || job.created_at}</div>
        </div>
        <div class="job-actions-top">
          <div class="job-id">${job.id.slice(0, 8)}</div>
          ${(job.status === 'running' || job.status === 'queued') ? `<button type="button" class="ghost-btn danger abort-job-btn" data-job-id="${job.id}">Abort job</button>` : ''}
        </div>
      </div>
      <div class="job-main">
        <div><strong>Type:</strong> ${jobType}</div>
        <div><strong>Song:</strong> ${song}</div>
        <div><strong>Artists:</strong> ${artists}</div>
        <div><strong>Album:</strong> ${album}</div>
        <div><strong>YouTube:</strong> ${youtube}</div>
        <div><strong>Final file:</strong> ${job.final_file || '—'}</div>
        <div><strong>Error:</strong> ${job.error || '—'}</div>
      </div>
      <div class="progress-wrap">
        <div class="progress-bar"><span style="width:${progressWidth(job)}"></span></div>
        <div class="progress-label">${Number(job.progress || 0)}%</div>
      </div>
      <details class="logs-box" data-job-id="${job.id}">
        <summary>Logs</summary>
        <pre>${(job.logs || []).join('\n')}</pre>
      </details>
    `;
    container.appendChild(card);
    const details = card.querySelector('.logs-box');
    if (openLogs.has(job.id)) details.open = true;
    details.addEventListener('toggle', (event) => {
      if (event.currentTarget.open) openLogs.add(job.id);
      else openLogs.delete(job.id);
    });

    const abortBtn = card.querySelector('.abort-job-btn');
    if (abortBtn) {
      abortBtn.addEventListener('click', async () => {
        abortBtn.disabled = true;
        const res = await fetch(`/api/jobs/${job.id}/abort`, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!data.ok) alert(data.error || 'Failed to abort job');
        fetchJobs();
      });
    }
  });
}

async function fetchJobs() {
  const res = await fetch('/api/jobs', { cache: 'no-store' });
  const data = await res.json();
  renderJobs(data.jobs || []);
}

async function fetchLibrarySongs() {
  const res = await fetch('/api/library-songs', { cache: 'no-store' });
  const data = await res.json();
  librarySongs = data.songs || [];
  renderLibrarySongOptions(librarySongs, el('selected_file')?.value || '');
}

async function submitDownload(event) {
  event.preventDefault();
  const payload = buildPayload('');

  if (!payload.youtube_url && (!payload.song_name || !payload.artist_names)) {
    alert('Provide either a YouTube link or at least song name + artist names.');
    return;
  }

  const res = await fetch('/api/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!data.ok) {
    alert('Failed to queue download');
    return;
  }
  clearInputs(['song_name','artist_names','album_name','youtube_url','album_art_url','rename_to']);
  fetchJobs();
}

async function submitRetag(event) {
  event.preventDefault();
  const payload = buildPayload('retag_');
  if (!payload.selected_file) {
    alert('Select a downloaded song to retag.');
    return;
  }
  if (!payload.youtube_url && (!payload.song_name || !payload.artist_names)) {
    alert('Provide either a YouTube link or at least song name + artist names for metadata lookup.');
    return;
  }

  const res = await fetch('/api/retag', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!data.ok) {
    alert('Failed to queue retag job');
    return;
  }
  clearInputs(['selected_file','library_song_search','retag_song_name','retag_artist_names','retag_album_name','retag_youtube_url','retag_album_art_url']);
  fetchLibrarySongs();
  fetchJobs();
}

async function clearJobs() {
  openLogs.clear();
  await fetch('/api/jobs/clear', { method: 'POST' });
  fetchJobs();
}

async function abortAllJobs() {
  const res = await fetch('/api/jobs/abort-all', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) return alert(data.error || 'Failed to abort all jobs');
  fetchJobs();
}

async function refreshJobsAndClearInputs() {
  clearInputs([
    'song_name', 'artist_names', 'album_name', 'youtube_url', 'album_art_url', 'rename_to', 'batch_json',
    'retag_song_name', 'retag_artist_names', 'retag_album_name', 'retag_youtube_url', 'retag_album_art_url', 'selected_file', 'library_song_search',
  ]);
  await clearJobs();
  await fetchJobs();
}

window.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.clear-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = el(btn.dataset.target);
      if (target) { target.value = ''; if (target.id === 'library_song_search') renderLibrarySongOptions(librarySongs, ''); if (target.id === 'selected_file') autofillRetagFieldsFromSelection(); }
    });
  });

  el('downloadForm').addEventListener('submit', submitDownload);
  el('retagForm').addEventListener('submit', submitRetag);
  if (el('batchDownloadForm')) el('batchDownloadForm').addEventListener('submit', (e) => { e.preventDefault(); submitBatchDownload(); });
  el('refreshJobsBtn').addEventListener('click', refreshJobsAndClearInputs);
  el('clearJobsBtn').addEventListener('click', clearJobs);
  if (el('abortAllJobsBtn')) el('abortAllJobsBtn').addEventListener('click', abortAllJobs);
  el('refreshLibraryBtn').addEventListener('click', fetchLibrarySongs);
  el('selected_file').addEventListener('change', autofillRetagFieldsFromSelection);
  el('library_song_search').addEventListener('input', () => { renderLibrarySongOptions(librarySongs, ''); });
  if (el('retagAllBtn')) el('retagAllBtn').addEventListener('click', submitRetagAll);

  fetchHealth();
  fetchJobs();
  fetchLibrarySongs();
  setInterval(fetchJobs, 1500);
});


async function submitBatchDownload() {
  const raw = el('batch_json').value.trim();
  if (!raw) return alert('Paste JSON payload first.');
  let payload;
  try { payload = JSON.parse(raw); } catch (err) { return alert('Invalid JSON payload'); }
  const res = await fetch('/api/download-batch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const data = await res.json();
  if (!data.ok) return alert(data.error || 'Failed to queue multi song download');
  clearInputs(['batch_json']);
  fetchJobs();
}

async function submitRetagAll() {
  const res = await fetch('/api/retag-all', { method: 'POST' });
  const data = await res.json();
  if (!data.ok) return alert(data.error || 'Failed to queue retag all');
  fetchJobs();
}
