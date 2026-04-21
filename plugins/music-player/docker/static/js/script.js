let LIBRARY = null;
let VIEW = 'all';
let FILTER_VALUE = '';
let SELECTED = new Set();
let CURRENT_VIEW_TRACKS = [];
let PLAY_QUEUE = [];
let PLAY_INDEX = -1;
let SHUFFLE = false;
let REPEAT = 'off';
let PLAY_SOURCE = { type: 'All Songs', name: 'All Songs' };
let PAUSED_MANUALLY = false;
let IS_SEEKING = false;
let SEEK_STATE = { active: false, trackId: null };
let TRANSITION_STATE = { inProgress: false, armedTrackId: null, fadeInterval: null };
let MOBILE_QUEUE_EXPANDED = false;

const el = (id) => document.getElementById(id);
const audio = () => el('audioPlayer');

function fmtTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function trackMap() { return new Map((LIBRARY?.tracks || []).map((t) => [t.id, t])); }
function tracksByIds(ids) { const map = trackMap(); return ids.map((id) => map.get(id)).filter(Boolean); }
function allTracks() { return (LIBRARY?.tracks || []).slice(); }
function isMobile() { return window.innerWidth <= 900; }
function currentQueueTrackId() { return PLAY_QUEUE[PLAY_INDEX] || null; }
function currentTrack() { return trackMap().get(currentQueueTrackId()) || null; }
function shuffleArray(arr) { const copy = arr.slice(); for (let i = copy.length - 1; i > 0; i -= 1) { const j = Math.floor(Math.random() * (i + 1)); [copy[i], copy[j]] = [copy[j], copy[i]]; } return copy; }

function closeSidebar() { if (isMobile()) { el('sidebar').classList.remove('open'); el('mobileOverlay').classList.add('hidden'); } }
function openSidebar() { if (isMobile()) { el('sidebar').classList.add('open'); el('mobileOverlay').classList.remove('hidden'); } }
function hideTrackMenu() { el('trackMenu').classList.add('hidden'); el('trackMenu').innerHTML = ''; }
function showModal(id) { el(id).classList.remove('hidden'); }
function hideModal(id) { el(id).classList.add('hidden'); }
function applyMobileQueueState() { const panel = el('nowOverlayPanel'); if (!panel) return; panel.classList.toggle('mobile-queue-expanded', !!MOBILE_QUEUE_EXPANDED); const hint = document.querySelector('.overlay-queue-hint'); if (hint) hint.textContent = MOBILE_QUEUE_EXPANDED ? 'Swipe down to collapse queue' : 'Swipe up to expand queue'; }
function openNowOverlay() { MOBILE_QUEUE_EXPANDED = false; el('nowPlayingOverlay').classList.remove('hidden'); document.body.classList.add('overlay-open'); renderOverlayQueue(); applyMobileQueueState(); syncSeekUi(); }
function closeNowOverlay() { el('nowPlayingOverlay').classList.add('hidden'); document.body.classList.remove('overlay-open'); MOBILE_QUEUE_EXPANDED = false; applyMobileQueueState(); }

function filterTracks(items) {
  const q = FILTER_VALUE.trim().toLowerCase();
  if (!q) return items;
  return items.filter((track) => `${track.title} ${track.artist} ${track.album} ${track.folder} ${track.filename} ${track.year || ''}`.toLowerCase().includes(q));
}
function filterItems(items) {
  const q = FILTER_VALUE.trim().toLowerCase();
  if (!q) return items;
  return items.filter((item) => item.name.toLowerCase().includes(q));
}

function buildQueue(items, startTrackId = null, shuffle = false) {
  const ids = items.map((t) => t.id);
  if (!ids.length) { PLAY_QUEUE = []; PLAY_INDEX = -1; return; }
  let queue = ids.slice();
  let startId = startTrackId;
  if (shuffle) {
    if (startId) {
      const remaining = ids.filter((id) => id !== startId);
      queue = [startId, ...shuffleArray(remaining)];
    } else {
      queue = shuffleArray(ids);
      startId = queue[0];
    }
  } else if (!startId) {
    startId = ids[0];
  }
  PLAY_QUEUE = queue;
  PLAY_INDEX = Math.max(0, queue.indexOf(startId));
}

function sourceTextFor(track) {
  if (!track) return 'playing from —';
  if (!PLAY_SOURCE || !PLAY_SOURCE.type) return 'playing from —';
  if (PLAY_SOURCE.type === 'All Songs') return 'playing from All Songs';
  return `playing from ${PLAY_SOURCE.type.toLowerCase()} ${PLAY_SOURCE.name}`;
}

