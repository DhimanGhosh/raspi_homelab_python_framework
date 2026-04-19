let LIBRARY = null;
let VIEW = 'all';
let FILTER_VALUE = '';
let SELECTED = new Set();
let CURRENT_VIEW_TRACKS = [];
let PLAY_QUEUE = [];
let PLAY_INDEX = -1;
let REPEAT = 'off';
let SHUFFLE_ENABLED = false;
let MANAGE_PLAYLIST_NAME = null;
let MODAL_TRACK_IDS = null;
let ACTIVE_AUDIO_KEY = 'A';
let CROSSFADE_TIMER = null;
let CROSSFADE_IN_PROGRESS = false;
let LAST_SCHEDULED_TRACK_ID = null;
let DUPLICATE_MODAL_STATE = null;

const el = (id) => document.getElementById(id);
const audioByKey = (key) => document.getElementById(key === 'A' ? 'audioPlayerA' : 'audioPlayerB');
const activeAudio = () => audioByKey(ACTIVE_AUDIO_KEY);
const standbyAudio = () => audioByKey(ACTIVE_AUDIO_KEY === 'A' ? 'B' : 'A');
const activeAudioKey = () => ACTIVE_AUDIO_KEY;
const standbyAudioKey = () => (ACTIVE_AUDIO_KEY === 'A' ? 'B' : 'A');

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

function fmtTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function allTracks() { return LIBRARY?.tracks || []; }
function trackMap() { return new Map(allTracks().map((track) => [track.id, track])); }
function currentQueueTrackId() { return PLAY_QUEUE[PLAY_INDEX] || null; }
function currentQueueTrack() { return trackMap().get(currentQueueTrackId()) || null; }
function currentTrackIds() { return MODAL_TRACK_IDS || Array.from(SELECTED); }

