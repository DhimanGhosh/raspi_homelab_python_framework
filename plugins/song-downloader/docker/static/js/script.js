const el = (id) => document.getElementById(id);

async function fetchHealth() {
  const res = await fetch('/api/health');
  const data = await res.json();
  el('healthStatus').textContent = `${data.status.toUpperCase()} • v${data.version}`;
}

function buildPayload() {
  return {
    song_name: el('song_name').value.trim(),
    artist_names: el('artist_names').value.trim(),
    album_name: el('album_name').value.trim(),
    youtube_url: el('youtube_url').value.trim(),
    rename_to: el('rename_to').value.trim(),
    auto_move: el('auto_move').checked,
  };
}

function renderJobs(jobs) {
  const container = el('jobsContainer');
  container.innerHTML = '';
  if (!jobs.length) {
    container.innerHTML = '<div class="empty-state">No jobs yet.</div>';
    return;
  }

  jobs.forEach((job) => {
    const card = document.createElement('article');
    card.className = 'job-card';
    card.innerHTML = `
      <div class="job-top">
        <div>
          <div class="job-status ${job.status}">${job.status}</div>
          <div class="job-time">${job.updated_at || job.created_at}</div>
        </div>
        <div class="job-id">${job.id.slice(0, 8)}</div>
      </div>
      <div class="job-main">
        <div><strong>Song:</strong> ${job.payload.song_name || '—'}</div>
        <div><strong>Artists:</strong> ${job.payload.artist_names || '—'}</div>
        <div><strong>Album:</strong> ${job.payload.album_name || 'Unknown'}</div>
        <div><strong>YouTube:</strong> ${job.payload.youtube_url || 'Search mode'}</div>
        <div><strong>Final file:</strong> ${job.final_file || '—'}</div>
        <div><strong>Error:</strong> ${job.error || '—'}</div>
      </div>
      <details class="logs-box">
        <summary>Logs</summary>
        <pre>${(job.logs || []).join('\n')}</pre>
      </details>
    `;
    container.appendChild(card);
  });
}

async function fetchJobs() {
  const res = await fetch('/api/jobs');
  const data = await res.json();
  renderJobs(data.jobs || []);
}

async function submitDownload(event) {
  event.preventDefault();
  const payload = buildPayload();

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
  fetchJobs();
}

async function clearJobs() {
  await fetch('/api/jobs/clear', { method: 'POST' });
  fetchJobs();
}

window.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.clear-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = el(btn.dataset.target);
      if (target) target.value = '';
    });
  });

  el('downloadForm').addEventListener('submit', submitDownload);
  el('refreshJobsBtn').addEventListener('click', fetchJobs);
  el('clearJobsBtn').addEventListener('click', clearJobs);

  fetchHealth();
  fetchJobs();
  setInterval(fetchJobs, 5000);
});