function sidebarContextText() {
  if (VIEW === 'all') return 'Viewing: All Songs';
  const [kind, ...rest] = VIEW.split(':');
  const name = rest.join(':');
  if (!name) return `Viewing: ${kind[0].toUpperCase()}${kind.slice(1)}`;
  return `Viewing ${kind.replace('_', ' ')}: ${name}`;
}

function syncSidebarHighlights() {
  document.querySelectorAll('.nav-btn').forEach((btn) => btn.classList.remove('active'));
  const topView = VIEW.split(':')[0];
  const nav = document.querySelector(`.nav-btn[data-view="${topView}"]`);
  if (nav) nav.classList.add('active');
  document.querySelectorAll('#savedPlaylists button').forEach((btn) => btn.classList.remove('active'));
  if (VIEW.startsWith('playlist:')) {
    const name = VIEW.slice('playlist:'.length);
    const btn = document.querySelector(`#savedPlaylists button[data-name="${CSS.escape(name)}"]`);
    if (btn) btn.classList.add('active');
  }
  const box = el('currentContext');
  box.textContent = sidebarContextText();
  box.classList.remove('hidden');
}

function renderSidebarPlaylists() {
  const box = el('savedPlaylists');
  box.innerHTML = '';
  (LIBRARY?.playlists || []).forEach((playlist) => {
    const btn = document.createElement('button');
    btn.dataset.name = playlist.name;
    btn.textContent = `${playlist.name} (${playlist.count})`;
    btn.onclick = () => { VIEW = `playlist:${playlist.name}`; renderCurrentView(); closeSidebar(); };
    box.appendChild(btn);
  });
  syncSidebarHighlights();
}

function updateNowPlaying(track) {
  el('nowTitle').textContent = track ? track.title : 'Nothing playing';
  el('overlayTitle').textContent = track ? track.title : 'Nothing playing';
  el('nowArtist').textContent = track ? (track.artist || 'Unknown Artist') : 'Select a track';
  el('overlayArtist').textContent = track ? (track.artist || 'Unknown Artist') : 'Select a track';
  el('nowAlbum').textContent = `Album: ${track ? (track.album || 'Unknown') : 'Unknown'}`;
  el('overlayAlbum').textContent = `Album: ${track ? (track.album || 'Unknown') : 'Unknown'}`;
  el('nowYear').textContent = track?.year ? `Year: ${track.year}` : '';
  el('overlayYear').textContent = track?.year ? `Year: ${track.year}` : '';
  const srcText = sourceTextFor(track);
  el('nowSource').textContent = srcText;
  el('overlaySource').textContent = srcText;
  const lyrics = track?.lyrics || 'Lyrics will appear here when available.';
  el('lyricsBox').textContent = lyrics;
  el('overlayLyrics').textContent = lyrics;
  if (track?.album_art) {
    el('nowArt').src = track.album_art; el('overlayArt').src = track.album_art;
    el('nowArt').classList.remove('hidden'); el('overlayArt').classList.remove('hidden');
    el('nowArtFallback').classList.add('hidden'); el('overlayArtFallback').classList.add('hidden');
  } else {
    el('nowArt').classList.add('hidden'); el('overlayArt').classList.add('hidden');
    el('nowArtFallback').classList.remove('hidden'); el('overlayArtFallback').classList.remove('hidden');
  }
  el('queueInfo').textContent = `Queue: ${PLAY_QUEUE.length} song(s)`;
  renderOverlayQueue();
}

function updatePlayPauseButtons() {
  const symbol = audio().paused ? '▶' : '⏸';
  el('playPauseBtn').textContent = symbol;
  el('overlayPlayPauseBtn').textContent = symbol;
}

function renderOverlayQueue() {
  const box = el('overlayQueue');
  if (!box) return;
  box.innerHTML = '';
  PLAY_QUEUE.forEach((id, index) => {
    const track = trackMap().get(id);
    if (!track) return;
    const row = document.createElement('button');
    row.className = `overlay-queue-row${index === PLAY_INDEX ? ' active' : ''}`;
    row.innerHTML = `<span class="overlay-queue-title-text">${escapeHtml(track.title)}</span><span class="overlay-queue-sub">${escapeHtml(track.artist)}${track.year ? ` · ${escapeHtml(track.year)}` : ''}</span>`;
    row.onclick = () => { PLAY_INDEX = index; PAUSED_MANUALLY = false; playFromQueue(index, true); };
    box.appendChild(row);
  });
}