function shuffleArray(items) {
  const arr = items.slice();
  for (let i = arr.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function tracksByIds(ids) {
  const map = trackMap();
  return (ids || []).map((id) => map.get(id)).filter(Boolean);
}

function filterTracks(items) {
  const q = FILTER_VALUE.trim().toLowerCase();
  if (!q) return items;
  return items.filter((track) => {
    const hay = [track.title, track.artist, track.album, track.folder, track.year, track.filename].join(' ').toLowerCase();
    return hay.includes(q);
  });
}

function setCurrentViewTracks(items) {
  CURRENT_VIEW_TRACKS = items.map((track) => track.id);
}

function getCurrentViewTracks() {
  return tracksByIds(CURRENT_VIEW_TRACKS);
}

function syncSearchClear() {
  el('clearSearchBtn').classList.toggle('hidden', !FILTER_VALUE);
}

function syncSelectionUI() {
  const count = SELECTED.size;
  const bar = el('selectionBar');
  bar.classList.toggle('hidden', count === 0);
  el('selectedCount').textContent = `${count} selected`;
  ['addToPlaylistBtn', 'addToFolderBtn', 'clearSelectionBtn'].forEach((id) => { el(id).disabled = count === 0; });
}

function syncToggleButtons() {
  el('shuffleToggleBtn').classList.toggle('active', SHUFFLE_ENABLED);
  const repeatBtn = el('repeatToggleBtn');
  repeatBtn.classList.toggle('active', REPEAT !== 'off');
  repeatBtn.textContent = REPEAT === 'one' ? '1' : (REPEAT === 'all' ? 'All' : 'Off');
}

function updateQueueSummary() {
  el('queueSummary').textContent = `Queue: ${PLAY_QUEUE.length} song(s)`;
}

function applyNowPlaying(track) {
  el('nowTitle').textContent = track ? track.title : 'Nothing playing';
  el('nowArtist').textContent = track ? (track.artist || 'Unknown Artist') : 'Select a track';
  el('nowAlbum').textContent = `Album: ${track?.album || 'Unknown'}`;
  el('nowMeta').textContent = track ? `${track.duration_text || '0:00'}${track.year ? ` • ${track.year}` : ''}` : '0:00';
  el('nowLyrics').textContent = track?.lyrics || 'Lyrics will appear here when available.';
  const cover = el('nowCover');
  const fallback = el('nowCoverFallback');
  if (track?.cover_data_url) {
    cover.src = track.cover_data_url;
    cover.style.display = 'block';
    fallback.style.display = 'none';
  } else {
    cover.removeAttribute('src');
    cover.style.display = 'none';
    fallback.style.display = 'grid';
  }
  updateQueueSummary();
}

function setQueueFromTracks(tracks, startId = null, doShuffle = false) {
  const ids = tracks.map((track) => track.id);
  PLAY_QUEUE = doShuffle ? shuffleArray(ids) : ids;
  PLAY_INDEX = startId ? Math.max(0, PLAY_QUEUE.indexOf(startId)) : 0;
  updateQueueSummary();
}

function getArtistOrder() {
  return (LIBRARY?.artists || []).map((artist) => artist.name).sort((a, b) => a.localeCompare(b));
}

function findNextArtistTracks() {
  const current = currentQueueTrack();
  if (!current) return [];
  const currentArtist = (current.artists && current.artists[0]) || current.artist || 'Unknown Artist';
  const order = getArtistOrder();
  if (!order.length) return [];
  let idx = order.findIndex((name) => name.toLowerCase() === String(currentArtist).toLowerCase());
  if (idx < 0) idx = 0;
  const nextArtist = order[(idx + 1) % order.length];
  const artistEntry = (LIBRARY?.artists || []).find((entry) => entry.name === nextArtist);
  return shuffleArray(tracksByIds(artistEntry?.tracks || []));
}

function stopAudio(player) {
  try { player.pause(); } catch (_) {}
  player.removeAttribute('src');
  player.load();
  player.currentTime = 0;
  player.volume = 1;
}

function syncPlaybackButton() {
  el('playPauseBtn').textContent = activeAudio().paused ? '▶' : '⏸';
}

function resetCrossfadeSchedule() {
  window.clearTimeout(CROSSFADE_TIMER);
  CROSSFADE_TIMER = null;
  LAST_SCHEDULED_TRACK_ID = null;
}

function currentQueueInfo() {
  return { queue: PLAY_QUEUE.slice(), index: PLAY_INDEX };
}

function computeNextPlaybackPlan() {
  if (!PLAY_QUEUE.length || PLAY_INDEX < 0) return null;
  if (REPEAT === 'one') {
    return { queue: PLAY_QUEUE.slice(), index: PLAY_INDEX, nextId: PLAY_QUEUE[PLAY_INDEX], sameTrack: true };
  }
  const nextIndex = PLAY_INDEX + 1;
  if (nextIndex < PLAY_QUEUE.length) {
    return { queue: PLAY_QUEUE.slice(), index: nextIndex, nextId: PLAY_QUEUE[nextIndex], sameTrack: false };
  }
  if (REPEAT === 'all') {
    return { queue: PLAY_QUEUE.slice(), index: 0, nextId: PLAY_QUEUE[0], sameTrack: false };
  }
  const nextArtistTracks = findNextArtistTracks();
  if (nextArtistTracks.length) {
    const queue = shuffleArray(nextArtistTracks.map((track) => track.id));
    return { queue, index: 0, nextId: queue[0], sameTrack: false };
  }
  return null;
}

function applyPlaybackPlan(plan) {
  PLAY_QUEUE = plan.queue.slice();
  PLAY_INDEX = plan.index;
  updateQueueSummary();
}

function loadTrackOnPlayer(track, player, { autoplay = true, volume = 1 } = {}) {
  if (!track) return Promise.resolve(false);
  player.src = track.stream_url;
  player.volume = volume;
  player.currentTime = 0;
  if (!autoplay) return Promise.resolve(true);
  return player.play().then(() => true).catch(() => false);
}

function openContextMenu(x, y, items) {
  const menu = el('contextMenu');
  menu.innerHTML = items.map((item) => `<button class="context-btn" data-action="${esc(item.action)}">${esc(item.label)}</button>`).join('');
  menu.classList.remove('hidden');
  menu.style.left = '0px';
  menu.style.top = '0px';
  const pad = 10;
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  const maxLeft = Math.max(pad, window.innerWidth - width - pad);
  const maxTop = Math.max(pad, window.innerHeight - height - pad);
  menu.style.left = `${Math.min(Math.max(pad, x), maxLeft)}px`;
  menu.style.top = `${Math.min(Math.max(pad, y), maxTop)}px`;
  menu.querySelectorAll('[data-action]').forEach((node) => {
    node.onclick = () => {
      const item = items.find((entry) => entry.action === node.dataset.action);
      closeContextMenu();
      if (item?.handler) item.handler();
    };
  });
}

function closeContextMenu() {
  el('contextMenu').classList.add('hidden');
}

function openPlaylistModal(trackIds = null) {
  MODAL_TRACK_IDS = trackIds;
  const ids = currentTrackIds();
  const names = tracksByIds(ids).map((track) => track.title).slice(0, 3);
  el('playlistModalSongName').textContent = names.length ? `Selected: ${names.join(', ')}${ids.length > 3 ? '…' : ''}` : 'No song selected';
  renderPlaylistModal();
  el('playlistModal').classList.remove('hidden');
}

function closePlaylistModal() {
  MODAL_TRACK_IDS = null;
  el('playlistModal').classList.add('hidden');
  el('newPlaylistName').value = '';
}

function openFolderModal(trackIds = null) {
  MODAL_TRACK_IDS = trackIds;
  const ids = currentTrackIds();
  const names = tracksByIds(ids).map((track) => track.title).slice(0, 3);
  el('folderModalSongName').textContent = names.length ? `Selected: ${names.join(', ')}${ids.length > 3 ? '…' : ''}` : 'No song selected';
  renderFolderModal();
  el('folderModal').classList.remove('hidden');
}

function closeFolderModal() {
  MODAL_TRACK_IDS = null;
  el('folderModal').classList.add('hidden');
  el('newFolderName').value = '';
}

function openManagePlaylistModal(name) {
  MANAGE_PLAYLIST_NAME = name;
  el('renamePlaylistName').value = name;
  el('managePlaylistModal').classList.remove('hidden');
}

function closeManagePlaylistModal() {
  MANAGE_PLAYLIST_NAME = null;
  el('managePlaylistModal').classList.add('hidden');
  el('renamePlaylistName').value = '';
}

function openSettingsModal() {
  const settings = LIBRARY?.settings || { crossfade_enabled: false, crossfade_seconds: 4 };
  el('crossfadeEnabled').checked = !!settings.crossfade_enabled;
  el('crossfadeSeconds').value = String(settings.crossfade_seconds || 4);
  el('crossfadeSecondsLabel').textContent = `${settings.crossfade_seconds || 4}s`;
  el('settingsModal').classList.remove('hidden');
}

function closeSettingsModal() {
  el('settingsModal').classList.add('hidden');
}

function openDuplicateModal(message, onAddAnyway) {
  DUPLICATE_MODAL_STATE = { onAddAnyway };
  el('duplicateMessage').textContent = message;
  el('duplicateModal').classList.remove('hidden');
}

function closeDuplicateModal() {
  DUPLICATE_MODAL_STATE = null;
  el('duplicateModal').classList.add('hidden');
}

async function addToPlaylist(name, addAnyway = false) {
  const ids = currentTrackIds();
  if (!name || !ids.length) return;
  const playlist = (LIBRARY?.playlists || []).find((entry) => entry.name === name);
  const duplicates = ids.filter((id) => (playlist?.tracks || []).includes(id));
  if (duplicates.length && !addAnyway) {
    const songNames = tracksByIds(duplicates).map((track) => track.title).slice(0, 5).join(', ');
    openDuplicateModal(
      `Already in playlist "${name}": ${songNames}${duplicates.length > 5 ? '…' : ''}.\nChoose whether to skip duplicates or add anyway.`,
      () => addToPlaylist(name, true),
    );
    return;
  }
  const res = await fetch('/api/playlists', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, track_ids: ids, add_anyway: addAnyway }),
  });
  if (!res.ok) {
    alert('Failed to add to playlist');
    return;
  }
  localStorage.setItem('music_player_last_playlist', name);
  closePlaylistModal();
  closeDuplicateModal();
  await loadLibrary();
  renderCurrentView();
}

