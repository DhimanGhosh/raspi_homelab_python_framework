let LIBRARY = null;
let VIEW = 'all';
let FILTER_VALUE = '';
let SELECTED = new Set();
let CURRENT_VIEW_TRACKS = [];
let PLAY_QUEUE = [];
let PLAY_INDEX = -1;
let SHUFFLE = false;
let REPEAT = 'off';
let PLAY_SOURCE = { type: 'all songs', name: 'All Songs' };
let PAUSED_MANUALLY = false;

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

function closeSidebar() { if (isMobile()) { el('sidebar').classList.remove('open'); el('mobileOverlay').classList.add('hidden'); } }
function openSidebar() { if (isMobile()) { el('sidebar').classList.add('open'); el('mobileOverlay').classList.remove('hidden'); } }
function hideTrackMenu() { el('trackMenu').classList.add('hidden'); el('trackMenu').innerHTML = ''; }

function filterTracks(items) {
  const q = FILTER_VALUE.trim().toLowerCase();
  if (!q) return items;
  return items.filter((track) => `${track.title} ${track.artist} ${track.album} ${track.folder} ${track.filename}`.toLowerCase().includes(q));
}
function filterItems(items) {
  const q = FILTER_VALUE.trim().toLowerCase();
  if (!q) return items;
  return items.filter((item) => item.name.toLowerCase().includes(q));
}
function shuffleArray(arr) {
  const copy = arr.slice();
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}
function buildQueue(items, startTrackId = null, shuffle = false) {
  const ids = items.map((t) => t.id);
  if (!ids.length) { PLAY_QUEUE = []; PLAY_INDEX = -1; return; }
  let queue = ids.slice();
  if (shuffle) {
    const current = startTrackId || ids[0];
    const remaining = ids.filter((id) => id !== current);
    queue = [current, ...shuffleArray(remaining)];
  }
  PLAY_QUEUE = queue;
  PLAY_INDEX = Math.max(0, queue.indexOf(startTrackId || queue[0]));
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
  const selectedLabel = VIEW.includes(':') ? VIEW.split(':')[1] : topView;
  box.textContent = `Selected: ${selectedLabel}`;
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
  el('nowArtist').textContent = track ? (track.artist || 'Unknown Artist') : 'Select a track';
  el('nowAlbum').textContent = `Album: ${track ? (track.album || 'Unknown') : 'Unknown'}`;
  const sourceText = track ? `${track.title} playing from ${PLAY_SOURCE.type} ${PLAY_SOURCE.name}` : 'Source: —';
  el('nowSource').textContent = sourceText;
  el('lyricsBox').textContent = track?.lyrics || 'Lyrics will appear here when available.';
  if (track?.album_art) {
    el('nowArt').src = track.album_art;
    el('nowArt').classList.remove('hidden');
    el('nowArtFallback').classList.add('hidden');
  } else {
    el('nowArt').classList.add('hidden');
    el('nowArtFallback').classList.remove('hidden');
  }
  el('queueInfo').textContent = `Queue: ${PLAY_QUEUE.length} song(s)`;
}

function updatePlayPauseButton() { el('playPauseBtn').textContent = audio().paused ? '▶' : '⏸'; }

function playFromQueue(index, autoPlay = true) {
  if (index < 0 || index >= PLAY_QUEUE.length) return;
  PLAY_INDEX = index;
  const track = currentTrack();
  if (!track) return;
  const player = audio();
  player.src = track.stream_url;
  updateNowPlaying(track);
  renderCurrentView();
  if (autoPlay) player.play().catch(() => {});
  updatePlayPauseButton();
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
    if (REPEAT === 'all') nextIndex = 0;
    else return;
  }
  PAUSED_MANUALLY = false;
  playFromQueue(nextIndex, true);
}
function playPrev() {
  if (!PLAY_QUEUE.length) return;
  let prevIndex = PLAY_INDEX - 1;
  if (prevIndex < 0) prevIndex = REPEAT === 'all' ? PLAY_QUEUE.length - 1 : 0;
  PAUSED_MANUALLY = false;
  playFromQueue(prevIndex, true);
}