function playFromQueue(index, autoPlay = true) {
  if (index < 0 || index >= PLAY_QUEUE.length) return;
  PLAY_INDEX = index;
  const track = currentTrack();
  if (!track) return;
  const player = audio();
  if (TRANSITION_STATE.fadeInterval) clearInterval(TRANSITION_STATE.fadeInterval);
  TRANSITION_STATE.fadeInterval = null;
  TRANSITION_STATE.inProgress = false;
  TRANSITION_STATE.armedTrackId = null;
  player.volume = 1;
  player.src = track.stream_url;
  player.load();
  updateNowPlaying(track);
  renderCurrentView();
  if (autoPlay) player.play().catch(() => {});
  updatePlayPauseButtons();
}

function playTrackById(id, sourceTracks = null, source = null) {
  const items = sourceTracks || CURRENT_VIEW_TRACKS || [];
  if (items.length) buildQueue(items, id, SHUFFLE);
  else { PLAY_QUEUE = [id]; PLAY_INDEX = 0; }
  if (source) PLAY_SOURCE = source;
  PAUSED_MANUALLY = false;
  playFromQueue(PLAY_INDEX, true);
}

function playNext(auto = false) {
  if (!PLAY_QUEUE.length) return;
  if (PAUSED_MANUALLY && auto) return;
  if (REPEAT === 'one') return playFromQueue(PLAY_INDEX, true);
  let nextIndex = PLAY_INDEX + 1;
  if (nextIndex >= PLAY_QUEUE.length) {
    if (REPEAT === 'all') {
      nextIndex = 0;
    } else {
      const currentId = currentQueueTrackId();
      const allIds = allTracks().map((t) => t.id);
      const extras = allIds.filter((id) => !PLAY_QUEUE.includes(id));
      if (extras.length) {
        PLAY_QUEUE = [...PLAY_QUEUE, ...(SHUFFLE ? shuffleArray(extras) : extras)];
        nextIndex = PLAY_INDEX + 1;
      } else {
        return;
      }
    }
  }
  PAUSED_MANUALLY = false;
  playFromQueue(nextIndex, true);
}
function playPrev() {
  const player = audio();
  if ((player.currentTime || 0) >= 5) {
    player.currentTime = 0;
    syncSeekUi();
    return;
  }
  if (!PLAY_QUEUE.length) return;
  let prevIndex = PLAY_INDEX - 1;
  if (prevIndex < 0) prevIndex = REPEAT === 'all' ? PLAY_QUEUE.length - 1 : 0;
  PAUSED_MANUALLY = false;
  playFromQueue(prevIndex, true);
}

function syncSelectionUI() {
  const count = SELECTED.size;
  el('selectionBar').classList.toggle('hidden', count === 0);
  el('selectedCount').textContent = `${count} selected`;
  ['addToPlaylistBtn','addToFolderBtn','clearSelectionBtn'].forEach((id) => { el(id).disabled = count === 0; });
}

function openMetadataModal(track) {
  el('metaTrackPath').value = track.path || '';
  el('metaTitle').value = track.title || '';
  el('metaArtist').value = track.artist || '';
  el('metaAlbum').value = track.album || '';
  el('metaYear').value = track.year || '';
  el('metaLyrics').value = track.lyrics || '';
  el('metaArtUrl').value = '';
  showModal('metadataModal');
}