function renderPlaylistModal() {
  const list = el('playlistList');
  list.innerHTML = '';
  (LIBRARY?.playlists || []).forEach((playlist) => {
    const btn = document.createElement('button');
    btn.className = 'playlist-chip';
    btn.textContent = `${playlist.name} (${playlist.count})`;
    btn.onclick = () => addToPlaylist(playlist.name);
    list.appendChild(btn);
  });
  const last = localStorage.getItem('music_player_last_playlist') || '';
  const lastBlock = el('lastPlaylistBlock');
  if (last) {
    lastBlock.classList.remove('hidden');
    el('lastPlaylistBtn').textContent = last;
    el('lastPlaylistBtn').onclick = () => addToPlaylist(last);
  } else {
    lastBlock.classList.add('hidden');
  }
}

async function createPlaylistAndAdd() {
  const name = el('newPlaylistName').value.trim();
  if (!name) return;
  await addToPlaylist(name);
}

function renderFolderModal() {
  const list = el('folderList');
  list.innerHTML = '';
  (LIBRARY?.folders || []).forEach((folder) => {
    const btn = document.createElement('button');
    btn.className = 'playlist-chip';
    btn.textContent = `${folder.name} (${folder.count})`;
    btn.onclick = () => addToFolder(folder.name);
    list.appendChild(btn);
  });
}