function syncSelectionUI() {
  const count = SELECTED.size;
  const bar = el('selectionBar');
  bar.classList.toggle('hidden', count === 0);
  el('selectedCount').textContent = `${count} selected`;
  ['addToPlaylistBtn','addToFolderBtn','clearSelectionBtn'].forEach((id) => { el(id).disabled = count === 0; });
}

function renderTrackMenu(track, button, sourceTracks, source) {
  const menu = el('trackMenu');
  menu.innerHTML = `
    <button data-action="play">Play now</button>
    <button data-action="next">Play next</button>
    <button data-action="playlist">Add to playlist</button>
    <button data-action="folder">Add to folder</button>
    ${track.album ? '<button data-action="album">Go to album</button>' : ''}
  `;
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
  if (playNextViewBtn) {
    playNextViewBtn.onclick = () => {
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
  }

  const box = el('tracksContainer');
  box.innerHTML = '';
  items.forEach((track) => {
    const row = document.createElement('div');
    row.className = `track-row${currentQueueTrackId() === track.id ? ' active' : ''}`;
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = SELECTED.has(track.id);
    cb.onchange = () => { cb.checked ? SELECTED.add(track.id) : SELECTED.delete(track.id); syncSelectionUI(); };

    const main = document.createElement('div');
    main.className = 'track-main';
    main.innerHTML = `<div class="track-title">${escapeHtml(track.title)}</div><div class="track-sub">${escapeHtml(track.artist)}</div><div class="track-folder">${escapeHtml(track.album || 'Unknown')} ${track.duration ? ` · ${fmtTime(track.duration)}` : ''}</div>`;
    main.onclick = () => playTrackById(track.id, items, source);

    const more = document.createElement('button');
    more.className = 'track-mini-btn';
    more.textContent = '…';
    more.onclick = (event) => { event.stopPropagation(); renderTrackMenu(track, more, items, source); };
    row.append(cb, main, more);
    box.appendChild(row);
  });
}

function renderItemCards(title, meta, items, typeKey) {
  el('contentTitle').textContent = title;
  el('contentMeta').textContent = meta;
  el('contextActions').innerHTML = '';
  CURRENT_VIEW_TRACKS = [];
  const box = el('tracksContainer');
  box.innerHTML = '';
  const grid = document.createElement('div');
  grid.className = 'item-grid';
  filterItems(items).forEach((item) => {
    const card = document.createElement('div');
    card.className = 'item-card';
    card.innerHTML = `<div class="item-card-title">${escapeHtml(item.name)}</div><div class="item-card-meta">${item.count} song(s)</div><div class="item-card-actions"><button class="playlist-chip open-btn">Open</button><button class="playlist-chip next-btn">Play next</button></div>`;
    card.querySelector('.open-btn').onclick = (e) => { e.stopPropagation(); VIEW = `${typeKey.slice(0,-1)}:${item.name}`; renderCurrentView(); };
    card.querySelector('.next-btn').onclick = (e) => {
      e.stopPropagation();
      const tracks = tracksByIds(item.tracks || []);
      if (!tracks.length) return;
      if (!PLAY_QUEUE.length) {
        PLAY_SOURCE = { type: typeKey.slice(0, -1), name: item.name };
        buildQueue(tracks, tracks[0].id, false); playFromQueue(0, true); return;
      }
      const idsToInsert = tracks.map((t) => t.id);
      PLAY_QUEUE = [...PLAY_QUEUE.slice(0, PLAY_INDEX + 1), ...idsToInsert, ...PLAY_QUEUE.slice(PLAY_INDEX + 1).filter((id) => !idsToInsert.includes(id))];
      updateNowPlaying(currentTrack());
    };
    grid.appendChild(card);
  });
  box.appendChild(grid);
}

function renderCurrentView() {
  syncSidebarHighlights();
  if (VIEW === 'all') return renderTrackRows(filterTracks(allTracks()), 'All Songs', `${filterTracks(allTracks()).length} track(s)`, { type: 'all songs', name: 'All Songs' });
  if (VIEW === 'playlists') return renderItemCards('Playlists', `${(LIBRARY?.playlists || []).length} shown`, LIBRARY?.playlists || [], 'playlists');
  if (VIEW === 'artists') return renderItemCards('Artists', `${filterItems(LIBRARY?.artists || []).length} shown`, LIBRARY?.artists || [], 'artists');
  if (VIEW === 'albums') return renderItemCards('Albums', `${filterItems(LIBRARY?.albums || []).length} shown`, LIBRARY?.albums || [], 'albums');
  if (VIEW === 'folders') return renderItemCards('Folders', `${filterItems(LIBRARY?.folders || []).length} shown`, LIBRARY?.folders || [], 'folders');
  if (VIEW.startsWith('playlist:')) { const n = VIEW.slice(9); const p = (LIBRARY?.playlists || []).find((x) => x.name === n); return renderTrackRows(filterTracks(tracksByIds(p?.tracks || [])), n, `${filterTracks(tracksByIds(p?.tracks || [])).length} track(s)`, { type: 'playlist', name: n }); }
  if (VIEW.startsWith('artist:')) { const n = VIEW.slice(7); const p = (LIBRARY?.artists || []).find((x) => x.name === n); return renderTrackRows(filterTracks(tracksByIds(p?.tracks || [])), n, `${filterTracks(tracksByIds(p?.tracks || [])).length} track(s)`, { type: 'artist', name: n }); }
  if (VIEW.startsWith('album:')) { const n = VIEW.slice(6); const p = (LIBRARY?.albums || []).find((x) => x.name === n); return renderTrackRows(filterTracks(tracksByIds(p?.tracks || [])), n, `${filterTracks(tracksByIds(p?.tracks || [])).length} track(s)`, { type: 'album', name: n }); }
  if (VIEW.startsWith('folder:')) { const n = VIEW.slice(7); const p = (LIBRARY?.folders || []).find((x) => x.name === n); return renderTrackRows(filterTracks(tracksByIds(p?.tracks || [])), n, `${filterTracks(tracksByIds(p?.tracks || [])).length} track(s)`, { type: 'folder', name: n }); }
}

async function loadLibrary() {
  const res = await fetch('/api/library', { cache: 'no-store' });
  LIBRARY = await res.json();
  el('brandVersion').textContent = `v${LIBRARY.version}`;
  renderSidebarPlaylists();
  renderCurrentView();
  updateNowPlaying(currentTrack());
}

async function addToPlaylist(name) {
  const ids = Array.from(SELECTED);
  if (!name || !ids.length) return;
  await fetch('/api/playlists', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, track_ids: ids }) });
  el('playlistModal').classList.add('hidden');
  SELECTED.clear();
  syncSelectionUI();
  await loadLibrary();
}
async function addToFolder(name) {
  const ids = Array.from(SELECTED);
  if (!name || !ids.length) return;
  await fetch('/api/folders/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, track_ids: ids }) });
  el('folderModal').classList.add('hidden');
  SELECTED.clear();
  syncSelectionUI();
  await loadLibrary();
}
function openPlaylistModal() {
  el('selectedSongsPreview').textContent = Array.from(SELECTED).map((id) => trackMap().get(id)?.title).filter(Boolean).join(', ');
  const list = el('playlistList'); list.innerHTML = '';
  (LIBRARY?.playlists || []).forEach((playlist) => {
    const btn = document.createElement('button'); btn.className = 'playlist-chip'; btn.textContent = `${playlist.name} (${playlist.count})`; btn.onclick = () => addToPlaylist(playlist.name); list.appendChild(btn);
  });
  el('playlistModal').classList.remove('hidden');
}
function openFolderModal() {
  const list = el('folderList'); list.innerHTML = '';
  (LIBRARY?.folders || []).forEach((folder) => { const btn = document.createElement('button'); btn.className = 'playlist-chip'; btn.textContent = `${folder.name} (${folder.count})`; btn.onclick = () => addToFolder(folder.name); list.appendChild(btn); });
  el('folderModal').classList.remove('hidden');
}

window.addEventListener('DOMContentLoaded', () => {
  loadLibrary();
  el('menuToggle').onclick = openSidebar;
  el('closeSidebarBtn').onclick = closeSidebar;
  el('mobileOverlay').onclick = closeSidebar;
  document.addEventListener('click', (e) => { if (!e.target.closest('#trackMenu') && !e.target.closest('.track-mini-btn')) hideTrackMenu(); });

  document.querySelectorAll('.nav-btn').forEach((btn) => btn.onclick = () => { VIEW = btn.dataset.view; renderCurrentView(); closeSidebar(); });
  el('searchInput').addEventListener('focus', () => { if (isMobile()) document.body.classList.add('searching'); });
  el('searchInput').addEventListener('blur', () => setTimeout(() => document.body.classList.remove('searching'), 150));
  el('searchInput').addEventListener('input', (e) => {
    FILTER_VALUE = e.target.value || '';
    el('clearSearchBtn').classList.toggle('hidden', !FILTER_VALUE);
    renderCurrentView();
  });
  el('clearSearchBtn').onclick = () => { el('searchInput').value = ''; FILTER_VALUE = ''; el('clearSearchBtn').classList.add('hidden'); renderCurrentView(); };
  el('refreshBtn').onclick = loadLibrary;

  el('shuffleAllBtn').onclick = () => {
    SHUFFLE = true;
    el('shuffleToggleBtn').classList.add('active-toggle');
    if (!CURRENT_VIEW_TRACKS.length) return;
    PLAY_SOURCE = { type: VIEW.split(':')[0], name: el('contentTitle').textContent };
    buildQueue(CURRENT_VIEW_TRACKS, CURRENT_VIEW_TRACKS[0].id, true);
    playFromQueue(0, true);
  };
  el('shuffleToggleBtn').onclick = () => {
    SHUFFLE = !SHUFFLE;
    el('shuffleToggleBtn').classList.toggle('active-toggle', SHUFFLE);
    if (!PLAY_QUEUE.length) return;
    const current = currentQueueTrackId();
    const source = CURRENT_VIEW_TRACKS.length ? CURRENT_VIEW_TRACKS : tracksByIds(PLAY_QUEUE);
    buildQueue(source, current, SHUFFLE);
    updateNowPlaying(currentTrack());
  };
  el('repeatToggleBtn').onclick = () => {
    REPEAT = REPEAT === 'off' ? 'all' : REPEAT === 'all' ? 'one' : 'off';
    el('repeatToggleBtn').textContent = REPEAT === 'off' ? 'Off' : REPEAT === 'all' ? 'All' : '1';
  };

  el('addToPlaylistBtn').onclick = openPlaylistModal;
  el('addToFolderBtn').onclick = openFolderModal;
  el('clearSelectionBtn').onclick = () => { SELECTED.clear(); syncSelectionUI(); renderCurrentView(); };
  el('closePlaylistModalBtn').onclick = () => el('playlistModal').classList.add('hidden');
  el('closeFolderModalBtn').onclick = () => el('folderModal').classList.add('hidden');
  document.querySelectorAll('[data-close]').forEach((node) => node.onclick = () => el(node.dataset.close === 'playlist' ? 'playlistModal' : 'folderModal').classList.add('hidden'));
  el('createPlaylistBtn').onclick = () => { const name = el('newPlaylistName').value.trim(); if (name) addToPlaylist(name); };
  el('createFolderBtn').onclick = async () => { const name = el('newFolderName').value.trim(); if (!name) return; await fetch('/api/folders/create', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }); if (SELECTED.size) addToFolder(name); else { el('folderModal').classList.add('hidden'); await loadLibrary(); } };

  el('playPauseBtn').onclick = () => {
    const player = audio();
    if (!player.src && PLAY_QUEUE.length) return playFromQueue(Math.max(PLAY_INDEX, 0), true);
    if (player.paused) { PAUSED_MANUALLY = false; player.play().catch(() => {}); }
    else { PAUSED_MANUALLY = true; player.pause(); }
    updatePlayPauseButton();
  };
  el('nextBtn').onclick = () => playNext(false);
  el('prevBtn').onclick = playPrev;

  const player = audio();
  player.addEventListener('pause', updatePlayPauseButton);
  player.addEventListener('play', updatePlayPauseButton);
  player.addEventListener('ended', () => playNext(true));
  player.addEventListener('timeupdate', () => {
    el('currentTime').textContent = fmtTime(player.currentTime || 0);
    el('totalTime').textContent = fmtTime(player.duration || 0);
    el('seekBar').value = player.duration ? String((player.currentTime / player.duration) * 100) : '0';
  });
  el('seekBar').addEventListener('input', (e) => { if (player.duration) player.currentTime = (Number(e.target.value) / 100) * player.duration; });
  syncSelectionUI();
});