async function saveMetadata() {
  const payload = { path: el('metaTrackPath').value, title: el('metaTitle').value.trim(), artist: el('metaArtist').value.trim(), album: el('metaAlbum').value.trim(), year: el('metaYear').value.trim(), lyrics: el('metaLyrics').value.trim(), album_art_url: el('metaArtUrl').value.trim() };
  const res = await fetch('/api/metadata/update', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const data = await res.json();
  if (!res.ok || !data.ok) return alert(data.error || 'Failed to update metadata');
  hideModal('metadataModal');
  await loadLibrary();
}

function renderTrackMenu(track, button, sourceTracks, source) {
  const menu = el('trackMenu');
  menu.innerHTML = `<button data-action="play">Play now</button><button data-action="next">Play next</button><button data-action="playlist">Add to playlist</button><button data-action="folder">Add to folder</button><button data-action="edit">Edit metadata</button>${track.album ? '<button data-action="album">Go to album</button>' : ''}`;
  const rect = button.getBoundingClientRect();
  menu.style.top = `${rect.bottom + window.scrollY + 4}px`;
  menu.style.left = `${Math.max(8, rect.left + window.scrollX - 120)}px`;
  menu.classList.remove('hidden');
  menu.querySelector('[data-action="play"]').onclick = () => { hideTrackMenu(); playTrackById(track.id, sourceTracks, source); };
  menu.querySelector('[data-action="next"]').onclick = () => {
    hideTrackMenu();
    const current = currentQueueTrackId();
    if (!PLAY_QUEUE.length) buildQueue(sourceTracks || [track], track.id, false);
    else {
      PLAY_QUEUE = PLAY_QUEUE.filter((id) => id !== track.id);
      PLAY_QUEUE.splice(Math.max(PLAY_INDEX + 1, 0), 0, track.id);
      if (current) PLAY_INDEX = PLAY_QUEUE.indexOf(current);
    }
    updateNowPlaying(currentTrack());
  };
  menu.querySelector('[data-action="playlist"]').onclick = () => { hideTrackMenu(); SELECTED = new Set([track.id]); syncSelectionUI(); openPlaylistModal(); };
  menu.querySelector('[data-action="folder"]').onclick = () => { hideTrackMenu(); SELECTED = new Set([track.id]); syncSelectionUI(); openFolderModal(); };
  menu.querySelector('[data-action="edit"]').onclick = () => { hideTrackMenu(); openMetadataModal(track); };
  const albumBtn = menu.querySelector('[data-action="album"]');
  if (albumBtn) albumBtn.onclick = () => { hideTrackMenu(); VIEW = `album:${track.album}`; renderCurrentView(); };
}

function renderTrackRows(items, title, meta, source) {
  CURRENT_VIEW_TRACKS = items.slice();
  el('contentTitle').textContent = title;
  el('contentMeta').textContent = meta;
  const actionBox = el('contextActions');
  actionBox.innerHTML = items.length ? '<button id="playNextViewBtn" class="ghost-btn">Play next</button>' : '';
  const playNextViewBtn = el('playNextViewBtn');
  if (playNextViewBtn) playNextViewBtn.onclick = () => {
    if (!items.length) return;
    if (!PLAY_QUEUE.length) {
      PLAY_SOURCE = source;
      buildQueue(items, items[0].id, false);
      playFromQueue(0, true);
      return;
    }
    const current = currentQueueTrackId();
    const idsToInsert = items.map((t) => t.id).filter((id) => id !== current);
    PLAY_QUEUE = [...PLAY_QUEUE.slice(0, PLAY_INDEX + 1), ...idsToInsert, ...PLAY_QUEUE.slice(PLAY_INDEX + 1).filter((id) => !idsToInsert.includes(id))];
    if (current) PLAY_INDEX = PLAY_QUEUE.indexOf(current);
    PLAY_SOURCE = source;
    updateNowPlaying(currentTrack());
  };
  const box = el('tracksContainer');
  box.innerHTML = '';
  items.forEach((track) => {
    const row = document.createElement('div');
    row.className = `track-row${currentQueueTrackId() === track.id ? ' active' : ''}`;
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = SELECTED.has(track.id);
    cb.onchange = () => { cb.checked ? SELECTED.add(track.id) : SELECTED.delete(track.id); syncSelectionUI(); };
    const main = document.createElement('div');
    main.className = 'track-main';
    const yearText = track.year ? ` · ${escapeHtml(track.year)}` : '';
    main.innerHTML = `<div class="track-title">${escapeHtml(track.title)}</div><div class="track-sub">${escapeHtml(track.artist)}</div><div class="track-folder">${escapeHtml(track.album || 'Unknown')}${yearText}${track.duration ? ` · ${fmtTime(track.duration)}` : ''}</div>`;
    main.onclick = () => playTrackById(track.id, items, source);
    const more = document.createElement('button'); more.className = 'track-mini-btn'; more.textContent = '…';
    more.onclick = (event) => { event.stopPropagation(); renderTrackMenu(track, more, items, source); };
    row.append(cb, main, more); box.appendChild(row);
  });
}

function renderItemCards(title, meta, items, typeKey) {
  el('contentTitle').textContent = title;
  el('contentMeta').textContent = meta;
  el('contextActions').innerHTML = '';
  CURRENT_VIEW_TRACKS = [];
  const box = el('tracksContainer'); box.innerHTML = '';
  const grid = document.createElement('div'); grid.className = 'item-grid';
  filterItems(items).forEach((item) => {
    const card = document.createElement('div'); card.className = 'item-card';
    card.innerHTML = `<div class="item-card-title">${escapeHtml(item.name)}</div><div class="item-card-meta">${item.count} song(s)</div>`;
    card.onclick = () => { VIEW = `${typeKey.slice(0,-1)}:${item.name}`; renderCurrentView(); };
    grid.appendChild(card);
  });
  box.appendChild(grid);
}

function renderCurrentView() {
  syncSidebarHighlights();
  if (VIEW === 'all') return renderTrackRows(filterTracks(allTracks()), 'All Songs', `${filterTracks(allTracks()).length} track(s)`, { type: 'All Songs', name: 'All Songs' });
  if (VIEW === 'playlists') return renderItemCards('Playlists', `${(LIBRARY?.playlists || []).length} shown`, LIBRARY?.playlists || [], 'playlists');
  if (VIEW === 'artists') return renderItemCards('Artists', `${filterItems(LIBRARY?.artists || []).length} shown`, LIBRARY?.artists || [], 'artists');
  if (VIEW === 'albums') return renderItemCards('Albums', `${filterItems(LIBRARY?.albums || []).length} shown`, LIBRARY?.albums || [], 'albums');
  if (VIEW === 'folders') return renderItemCards('Folders', `${filterItems(LIBRARY?.folders || []).length} shown`, LIBRARY?.folders || [], 'folders');
  if (VIEW === 'release_years') return renderItemCards('Release Years', `${filterItems(LIBRARY?.release_years || []).length} shown`, LIBRARY?.release_years || [], 'release_years');
  if (VIEW.startsWith('playlist:')) { const n = VIEW.slice(9); const p = (LIBRARY?.playlists || []).find((x) => x.name === n); const rows = filterTracks(tracksByIds(p?.tracks || [])); return renderTrackRows(rows, n, `${rows.length} track(s)`, { type: 'playlist', name: n }); }
  if (VIEW.startsWith('artist:')) { const n = VIEW.slice(7); const p = (LIBRARY?.artists || []).find((x) => x.name === n); const rows = filterTracks(tracksByIds(p?.tracks || [])); return renderTrackRows(rows, n, `${rows.length} track(s)`, { type: 'artist', name: n }); }
  if (VIEW.startsWith('album:')) { const n = VIEW.slice(6); const p = (LIBRARY?.albums || []).find((x) => x.name === n); const rows = filterTracks(tracksByIds(p?.tracks || [])); return renderTrackRows(rows, n, `${rows.length} track(s)`, { type: 'album', name: n }); }
  if (VIEW.startsWith('folder:')) { const n = VIEW.slice(7); const p = (LIBRARY?.folders || []).find((x) => x.name === n); const rows = filterTracks(tracksByIds(p?.tracks || [])); return renderTrackRows(rows, n, `${rows.length} track(s)`, { type: 'folder', name: n }); }
  if (VIEW.startsWith('release_year:')) { const n = VIEW.slice('release_year:'.length); const p = (LIBRARY?.release_years || []).find((x) => x.name === n); const rows = filterTracks(tracksByIds(p?.tracks || [])); return renderTrackRows(rows, n, `${rows.length} track(s)`, { type: 'release year', name: n }); }
}

async function loadLibrary() {
  const res = await fetch('/api/library', { cache: 'no-store' });
  LIBRARY = await res.json();
  el('brandVersion').textContent = `v${LIBRARY.version}`;
  renderSidebarPlaylists();
  renderCurrentView();
  updateNowPlaying(currentTrack());
}

async function addToPlaylist(name) { const ids = Array.from(SELECTED); if (!name || !ids.length) return; await fetch('/api/playlists', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, track_ids: ids }) }); hideModal('playlistModal'); SELECTED.clear(); syncSelectionUI(); await loadLibrary(); }
async function addToFolder(name) { const ids = Array.from(SELECTED); if (!name || !ids.length) return; await fetch('/api/folders/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, track_ids: ids }) }); hideModal('folderModal'); SELECTED.clear(); syncSelectionUI(); await loadLibrary(); }
function openPlaylistModal() { el('selectedSongsPreview').textContent = Array.from(SELECTED).map((id) => trackMap().get(id)?.title).filter(Boolean).join(', '); const list = el('playlistList'); list.innerHTML = ''; (LIBRARY?.playlists || []).forEach((playlist) => { const btn = document.createElement('button'); btn.className = 'playlist-chip'; btn.textContent = `${playlist.name} (${playlist.count})`; btn.onclick = () => addToPlaylist(playlist.name); list.appendChild(btn); }); showModal('playlistModal'); }
function openFolderModal() { const list = el('folderList'); list.innerHTML = ''; (LIBRARY?.folders || []).forEach((folder) => { const btn = document.createElement('button'); btn.className = 'playlist-chip'; btn.textContent = `${folder.name} (${folder.count})`; btn.onclick = () => addToFolder(folder.name); list.appendChild(btn); }); showModal('folderModal'); }

function saveSettings() { localStorage.setItem('music_transition_enabled', el('transitionEnabled').checked ? '1' : '0'); localStorage.setItem('music_transition_seconds', el('transitionSeconds').value || '0'); hideModal('settingsModal'); }
function loadSettings() { el('transitionEnabled').checked = localStorage.getItem('music_transition_enabled') === '1'; el('transitionSeconds').value = localStorage.getItem('music_transition_seconds') || '0'; }

function syncSeekUi(previewCurrent = null) {
  const player = audio();
  const duration = Number(player.duration || 0);
  const current = Number((previewCurrent ?? player.currentTime) || 0);
  ['currentTime','overlayCurrentTime'].forEach((id) => { if (el(id)) el(id).textContent = fmtTime(current); });
  ['totalTime','overlayTotalTime'].forEach((id) => { if (el(id)) el(id).textContent = fmtTime(duration); });
  const max = 1000;
  const value = duration > 0 ? Math.round((current / duration) * max) : 0;
  ['seekBar','overlaySeekBar'].forEach((id) => {
    const node = el(id);
    if (!node) return;
    node.max = String(max);
    node.value = String(value);
    node.style.setProperty('--seek-pct', `${(value / max) * 100}%`);
  });
}

function attachRangeSeek(inputId) {
  const input = el(inputId);
  if (!input) return;
  const player = audio();
  const max = 1000;
  const preview = (evt) => {
    const duration = Number(player.duration || 0);
    const pct = Number(input.value || 0) / max;
    syncSeekUi(duration * pct);
  };
  const commit = () => {
    const duration = Number(player.duration || 0);
    if (!duration) return;
    const pct = Number(input.value || 0) / max;
    player.currentTime = duration * pct;
    syncSeekUi();
  };
  const begin = (evt) => {
    evt.stopPropagation();
    IS_SEEKING = true;
    preview();
  };
  input.addEventListener('pointerdown', begin);
  input.addEventListener('mousedown', begin);
  input.addEventListener('touchstart', begin, { passive: true });
  input.addEventListener('input', preview);
  input.addEventListener('change', () => { commit(); IS_SEEKING = false; });
  input.addEventListener('pointerup', () => { commit(); IS_SEEKING = false; });
  input.addEventListener('mouseup', () => { commit(); IS_SEEKING = false; });
  input.addEventListener('touchend', () => { commit(); IS_SEEKING = false; }, { passive: true });
}

function transitionEnabled() { return !!el('transitionEnabled')?.checked; }
function transitionSeconds() { return Math.max(0, Number(el('transitionSeconds')?.value || 0)); }
function clearTransitionState() {
  if (TRANSITION_STATE.fadeInterval) clearInterval(TRANSITION_STATE.fadeInterval);
  TRANSITION_STATE = { inProgress: false, armedTrackId: null, fadeInterval: null };
  audio().volume = 1;
}
function maybeStartTransition() {
  const player = audio();
  const trackId = currentQueueTrackId();
  const seconds = transitionSeconds();
  if (!transitionEnabled() || seconds <= 0 || !trackId || TRANSITION_STATE.inProgress || TRANSITION_STATE.armedTrackId === trackId) return;
  const duration = Number(player.duration || 0);
  const current = Number(player.currentTime || 0);
  const remaining = duration - current;
  if (!duration || remaining > seconds || remaining <= 0.35 || PAUSED_MANUALLY || player.paused || !player.src) return;
  TRANSITION_STATE.armedTrackId = trackId;
  TRANSITION_STATE.inProgress = true;
  const fadeMs = Math.max(800, Math.min(seconds * 1000, 5000));
  const steps = Math.max(12, Math.round(fadeMs / 80));
  let step = 0;
  const startVolume = player.volume || 1;
  TRANSITION_STATE.fadeInterval = setInterval(() => {
    step += 1;
    player.volume = Math.max(0, startVolume * (1 - (step / steps)));
    if (step >= steps) {
      clearInterval(TRANSITION_STATE.fadeInterval);
      TRANSITION_STATE.fadeInterval = null;
      playNext(false);
      const nextPlayer = audio();
      nextPlayer.volume = 0;
      let inStep = 0;
      TRANSITION_STATE.fadeInterval = setInterval(() => {
        inStep += 1;
        nextPlayer.volume = Math.min(1, inStep / steps);
        if (inStep >= steps) clearTransitionState();
      }, Math.max(40, fadeMs / steps));
    }
  }, Math.max(40, fadeMs / steps));
}

window.addEventListener('DOMContentLoaded', () => {
  document.addEventListener('contextmenu', (e) => e.preventDefault());
  loadSettings(); loadLibrary();
  el('menuToggle').onclick = (e) => { e.stopPropagation(); openSidebar(); };
  el('closeSidebarBtn').onclick = closeSidebar; el('mobileOverlay').onclick = closeSidebar;
  document.addEventListener('click', (e) => { if (!e.target.closest('#trackMenu') && !e.target.closest('.track-mini-btn')) hideTrackMenu(); });
  document.querySelectorAll('.nav-btn').forEach((btn) => btn.onclick = () => { VIEW = btn.dataset.view; renderCurrentView(); closeSidebar(); });
  el('searchInput').addEventListener('focus', () => { if (isMobile()) document.body.classList.add('searching'); });
  el('searchInput').addEventListener('blur', () => setTimeout(() => document.body.classList.remove('searching'), 150));
  el('searchInput').addEventListener('input', (e) => { FILTER_VALUE = e.target.value || ''; el('clearSearchBtn').classList.toggle('hidden', !FILTER_VALUE); renderCurrentView(); });
  el('clearSearchBtn').onclick = () => { el('searchInput').value = ''; FILTER_VALUE = ''; el('clearSearchBtn').classList.add('hidden'); renderCurrentView(); };
  el('refreshBtn').onclick = loadLibrary; el('settingsBtn').onclick = () => showModal('settingsModal'); el('closeSettingsModalBtn').onclick = () => hideModal('settingsModal'); el('saveSettingsBtn').onclick = saveSettings; document.querySelector('[data-close="settings"]').onclick = () => hideModal('settingsModal');
  el('shuffleAllBtn').onclick = () => {
    if (!CURRENT_VIEW_TRACKS.length) return;
    SHUFFLE = true;
    ['shuffleToggleBtn','overlayShuffleBtn'].forEach((id) => el(id).classList.add('active-toggle'));
    const typeMap = { all: 'All Songs', playlist: 'playlist', artist: 'artist', album: 'album', folder: 'folder', release_year: 'release year' };
    PLAY_SOURCE = { type: typeMap[VIEW.split(':')[0]] || 'All Songs', name: el('contentTitle').textContent };
    const ids = CURRENT_VIEW_TRACKS.map((t) => t.id);
    PLAY_QUEUE = shuffleArray(ids);
    PLAY_INDEX = 0;
    PAUSED_MANUALLY = false;
    playFromQueue(0, true);
  };
  const toggleShuffle = () => {
    SHUFFLE = !SHUFFLE;
    ['shuffleToggleBtn','overlayShuffleBtn'].forEach((id) => el(id).classList.toggle('active-toggle', SHUFFLE));
    if (!PLAY_QUEUE.length) return;
    const current = currentQueueTrackId();
    if (!current) return;
    const rest = PLAY_QUEUE.filter((id) => id !== current);
    PLAY_QUEUE = [current, ...(SHUFFLE ? shuffleArray(rest) : rest)];
    PLAY_INDEX = 0;
    updateNowPlaying(currentTrack());
    renderOverlayQueue();
  };
  el('shuffleToggleBtn').onclick = toggleShuffle; el('overlayShuffleBtn').onclick = toggleShuffle;
  const toggleRepeat = () => { REPEAT = REPEAT === 'off' ? 'all' : REPEAT === 'all' ? 'one' : 'off'; const txt = REPEAT === 'off' ? 'Off' : REPEAT === 'all' ? 'All' : '1'; el('repeatToggleBtn').textContent = txt; el('overlayRepeatBtn').textContent = txt; };
  el('repeatToggleBtn').onclick = toggleRepeat; el('overlayRepeatBtn').onclick = toggleRepeat;
  el('addToPlaylistBtn').onclick = openPlaylistModal; el('addToFolderBtn').onclick = openFolderModal; el('clearSelectionBtn').onclick = () => { SELECTED.clear(); syncSelectionUI(); renderCurrentView(); };
  el('closePlaylistModalBtn').onclick = () => hideModal('playlistModal'); el('closeFolderModalBtn').onclick = () => hideModal('folderModal'); el('closeMetadataModalBtn').onclick = () => hideModal('metadataModal');
  document.querySelectorAll('[data-close="playlist"]').forEach((node) => node.onclick = () => hideModal('playlistModal')); document.querySelectorAll('[data-close="folder"]').forEach((node) => node.onclick = () => hideModal('folderModal')); document.querySelectorAll('[data-close="metadata"]').forEach((node) => node.onclick = () => hideModal('metadataModal'));
  el('createPlaylistBtn').onclick = () => { const name = el('newPlaylistName').value.trim(); if (name) addToPlaylist(name); };
  el('createFolderBtn').onclick = async () => { const name = el('newFolderName').value.trim(); if (!name) return; await fetch('/api/folders/create', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }); if (SELECTED.size) addToFolder(name); else { hideModal('folderModal'); await loadLibrary(); } };
  el('saveMetadataBtn').onclick = saveMetadata;

  const player = audio();
  const togglePlayPause = () => { if (!player.src && PLAY_QUEUE.length) return playFromQueue(Math.max(PLAY_INDEX, 0), true); if (player.paused) { PAUSED_MANUALLY = false; player.play().catch(() => {}); } else { PAUSED_MANUALLY = true; player.pause(); } updatePlayPauseButtons(); };
  el('playPauseBtn').onclick = (e) => { e.stopPropagation(); togglePlayPause(); };
  el('overlayPlayPauseBtn').onclick = togglePlayPause;
  el('nextBtn').onclick = (e) => { e.stopPropagation(); playNext(false); }; el('overlayNextBtn').onclick = () => playNext(false);
  el('prevBtn').onclick = (e) => { e.stopPropagation(); playPrev(); }; el('overlayPrevBtn').onclick = () => playPrev();
  player.addEventListener('pause', updatePlayPauseButtons); player.addEventListener('play', updatePlayPauseButtons); player.addEventListener('ended', () => { if (!TRANSITION_STATE.inProgress) playNext(true); });
  player.addEventListener('loadedmetadata', () => { clearTransitionState(); syncSeekUi(); }); player.addEventListener('timeupdate', () => { if (!IS_SEEKING) syncSeekUi(); maybeStartTransition(); });

  attachRangeSeek('seekBar');
  attachRangeSeek('overlaySeekBar');

  el('playerBar').addEventListener('click', (e) => { if (e.target.closest('button') || e.target.closest('.seek-range')) return; openNowOverlay(); });
  el('closeNowOverlayBtn').onclick = closeNowOverlay;
  el('nowPlayingOverlay').addEventListener('click', (e) => { if (e.target === el('nowPlayingOverlay')) closeNowOverlay(); });
  let overlayStartY = null;
  let overlayStartX = null;
  const panel = el('nowOverlayPanel');
  const overlayHead = document.querySelector('.now-overlay-head');
  const queueHandle = el('overlayQueueHandle');
  const queueBox = el('overlayQueue');
  const beginSwipe = (e) => { overlayStartY = e.touches?.[0]?.clientY ?? null; overlayStartX = e.touches?.[0]?.clientX ?? null; };
  const moveSwipe = (e) => {
    if (overlayStartY == null) return;
    const y = e.touches?.[0]?.clientY ?? overlayStartY;
    const x = e.touches?.[0]?.clientX ?? overlayStartX;
    const dy = y - overlayStartY;
    const dx = Math.abs((x ?? 0) - (overlayStartX ?? 0));
    if (Math.abs(dy) < 70 || Math.abs(dy) < dx) return;
    if (dy < -70) {
      MOBILE_QUEUE_EXPANDED = true;
      applyMobileQueueState();
      overlayStartY = null; overlayStartX = null;
    } else if (dy > 70) {
      if (MOBILE_QUEUE_EXPANDED) {
        MOBILE_QUEUE_EXPANDED = false;
        applyMobileQueueState();
      } else {
        closeNowOverlay();
      }
      overlayStartY = null; overlayStartX = null;
    }
  };
  const endSwipe = () => { overlayStartY = null; overlayStartX = null; };
  [panel, overlayHead, queueHandle, queueBox].forEach((target) => {
    if (!target) return;
    target.addEventListener('touchstart', beginSwipe, { passive: true });
    target.addEventListener('touchmove', moveSwipe, { passive: true });
    target.addEventListener('touchend', endSwipe, { passive: true });
  });

  syncSelectionUI();
});