async function addToFolder(name) {
  const ids = currentTrackIds();
  if (!name || !ids.length) return;
  const res = await fetch('/api/folders/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, track_ids: ids }),
  });
  if (!res.ok) {
    alert('Failed to add to folder');
    return;
  }
  closeFolderModal();
  SELECTED.clear();
  await loadLibrary();
  renderCurrentView();
}

async function createFolderAndAdd() {
  const name = el('newFolderName').value.trim();
  if (!name) return;
  const res = await fetch('/api/folders/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    alert('Failed to create folder');
    return;
  }
  await addToFolder(name);
}

async function renamePlaylist() {
  if (!MANAGE_PLAYLIST_NAME) return;
  const newName = el('renamePlaylistName').value.trim();
  if (!newName) return;
  const res = await fetch('/api/playlists/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old_name: MANAGE_PLAYLIST_NAME, new_name: newName }),
  });
  if (!res.ok) {
    alert('Failed to rename playlist');
    return;
  }
  closeManagePlaylistModal();
  await loadLibrary();
  VIEW = 'playlists';
  renderCurrentView();
}

async function deletePlaylist() {
  if (!MANAGE_PLAYLIST_NAME) return;
  const res = await fetch('/api/playlists/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: MANAGE_PLAYLIST_NAME }),
  });
  if (!res.ok) {
    alert('Failed to delete playlist');
    return;
  }
  closeManagePlaylistModal();
  await loadLibrary();
  VIEW = 'playlists';
  renderCurrentView();
}

async function saveSettings() {
  const payload = {
    crossfade_enabled: el('crossfadeEnabled').checked,
    crossfade_seconds: Number(el('crossfadeSeconds').value || 0),
  };
  const res = await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    alert('Failed to save settings');
    return;
  }
  await loadLibrary();
  closeSettingsModal();
}

function renderSavedPlaylists() {
  const box = el('savedPlaylists');
  box.innerHTML = '';
  (LIBRARY?.playlists || []).forEach((playlist) => {
    const btn = document.createElement('button');
    btn.className = 'playlist-chip';
    btn.textContent = `${playlist.name} (${playlist.count})`;
    btn.onclick = () => {
      VIEW = `playlist:${playlist.name}`;
      renderCurrentView();
      closeSidebar();
    };
    box.appendChild(btn);
  });
}

function queueTrackNext(trackId) {
  if (!trackId) return;
  if (!PLAY_QUEUE.length || PLAY_INDEX < 0) {
    PLAY_QUEUE = [trackId];
    PLAY_INDEX = 0;
    playFromQueue(0);
    return;
  }
  const existingIndex = PLAY_QUEUE.indexOf(trackId);
  if (existingIndex >= 0) PLAY_QUEUE.splice(existingIndex, 1);
  PLAY_QUEUE.splice(PLAY_INDEX + 1, 0, trackId);
  updateQueueSummary();
}

function queueCollectionNext(trackIds) {
  const ids = (trackIds || []).filter(Boolean);
  if (!ids.length) return;
  if (!PLAY_QUEUE.length || PLAY_INDEX < 0) {
    PLAY_QUEUE = ids.slice();
    PLAY_INDEX = 0;
    playFromQueue(0);
    return;
  }
  const remaining = PLAY_QUEUE.filter((id) => !ids.includes(id));
  const before = remaining.slice(0, PLAY_INDEX + 1);
  const after = remaining.slice(PLAY_INDEX + 1);
  PLAY_QUEUE = before.concat(ids, after);
  updateQueueSummary();
}

