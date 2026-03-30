
let state = window.__INITIAL_STATE__ || { apps: [], notifications: [], jobs: [], total_bundles: 0, backups: [], notification_total: 0, backup_root: '' };
const pollMs = 2000;
let openBundleId = null;
let otaWatchTimer = null;
let selectedLogName = null;
let selectedLogAutoScroll = true;
let miniLogState = {};
let notificationFilterAppId = null;
let menuOpen = false;

async function apiJson(url, options = {}) {
  const res = await fetch(url, Object.assign({ headers: { Accept: 'application/json' } }, options));
  const data = await res.json().catch(() => ({ ok: false, message: 'Unexpected response' }));
  return { res, data };
}
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m])); }
function pct(v) { return Math.max(0, Math.min(100, Number(v || 0))); }
function statusClass(status) { return ({ queued: 'queued', running: 'running', success: 'success', failed: 'failed', canceled: 'failed' })[status] || 'queued'; }
function fmtAction(a) { return a === 'update' ? 'Update' : a === 'reinstall' ? 'Reinstall' : 'Install'; }
function isUninstallJob(job) { return !!(job && (job.action === 'uninstall' || /uninstall|stopping|remov/i.test(String(job.message || '')))); }
function queueLabel(job) { if (!job) return 'Working'; if (isUninstallJob(job)) return job.status === 'queued' ? 'Queued uninstall' : 'Uninstalling'; return job.status === 'queued' ? 'Queued' : 'Installing'; }
function captureMiniLogState() {
  document.querySelectorAll('.mini-log[data-job-id]').forEach(el => {
    miniLogState[el.dataset.jobId] = {
      scrollTop: el.scrollTop,
      nearBottom: (el.scrollHeight - el.clientHeight - el.scrollTop) < 24,
    };
  });
}
function syncSelectedLogBox() {
  const box = document.getElementById('logBox');
  if (!box) return;
  selectedLogAutoScroll = (box.scrollHeight - box.clientHeight - box.scrollTop) < 24;
}
async function refreshSelectedLog() {
  if (!selectedLogName) return;
  const res = await fetch('/api/logs/' + encodeURIComponent(selectedLogName) + '?ts=' + Date.now(), { cache: 'no-store' });
  if (!res.ok) return;
  const txt = await res.text();
  const logCard = document.getElementById('logCard');
  const box = document.getElementById('logBox');
  logCard.style.display = 'block';
  const currentScrollTop = box.scrollTop;
  box.textContent = txt || 'No log available.';
  box.scrollTop = selectedLogAutoScroll ? box.scrollHeight : currentScrollTop;
}
function showLog(text, logName) {
  if (logName) selectedLogName = logName;
  document.getElementById('logCard').style.display = 'block';
  const box = document.getElementById('logBox');
  box.textContent = text || 'No log available.';
  box.scrollTop = box.scrollHeight;
  selectedLogAutoScroll = true;
  document.getElementById('logCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function renderJobs() {
  captureMiniLogState();
  const area = document.getElementById('jobsArea');
  const jobs = (state.jobs || []).slice().sort((a, b) => (['running', 'queued', 'failed', 'success'].indexOf(a.status) - ['running', 'queued', 'failed', 'success'].indexOf(b.status)) || String(b.updated_at).localeCompare(String(a.updated_at)));
  if (!jobs.length) { area.innerHTML = '<div class="muted">No install jobs yet.</div>'; return; }
  area.innerHTML = jobs.map(j => `
    <div class="job-card ${statusClass(j.status)}">
      <div class="row between"><div class="job-title-wrap"><strong>${esc(j.app_name || j.app_id)}</strong><span class="pill ${statusClass(j.status)}">${esc(j.status)}</span></div>${['queued', 'running'].includes(j.status) ? `<button class="secondary cancel-btn" data-cancel-job="${esc(j.id)}">Cancel</button>` : `<button class="secondary cancel-btn" data-dismiss-job="${esc(j.id)}">Dismiss</button>`}</div>
      <div class="muted small">${esc(j.action)} • ${esc(j.bundle_filename || 'manual')}</div>
      <div class="progress"><div class="bar ${statusClass(j.status)}" style="width:${pct(j.progress)}%"></div></div>
      <div class="muted">${esc(j.message || '')}</div>
      <div class="job-actions">${j.log_name ? `<button class="secondary" data-show-log="${esc(j.log_name)}">View log</button>` : ''}</div>
      <pre class="mini-log" data-job-id="${esc(j.id)}">${esc(j.log_tail || '')}</pre>
    </div>`).join('');
  document.querySelectorAll('.mini-log[data-job-id]').forEach(el => {
    const prev = miniLogState[el.dataset.jobId];
    if (!prev) return;
    if (prev.nearBottom) { el.scrollTop = el.scrollHeight; } else { el.scrollTop = prev.scrollTop; }
  });
}
function appCard(app) {
  const bundles = (app.bundles || []).map(b => `<div class="bundle-row"><div><div>${esc(b.filename)}</div><div class="muted">v${esc(b.version)}</div></div><div class="bundle-actions"><button class="secondary" data-install-bundle="${esc(app.id)}" data-bundle-filename="${esc(b.filename)}">Install</button><button class="danger" data-delete-bundle="${esc(b.filename)}">Delete</button></div></div>`).join('') || '<div class="muted">No uploaded bundles.</div>';
  const job = app.job;
  const open = app.open_url ? `<a class="btn secondary" target="_blank" href="${esc(app.open_url)}">Open</a>` : '';
  let action = '';
  if (job) { action = `<button disabled>${esc(queueLabel(job))}</button>`; }
  else if (['install', 'update', 'reinstall'].includes(app.action)) { action = `<button data-install="${esc(app.id)}">${fmtAction(app.action)}</button>`; }
  const uninstall = (app.id !== 'control-center' && app.installed && !job) ? `<button data-uninstall="${esc(app.id)}">Uninstall</button>` : '';
  const progress = job ? `<div class="progress compact"><div class="bar ${statusClass(job.status)}" style="width:${pct(job.progress)}%"></div></div><div class="muted small">${esc(job.message || '')}</div>` : '';
  const noteBadge = app.notification_count ? `<button class="app-notif-badge app-note-trigger" title="Show notifications" data-show-app-notifications="${esc(app.id)}">${app.notification_count}</button>` : '';
  return `<div class="app-card"><div class="app-card-top"><div><h3>${esc(app.name || app.id)} <span class="title-version">v${esc(app.installed_version || app.latest_version || '—')}</span></h3><div class="muted">ID: ${esc(app.id)}</div><div>Installed: ${esc(app.installed_version || '—')}</div><div>Latest: ${esc(app.latest_version || '—')}</div><div>Port: ${esc(app.port || '—')}</div>${app.bundle_filename ? `<div class="muted">${esc(app.bundle_filename)}</div>` : ''}</div>${noteBadge}</div>${progress}<div class="actions">${open}${action}${uninstall}</div><details class="bundles-block" data-app-id="${esc(app.id)}"><summary>Bundles (${(app.bundles || []).length})</summary>${bundles}</details></div>`;
}

function renderApps() {
  document.getElementById('appsArea').innerHTML = (state.apps || []).map(appCard).join('');
  document.querySelectorAll('#appsArea details.bundles-block').forEach(el => { el.open = (openBundleId && el.dataset.appId === openBundleId); });
  document.getElementById('bundleCount').textContent = state.total_bundles || 0;
}
function getFilteredNotifications() {
  const notes = state.notifications || [];
  if (!notificationFilterAppId) return notes;
  return notes.filter(n => (n.app_id || '') === notificationFilterAppId);
}
function toggleMenu(force) {
  const menu = document.getElementById('menuDrawer');
  const drawer = document.getElementById('notificationDrawer');
  const backdrop = document.getElementById('drawerBackdrop');
  const show = typeof force === 'boolean' ? force : menu.classList.contains('hidden');
  menu.classList.toggle('hidden', !show);
  if (show && drawer) drawer.classList.add('hidden');
  backdrop.classList.toggle('hidden', !show);
  menuOpen = show;
}

function renderNotifications() {
  const area = document.getElementById('notificationsArea');
  const allNotes = state.notifications || [];
  const notes = getFilteredNotifications();
  area.innerHTML = notes.length ? notes.map(n => `<div class="notice notice-banner"><div><strong>${esc(n.app_id || 'system')}</strong></div><div>${esc(n.ts)} — ${esc(n.message)}</div></div>`).join('') : '<div class="muted">No notifications.</div>';
  const count = Number(state.notification_total || allNotes.length || 0);
  const badge = document.getElementById('topNotificationCount');
  badge.textContent = count;
  badge.style.display = count ? 'inline-flex' : 'none';
  const title = document.getElementById('notificationTitle');
  const subtitle = document.getElementById('notificationSubtitle');
  if (notificationFilterAppId) {
    const app = (state.apps || []).find(a => a.id === notificationFilterAppId);
    title.textContent = (app?.name || notificationFilterAppId) + ' notifications';
    subtitle.textContent = `${notes.length} shown`;
  } else {
    title.textContent = 'Notifications';
    subtitle.textContent = `${count} total`;
  }
}
function renderBackups() {
  const area = document.getElementById('backupsArea');
  if (!area) return;
  const items = state.backups || [];
  if (!items.length) { area.innerHTML = '<div class="muted">No homelab snapshots yet.</div>'; return; }
  area.innerHTML = items.map(b => `<div class="backup-row"><div><div><strong>${esc(b.filename)}</strong></div><div class="muted small">${esc(b.created_at)} • ${esc(b.size_mb)} MB</div><div class="muted small">${esc(b.path || state.backup_root || '')}</div></div><div class="bundle-actions"><button class="secondary" data-restore-backup="${esc(b.filename)}">Rollback</button><button class="danger" data-delete-backup="${esc(b.filename)}">Delete</button></div></div>`).join('');
  const root = document.getElementById('backupRoot');
  if (root) root.textContent = state.backup_root || '';
}
async function refreshState() {
  const { res, data } = await apiJson('/api/state');
  if (res.ok) { state = Object.assign({}, state, data); renderJobs(); renderApps(); renderNotifications(); renderBackups(); await refreshSelectedLog(); }
}
function setQueueMessage(msg) { const el = document.getElementById('queueMsg'); if (el) el.textContent = msg || ''; }
async function queueAction(url, options = {}, ui = {}) {
  const { res, data } = await apiJson(url, options);
  const uploadMsg = document.getElementById('uploadMsg');
  if (uploadMsg && ui.clearUploadMsg !== false && !ui.useUploadMsg) { uploadMsg.textContent = ''; }
  if (uploadMsg && ui.useUploadMsg) { uploadMsg.textContent = data.message || ''; }
  if (ui.useQueueMsg) { setQueueMessage(data.message || ''); }
  await refreshState();
  return { res, data };
}
async function watchForOtaRestart(previousVersion) {
  if (otaWatchTimer) { clearInterval(otaWatchTimer); otaWatchTimer = null; }
  let attempts = 0;
  let sawFailure = false;
  otaWatchTimer = setInterval(async () => {
    attempts += 1;
    try {
      const res = await fetch('/api/health', { cache: 'no-store' });
      if (!res.ok) throw new Error('health not ok');
      const data = await res.json();
      const newVersion = String(data.version || '');
      if (sawFailure || (previousVersion && newVersion && newVersion !== previousVersion)) {
        clearInterval(otaWatchTimer);
        otaWatchTimer = null;
        window.location.reload();
        return;
      }
    } catch (e) {
      sawFailure = true;
    }
    if (attempts >= 90) { clearInterval(otaWatchTimer); otaWatchTimer = null; }
  }, 2000);
}
function toggleNotifications(show, appId = null) {
  notificationFilterAppId = appId;
  renderNotifications();
  document.getElementById('notificationDrawer').classList.toggle('hidden', !show);
  document.getElementById('drawerBackdrop').classList.toggle('hidden', !show);
}

document.addEventListener('click', async (e) => {
  const t = e.target.closest('button'); if (!t) return;
  if (t.id === 'hideLogBtn') { e.preventDefault(); document.getElementById('logCard').style.display = 'none'; selectedLogName = null; return; }
  if (t.id === 'notificationsToggle') { e.preventDefault(); toggleNotifications(document.getElementById('notificationDrawer').classList.contains('hidden')); return; }
  if (t.id === 'clearNotificationsBtn') { e.preventDefault(); await queueAction('/api/notifications/clear', { method: 'POST' }, { silent: true }); toggleNotifications(false); return; }
  if (t.dataset.showAppNotifications) { e.preventDefault(); toggleNotifications(true, t.dataset.showAppNotifications); return; }
  if (t.id === 'updateAllBtn') { e.preventDefault(); if (!confirm('Queue all available updates now?')) return; const prev = state.current_version; const out = await queueAction('/api/update-all', { method: 'POST' }, { useQueueMsg: true }); if (out.res.ok && /Control Center OTA started/.test(out.data.message || '')) { watchForOtaRestart(prev); } return; }
  if (t.id === 'installAllBtn') { e.preventDefault(); if (!confirm('Queue install/update for all available bundles now?')) return; const prev = state.current_version; const out = await queueAction('/api/install-all', { method: 'POST' }, { useQueueMsg: true }); if (out.res.ok && /Control Center OTA started/.test(out.data.message || '')) { watchForOtaRestart(prev); } return; }
  if (t.id === 'createBackupBtn') { e.preventDefault(); await queueAction('/api/backups/create', { method: 'POST' }, { silent: true }); return; }
  if (t.dataset.showLog) { e.preventDefault(); const txt = await fetch('/api/logs/' + encodeURIComponent(t.dataset.showLog) + '?ts=' + Date.now(), { cache: 'no-store' }).then(r => r.text()); showLog(txt, t.dataset.showLog); return; }
  if (t.dataset.action === 'rescan') { e.preventDefault(); await queueAction('/api/marketplace/rescan', { method: 'POST' }, { silent: true }); return; }
  if (t.dataset.action === 'clear-notifications') { e.preventDefault(); await queueAction('/api/notifications/clear', { method: 'POST' }, { silent: true }); return; }
  if (t.id === 'clearCompletedBtn' || t.dataset.action === 'clear-completed-jobs') { e.preventDefault(); await queueAction('/api/jobs/clear-completed', { method: 'POST' }, { silent: true }); return; }
  if (t.id === 'clearAllBtn') { e.preventDefault(); if (!confirm('Clear all install queue jobs?')) return; await queueAction('/api/jobs/clear-all', { method: 'POST' }, { silent: true }); return; }
  if (t.dataset.cancelJob) { e.preventDefault(); await queueAction('/api/jobs/' + encodeURIComponent(t.dataset.cancelJob) + '/cancel', { method: 'POST' }, { silent: true }); return; }
  if (t.dataset.dismissJob) { e.preventDefault(); await queueAction('/api/jobs/' + encodeURIComponent(t.dataset.dismissJob) + '/dismiss', { method: 'POST' }, { silent: true }); return; }
  if (t.dataset.action === 'ota') { e.preventDefault(); const prev = state.current_version; const out = await queueAction('/api/ota/apply', { method: 'POST' }, { silent: true }); if (out.res.ok) { watchForOtaRestart(prev); } return; }
  if (t.dataset.install) { e.preventDefault(); await queueAction('/api/apps/' + encodeURIComponent(t.dataset.install) + '/install', { method: 'POST' }, { silent: true }); return; }
  if (t.dataset.installBundle) { e.preventDefault(); const appId = t.dataset.installBundle; const filename = t.dataset.bundleFilename; await queueAction('/api/apps/' + encodeURIComponent(appId) + '/install-bundle/' + encodeURIComponent(filename), { method: 'POST' }, { silent: true }); return; }
  if (t.dataset.uninstall) { e.preventDefault(); await queueAction('/api/apps/' + encodeURIComponent(t.dataset.uninstall) + '/uninstall', { method: 'POST' }, { silent: true }); return; }
  if (t.dataset.deleteBundle) { e.preventDefault(); if (!confirm('Delete bundle ' + t.dataset.deleteBundle + '?')) return; const msg = document.getElementById('uploadMsg'); if (msg) msg.textContent = ''; const { res } = await apiJson('/api/bundles/' + encodeURIComponent(t.dataset.deleteBundle), { method: 'DELETE' }); if (res.ok) { await refreshState(); } return; }
  if (t.dataset.restoreBackup) { e.preventDefault(); if (!confirm('Rollback entire homelab from ' + t.dataset.restoreBackup + '?')) return; await queueAction('/api/backups/' + encodeURIComponent(t.dataset.restoreBackup) + '/restore', { method: 'POST' }, { silent: true }); watchForOtaRestart(state.current_version); return; }
  if (t.dataset.deleteBackup) { e.preventDefault(); if (!confirm('Delete snapshot ' + t.dataset.deleteBackup + '?')) return; await queueAction('/api/backups/' + encodeURIComponent(t.dataset.deleteBackup), { method: 'DELETE' }, { silent: true }); return; }
});

document.getElementById('drawerBackdrop')?.addEventListener('click', () => toggleNotifications(false));
document.getElementById('logBox')?.addEventListener('scroll', syncSelectedLogBox);

document.addEventListener('submit', async (e) => {
  if (e.target.id === 'uploadForm') {
    e.preventDefault();
    const fd = new FormData(e.target);
    const { res, data } = await apiJson('/api/bundles/upload', { method: 'POST', body: fd });
    document.getElementById('uploadMsg').textContent = data.message || '';
    if (res.ok) {
      const form = e.target;
      const oldInput = document.getElementById('bundleInput');
      if (oldInput) {
        const newInput = oldInput.cloneNode();
        newInput.value = '';
        oldInput.replaceWith(newInput);
      }
      form.reset();
      const dz = document.getElementById('dropZone'); if (dz) dz.classList.remove('has-files');
      await refreshState();
    }
  }
});

document.addEventListener('click', (e) => {
  const summary = e.target.closest('#appsArea details.bundles-block > summary');
  if (!summary) return;
  e.preventDefault();
  const details = summary.parentElement;
  const appId = details?.dataset?.appId;
  if (!appId) return;
  openBundleId = (openBundleId === appId) ? null : appId;
  document.querySelectorAll('#appsArea details.bundles-block').forEach(el => { el.open = (openBundleId && el.dataset.appId === openBundleId); });
});
renderJobs(); renderApps(); renderNotifications(); renderBackups();
setInterval(refreshState, pollMs);


document.getElementById('menuToggle')?.addEventListener('click', () => toggleMenu());
document.getElementById('menuNotificationsBtn')?.addEventListener('click', () => toggleNotifications(true));
document.getElementById('menuRescanBtn')?.addEventListener('click', () => queueAction('/api/marketplace/rescan', { method: 'POST' }));
document.getElementById('dropZone')?.addEventListener('dragover', e => { e.preventDefault(); e.currentTarget.classList.add('dragover'); });
document.getElementById('dropZone')?.addEventListener('dragleave', e => { e.currentTarget.classList.remove('dragover'); });
document.getElementById('dropZone')?.addEventListener('drop', e => { e.preventDefault(); const input = document.getElementById('bundleInput'); if (!input) return; input.files = e.dataTransfer.files; e.currentTarget.classList.remove('dragover'); e.currentTarget.classList.toggle('has-files', input.files.length > 0); });
document.getElementById('bundleInput')?.addEventListener('change', e => { document.getElementById('dropZone')?.classList.toggle('has-files', !!e.target.files.length); });
document.getElementById('drawerBackdrop')?.addEventListener('click', () => { toggleNotifications(false); toggleMenu(false); });