function renderTrackRows(items, title, meta = '') {
  el('contentTitle').textContent = title;
  el('contentMeta').textContent = meta;
  const actionsHost = el('contentActions');
  actionsHost.innerHTML = '';
  if (items.length) {
    const playNextBtn = document.createElement('button');
    playNextBtn.className = 'ghost-btn';
    playNextBtn.textContent = 'Play next';
    playNextBtn.onclick = () => queueCollectionNext(items.map((track) => track.id));
    actionsHost.appendChild(playNextBtn);
  }
  const box = el('tracksContainer');
  box.innerHTML = '';
  setCurrentViewTracks(items);

  items.forEach((track) => {
    const row = document.createElement('div');
    row.className = `track-row${currentQueueTrackId() === track.id ? ' active' : ''}`;
    row.oncontextmenu = (event) => rowContextMenu(track, event);

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = SELECTED.has(track.id);
    cb.onchange = () => {
      if (cb.checked) SELECTED.add(track.id);
      else SELECTED.delete(track.id);
      syncSelectionUI();
    };

    const main = document.createElement('div');
    main.className = 'track-main';
    main.onclick = () => playTrackById(track.id, items);
    main.innerHTML = `
      <div class="track-title" title="${esc(track.title)}">${esc(track.title)}</div>
      <div class="track-sub" title="${esc(track.artist || 'Unknown Artist')}">${esc(track.artist || 'Unknown Artist')}</div>
      <div class="track-subline">
        <span title="${esc(track.album || 'Unknown')}">${esc(track.album || 'Unknown')}</span>
        <span>${esc(track.duration_text || '0:00')}</span>
        ${track.year ? `<span>${esc(track.year)}</span>` : ''}
        ${track.folder ? `<span title="${esc(track.folder)}">${esc(track.folder)}</span>` : ''}
      </div>
    `;

    const actions = document.createElement('div');
    actions.className = 'track-actions';
    const menuBtn = document.createElement('button');
    menuBtn.className = 'track-menu-btn';
    menuBtn.textContent = '⋯';
    menuBtn.onclick = (event) => rowContextMenu(track, event);
    actions.appendChild(menuBtn);

    row.append(cb, main, actions);
    box.appendChild(row);
  });
}

function renderItemCards(title, meta, items, typeKey) {
  el('contentTitle').textContent = title;
  el('contentMeta').textContent = meta;
  el('contentActions').innerHTML = '';
  const box = el('tracksContainer');
  box.innerHTML = '';
  const grid = document.createElement('div');
  grid.className = 'item-grid';
  const prefixes = { playlists: 'playlist:', artists: 'artist:', albums: 'album:', folders: 'folder:' };

  items.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'item-card';
    card.onclick = () => { VIEW = prefixes[typeKey] + item.name; renderCurrentView(); };
    card.innerHTML = `<div class="item-card-title" title="${esc(item.name)}">${esc(item.name)}</div><div class="item-card-meta">${item.count} song(s)</div>`;
    const actions = document.createElement('div');
    actions.className = 'item-card-actions';

    const openBtn = document.createElement('button');
    openBtn.className = 'ghost-btn';
    openBtn.textContent = 'Open';
    openBtn.onclick = (event) => {
      event.stopPropagation();
      VIEW = `${prefixes[typeKey]}${item.name}`;
      renderCurrentView();
    };
    actions.appendChild(openBtn);

    const playNextBtn = document.createElement('button');
    playNextBtn.className = 'ghost-btn';
    playNextBtn.textContent = 'Play next';
    playNextBtn.onclick = (event) => {
      event.stopPropagation();
      queueCollectionNext(item.tracks || []);
    };
    actions.appendChild(playNextBtn);

    if (typeKey === 'playlists') {
      const manageBtn = document.createElement('button');
      manageBtn.className = 'ghost-btn';
      manageBtn.textContent = 'Manage';
      manageBtn.onclick = (event) => {
        event.stopPropagation();
        openManagePlaylistModal(item.name);
      };
      actions.appendChild(manageBtn);
    }
    card.appendChild(actions);
    grid.appendChild(card);
  });
  box.appendChild(grid);
}

function renderCurrentView() {
  document.querySelectorAll('.nav-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === VIEW.split(':')[0]));

  if (VIEW === 'all') {
    const items = filterTracks(allTracks());
    return renderTrackRows(items, 'All Songs', `${items.length} track(s)`);
  }
  if (VIEW === 'playlists') return renderItemCards('Playlists', `${(LIBRARY?.playlists || []).length} playlist(s)`, (LIBRARY?.playlists || []).slice().sort((a, b) => a.name.localeCompare(b.name)), 'playlists');
  if (VIEW === 'artists') return renderItemCards('Artists', `${(LIBRARY?.artists || []).length} artist(s)`, (LIBRARY?.artists || []).slice().sort((a, b) => a.name.localeCompare(b.name)), 'artists');
  if (VIEW === 'albums') return renderItemCards('Albums', `${(LIBRARY?.albums || []).length} album(s)`, (LIBRARY?.albums || []).slice().sort((a, b) => a.name.localeCompare(b.name)), 'albums');
  if (VIEW === 'folders') return renderItemCards('Folders', `${(LIBRARY?.folders || []).length} folder(s)`, (LIBRARY?.folders || []).slice().sort((a, b) => a.name.localeCompare(b.name)), 'folders');

  if (VIEW.startsWith('playlist:')) {
    const name = VIEW.slice('playlist:'.length);
    const playlist = (LIBRARY?.playlists || []).find((entry) => entry.name === name);
    return renderTrackRows(filterTracks(tracksByIds(playlist?.tracks || [])), name, `${playlist?.count || 0} track(s)`);
  }
  if (VIEW.startsWith('artist:')) {
    const name = VIEW.slice('artist:'.length);
    const artist = (LIBRARY?.artists || []).find((entry) => entry.name === name);
    return renderTrackRows(filterTracks(tracksByIds(artist?.tracks || [])), name, `${artist?.count || 0} track(s)`);
  }
  if (VIEW.startsWith('album:')) {
    const name = VIEW.slice('album:'.length);
    const album = (LIBRARY?.albums || []).find((entry) => entry.name === name);
    return renderTrackRows(filterTracks(tracksByIds(album?.tracks || [])), name, `${album?.count || 0} track(s)`);
  }
  if (VIEW.startsWith('folder:')) {
    const name = VIEW.slice('folder:'.length);
    const folder = (LIBRARY?.folders || []).find((entry) => entry.name === name);
    return renderTrackRows(filterTracks(tracksByIds(folder?.tracks || [])), name, `${folder?.count || 0} track(s)`);
  }
}

function rowContextMenu(track, event) {
  event.preventDefault();
  openContextMenu(event.clientX, event.clientY, [
    { action: 'play-next', label: 'Play next', handler: () => queueTrackNext(track.id) },
    { action: 'add-playlist', label: 'Add to playlist', handler: () => openPlaylistModal([track.id]) },
    { action: 'add-folder', label: 'Add to folder', handler: () => openFolderModal([track.id]) },
    { action: 'go-album', label: 'Go to album', handler: () => { VIEW = `album:${track.album || 'Unknown'}`; renderCurrentView(); } },
  ]);
}

function playTrackById(id, sourceTracks = null) {
  const source = sourceTracks || getCurrentViewTracks();
  if (source.length) setQueueFromTracks(source, id, SHUFFLE_ENABLED);
  else {
    PLAY_QUEUE = [id];
    PLAY_INDEX = 0;
  }
  playFromQueue(Math.max(0, PLAY_QUEUE.indexOf(id)));
}

function playFromQueue(index) {
  if (!PLAY_QUEUE.length || index < 0 || index >= PLAY_QUEUE.length) return;
  resetCrossfadeSchedule();
  CROSSFADE_IN_PROGRESS = false;
  PLAY_INDEX = index;
  const track = currentQueueTrack();
  if (!track) return;
  const current = activeAudio();
  const standby = standbyAudio();
  stopAudio(standby);
  loadTrackOnPlayer(track, current, { autoplay: true, volume: 1 }).finally(() => {
    syncPlaybackButton();
    applyNowPlaying(track);
    renderCurrentView();
    scheduleCrossfade();
  });
}

function stopPlaybackUi() {
  resetCrossfadeSchedule();
  CROSSFADE_IN_PROGRESS = false;
  stopAudio(activeAudio());
  stopAudio(standbyAudio());
  syncPlaybackButton();
}

function immediateAdvance() {
  const plan = computeNextPlaybackPlan();
  if (!plan) {
    stopPlaybackUi();
    return false;
  }
  if (plan.sameTrack) {
    const player = activeAudio();
    player.currentTime = 0;
    player.play().catch(() => {});
    syncPlaybackButton();
    scheduleCrossfade();
    return true;
  }
  applyPlaybackPlan(plan);
  playFromQueue(plan.index);
  return true;
}

function scheduleCrossfade() {
  resetCrossfadeSchedule();
  const settings = LIBRARY?.settings || {};
  const player = activeAudio();
  const trackId = currentQueueTrackId();
  if (!trackId) return;
  const plan = computeNextPlaybackPlan();
  const seconds = Number(settings.crossfade_seconds || 0);
  const duration = Number(player.duration || 0);
  if (!settings.crossfade_enabled || seconds <= 0 || !duration || !plan || plan.sameTrack) return;
  LAST_SCHEDULED_TRACK_ID = trackId;
  const ms = Math.max(0, ((duration - player.currentTime) - seconds) * 1000);
  CROSSFADE_TIMER = window.setTimeout(() => {
    if (currentQueueTrackId() !== trackId) return;
    startCrossfade(plan, seconds);
  }, ms);
}

function startCrossfade(plan, seconds) {
  if (CROSSFADE_IN_PROGRESS || !plan || plan.sameTrack) return;
  const nextTrack = trackMap().get(plan.nextId);
  if (!nextTrack) return;
  CROSSFADE_IN_PROGRESS = true;
  resetCrossfadeSchedule();

  const current = activeAudio();
  const next = standbyAudio();
  const nextKey = standbyAudioKey();
  next.volume = 0;
  loadTrackOnPlayer(nextTrack, next, { autoplay: true, volume: 0 }).then((started) => {
    if (!started) {
      CROSSFADE_IN_PROGRESS = false;
      immediateAdvance();
      return;
    }
    applyNowPlaying(nextTrack);
    applyPlaybackPlan(plan);
    renderCurrentView();

    const steps = Math.max(1, Math.round(seconds * 12));
    let count = 0;
    const currentStart = Math.max(0, current.volume || 1);
    const timer = window.setInterval(() => {
      count += 1;
      const ratio = count / steps;
      current.volume = Math.max(0, currentStart * (1 - ratio));
      next.volume = Math.min(1, ratio);
      if (count >= steps) {
        window.clearInterval(timer);
        try { current.pause(); } catch (_) {}
        current.volume = 1;
        current.removeAttribute('src');
        current.load();
        ACTIVE_AUDIO_KEY = nextKey;
        next.volume = 1;
        CROSSFADE_IN_PROGRESS = false;
        syncPlaybackButton();
        scheduleCrossfade();
      }
    }, Math.max(40, (seconds * 1000) / steps));
  });
}

async function loadLibrary() {
  const res = await fetch('/api/library', { cache: 'no-store' });
  LIBRARY = await res.json();
  renderSavedPlaylists();
  syncSelectionUI();
  syncToggleButtons();
  renderCurrentView();
  applyNowPlaying(currentQueueTrack());
}

function openSidebar() {
  el('sidebar').classList.add('open');
  el('mobileOverlay').classList.remove('hidden');
}

function closeSidebar() {
  el('sidebar').classList.remove('open');
  el('mobileOverlay').classList.add('hidden');
}

function bindAudioEvents(player) {
  player.addEventListener('timeupdate', () => {
    if (player !== activeAudio()) return;
    el('currentTime').textContent = fmtTime(player.currentTime || 0);
    el('totalTime').textContent = fmtTime(player.duration || 0);
    el('seekBar').value = player.duration ? String((player.currentTime / player.duration) * 100) : '0';
  });

  player.addEventListener('loadedmetadata', () => {
    if (player !== activeAudio()) return;
    el('totalTime').textContent = fmtTime(player.duration || 0);
    scheduleCrossfade();
  });

  player.addEventListener('ended', () => {
    if (player !== activeAudio() || CROSSFADE_IN_PROGRESS) return;
    immediateAdvance();
  });

  player.addEventListener('play', () => {
    if (player === activeAudio()) syncPlaybackButton();
  });

  player.addEventListener('pause', () => {
    if (player === activeAudio() && !CROSSFADE_IN_PROGRESS) syncPlaybackButton();
  });
}

window.addEventListener('DOMContentLoaded', () => {
  bindAudioEvents(el('audioPlayerA'));
  bindAudioEvents(el('audioPlayerB'));
  loadLibrary();

  document.addEventListener('click', (event) => {
    if (!event.target.closest('#contextMenu') && !event.target.closest('.track-menu-btn') && !event.target.closest('#nowMoreBtn')) closeContextMenu();
  });
  document.addEventListener('contextmenu', (event) => {
    if (!event.target.closest('.track-row')) closeContextMenu();
  });

  el('menuToggle').onclick = openSidebar;
  el('closeSidebarBtn').onclick = closeSidebar;
  el('mobileOverlay').onclick = closeSidebar;
  document.querySelectorAll('.nav-btn').forEach((btn) => {
    btn.onclick = () => {
      VIEW = btn.dataset.view;
      renderCurrentView();
      closeSidebar();
    };
  });

  el('searchInput').addEventListener('input', (event) => {
    FILTER_VALUE = event.target.value || '';
    syncSearchClear();
    renderCurrentView();
  });
  el('clearSearchBtn').onclick = () => {
    FILTER_VALUE = '';
    el('searchInput').value = '';
    syncSearchClear();
    renderCurrentView();
  };

  el('refreshBtn').onclick = loadLibrary;
  el('shuffleAllBtn').onclick = () => {
    const items = getCurrentViewTracks();
    if (!items.length) return;
    setQueueFromTracks(items, null, true);
    playFromQueue(0);
  };
  el('openSettingsBtn').onclick = openSettingsModal;
  el('saveSettingsBtn').onclick = saveSettings;
  el('closeSettingsModalBtn').onclick = closeSettingsModal;
  el('crossfadeSeconds').oninput = (event) => { el('crossfadeSecondsLabel').textContent = `${event.target.value}s`; };

  el('clearSelectionBtn').onclick = () => {
    SELECTED.clear();
    syncSelectionUI();
    renderCurrentView();
  };
  el('addToPlaylistBtn').onclick = () => openPlaylistModal();
  el('addToFolderBtn').onclick = () => openFolderModal();
  el('createPlaylistBtn').onclick = createPlaylistAndAdd;
  el('createFolderBtn').onclick = createFolderAndAdd;
  el('closePlaylistModalBtn').onclick = closePlaylistModal;
  el('closeFolderModalBtn').onclick = closeFolderModal;
  el('closeManagePlaylistModalBtn').onclick = closeManagePlaylistModal;
  el('closeDuplicateModalBtn').onclick = closeDuplicateModal;
  el('duplicateSkipBtn').onclick = closeDuplicateModal;
  el('duplicateAddBtn').onclick = () => {
    const handler = DUPLICATE_MODAL_STATE?.onAddAnyway;
    closeDuplicateModal();
    if (handler) handler();
  };

  document.querySelectorAll('[data-close]').forEach((node) => {
    node.onclick = () => {
      if (node.dataset.close === 'playlist') closePlaylistModal();
      if (node.dataset.close === 'folder') closeFolderModal();
      if (node.dataset.close === 'manage-playlist') closeManagePlaylistModal();
      if (node.dataset.close === 'settings') closeSettingsModal();
      if (node.dataset.close === 'duplicate') closeDuplicateModal();
    };
  });

  el('renamePlaylistBtn').onclick = renamePlaylist;
  el('deletePlaylistBtn').onclick = deletePlaylist;

  el('playPauseBtn').onclick = () => {
    const player = activeAudio();
    if (!player.src && PLAY_QUEUE.length) {
      playFromQueue(PLAY_INDEX >= 0 ? PLAY_INDEX : 0);
      return;
    }
    if (player.paused) player.play().catch(() => {});
    else player.pause();
  };
  el('nextBtn').onclick = immediateAdvance;
  el('prevBtn').onclick = () => {
    if (!PLAY_QUEUE.length) return;
    if (REPEAT === 'one') {
      activeAudio().currentTime = 0;
      activeAudio().play().catch(() => {});
      return;
    }
    const prev = PLAY_INDEX > 0 ? PLAY_INDEX - 1 : (REPEAT === 'all' ? PLAY_QUEUE.length - 1 : 0);
    playFromQueue(prev);
  };
  el('shuffleToggleBtn').onclick = () => {
    SHUFFLE_ENABLED = !SHUFFLE_ENABLED;
    syncToggleButtons();
  };
  el('repeatToggleBtn').onclick = () => {
    REPEAT = REPEAT === 'off' ? 'all' : (REPEAT === 'all' ? 'one' : 'off');
    syncToggleButtons();
  };
  el('nowMoreBtn').onclick = (event) => {
    const track = currentQueueTrack();
    if (!track) return;
    openContextMenu(event.clientX, event.clientY, [
      { action: 'np-play-next', label: 'Play next', handler: () => queueTrackNext(track.id) },
      { action: 'np-add-playlist', label: 'Add to playlist', handler: () => openPlaylistModal([track.id]) },
      { action: 'np-add-folder', label: 'Add to folder', handler: () => openFolderModal([track.id]) },
      { action: 'np-go-album', label: 'Go to album', handler: () => { VIEW = `album:${track.album || 'Unknown'}`; renderCurrentView(); } },
    ]);
  };

  el('seekBar').addEventListener('input', (event) => {
    const player = activeAudio();
    if (!player.duration) return;
    player.currentTime = (Number(event.target.value || 0) / 100) * player.duration;
    scheduleCrossfade();
  });
});
