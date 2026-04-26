const $ = (id) => document.getElementById(id);
const audio = new Audio();
audio.preload = 'metadata';

const S = {
  lib: { app: {}, tracks: [], artists: [], albums: [], folders: [], playlists: [] },
  view: 'home',
  context: null,
  search: '',
  queue: [],
  index: -1,
  draggingSeek: false,
  repeat: 'off',
  shuffle: false,
  menuPayload: null,
  playlistPayload: null,
  duplicatePayload: null,
  metadataTrackId: null,
  artistImageArtist: null,
  dragTrackId: null,
  queueOriginIds: [],
  queueOriginLabel: 'Local library',
  queueOriginType: 'tracks',
  userInitiatedPlayback: false,
  booting: true,
  dragTouch: null,
};

function escapeHtml(text = '') {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function fmt(seconds = 0) {
  const total = Number.isFinite(seconds) ? Math.floor(seconds) : 0;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function shuffledIds(ids) {
  const arr = [...ids];
  for (let i = arr.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function uniqueIds(ids) {
  const seen = new Set();
  return ids.filter((id) => id && !seen.has(id) && seen.add(id));
}

function numericYear(track) {
  const value = parseInt(track?.year, 10);
  return Number.isFinite(value) ? value : null;
}

function averageYear(tracks) {
  const years = tracks.map(numericYear).filter(Boolean);
  if (!years.length) return null;
  return Math.round(years.reduce((a, b) => a + b, 0) / years.length);
}

function trackById(id) {
  return (S.lib.tracks || []).find((track) => track.id === id) || null;
}

function artMarkup(url, cls = 'fallback') {
  return url ? `<img src="${escapeHtml(url)}" alt="art" />` : `<span class="${cls}">♪</span>`;
}

function matchesSearch(value) {
  if (!S.search) return true;
  return String(value || '').toLowerCase().includes(S.search.toLowerCase());
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const message = data?.error || data?.message || `Request failed: ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

async function fileToDataUrl(file) {
  if (!file) return '';
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result || '');
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function openSidebar() {
  $('sidebar').classList.add('open');
  $('mobileOverlay').classList.remove('hidden');
}

function closeSidebar() {
  $('sidebar').classList.remove('open');
  $('mobileOverlay').classList.add('hidden');
}

function currentTrack() {
  return S.queue[S.index] || null;
}

function buildTrackList(trackIds) {
  return trackIds.map(trackById).filter(Boolean);
}

function currentContextLabel() {
  return S.queueOriginLabel || 'Local library';
}

function updateQueueOrigin(trackIds, label = 'Local library', type = 'tracks') {
  S.queueOriginIds = uniqueIds(trackIds);
  S.queueOriginLabel = label;
  S.queueOriginType = type;
}

function canAutoplay(autoplay) {
  // Only explicit playback controls are allowed to start audio.
  // Navigation clicks such as Home/Artists/Albums create browser user activation,
  // so relying on navigator.userActivation caused Home to navigate and start songs.
  return !!autoplay && !S.booting && S.userInitiatedPlayback === true;
}

function hardStopStartupPlayback() {
  try { audio.pause(); } catch (error) { }
  try { audio.removeAttribute('src'); audio.load(); } catch (error) { }
  S.queue = [];
  S.index = -1;
  S.autoQueueMode = false;
  S.userInitiatedPlayback = false;
}

function setQueue(trackIds, startIndex = 0, autoplay = true, contextLabel = 'Local library', options = {}) {
  // Navigation/render clicks must never create a current song.
  // Only an explicit playback action sets S.userInitiatedPlayback before calling setQueue().
  // This prevents Home / All Songs / refresh from selecting the first library item in
  // the footer or opening a huge queue while the player is still in "Nothing playing" state.
  if (autoplay && !S.userInitiatedPlayback) {
    return;
  }

  const ids = uniqueIds(trackIds);
  if (options.storeOrigin !== false) {
    updateQueueOrigin(ids, contextLabel, options.originType || S.context?.type || 'tracks');
  }
  S.autoQueueMode = !!options.autoQueueMode;
  S.queue = buildTrackList(ids);
  S.index = Math.max(0, Math.min(startIndex, Math.max(0, S.queue.length - 1)));
  $('nowPlayingContext').textContent = contextLabel;
  renderQueue();
  if (S.queue.length) loadCurrent(autoplay);
  else {
    audio.pause();
    audio.removeAttribute('src');
    updatePlayer();
  }
}


function insertPlayNext(trackIds) {
  const items = buildTrackList(uniqueIds(trackIds));
  if (!items.length) return;
  if (S.index < 0 || !S.queue.length) {
    S.queue = items;
    S.index = -1;
    renderQueue();
    updatePlayer();
    return;
  }
  const insertAt = S.index + 1;
  const idsInQueue = new Set(S.queue.map((track) => track.id));
  const deduped = items.filter((track) => !idsInQueue.has(track.id));
  S.queue.splice(insertAt, 0, ...deduped);
  renderQueue();
}

function appendToQueue(trackIds) {
  const items = buildTrackList(uniqueIds(trackIds));
  if (!items.length) return;
  if (S.index < 0 || !S.queue.length) {
    S.queue = items;
    S.index = -1;
    renderQueue();
    updatePlayer();
    return;
  }
  const idsInQueue = new Set(S.queue.map((track) => track.id));
  const deduped = items.filter((track) => !idsInQueue.has(track.id));
  S.queue.push(...deduped);
  renderQueue();
}

function removeQueueItem(trackId) {
  const idx = S.queue.findIndex((track) => track.id === trackId);
  if (idx < 0) return;
  const wasCurrent = idx === S.index;
  S.queue.splice(idx, 1);
  if (idx < S.index) S.index -= 1;
  if (S.index >= S.queue.length) S.index = S.queue.length - 1;
  renderQueue();
  if (!S.queue.length) {
    audio.pause();
    audio.removeAttribute('src');
  } else if (wasCurrent) {
    loadCurrent(true);
  }
  updatePlayer();
}

function smartYearCandidates() {
  const exclude = new Set(S.queue.map((track) => track.id));
  const baseTracks = S.queue.slice(Math.max(0, S.index - 9), S.index + 1);
  const center = averageYear(baseTracks) || numericYear(currentTrack()) || averageYear(S.lib.tracks);
  let candidates = S.lib.tracks.filter((track) => !exclude.has(track.id));
  if (center) {
    const within10 = candidates.filter((track) => {
      const y = numericYear(track);
      return y && Math.abs(y - center) <= 10;
    });
    if (within10.length >= 4) candidates = within10;
  }
  return shuffledIds(candidates.map((track) => track.id));
}

function appendSmartBatch(force = false) {
  if (S.repeat !== 'off') return false;
  const remainingAfterCurrent = S.queue.length - S.index - 1;
  if (!force && remainingAfterCurrent > 2) return false;
  const picks = smartYearCandidates().slice(0, 10);
  const tracks = buildTrackList(picks);
  if (!tracks.length) return false;
  S.queue.push(...tracks);
  S.autoQueueMode = true;
  renderQueue();
  return true;
}

function loadCurrent(autoplay = true) {
  const track = currentTrack();
  if (!track) return;
  audio.src = track.stream_url;
  audio.load();
  if (canAutoplay(autoplay)) audio.play().catch(() => null);
  updatePlayer();
  renderQueue();
}

function applyNowPlayingTheme(track) {
  const overlay = $('nowPlayingOverlay');
  if (!overlay) return;
  const fallback = 'linear-gradient(135deg, rgba(26,26,28,.96), rgba(5,5,5,.98))';
  if (!track?.art_url) {
    overlay.style.setProperty('--overlay-bg', fallback);
    return;
  }
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    try {
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      const size = 24;
      canvas.width = size;
      canvas.height = size;
      ctx.drawImage(img, 0, 0, size, size);
      const { data } = ctx.getImageData(0, 0, size, size);
      let r = 0; let g = 0; let b = 0; let count = 0;
      for (let i = 0; i < data.length; i += 16) {
        r += data[i];
        g += data[i + 1];
        b += data[i + 2];
        count += 1;
      }
      r = Math.max(18, Math.round(r / count));
      g = Math.max(8, Math.round(g / count));
      b = Math.max(10, Math.round(b / count));
      overlay.style.setProperty('--overlay-bg', `radial-gradient(circle at top left, rgba(${r},${g},${b},0.58), rgba(${Math.max(8, r - 65)},${Math.max(8, g - 65)},${Math.max(8, b - 65)},0.82) 40%, rgba(6,6,7,0.98) 100%)`);
    } catch (error) {
      overlay.style.setProperty('--overlay-bg', fallback);
    }
  };
  img.onerror = () => overlay.style.setProperty('--overlay-bg', fallback);
  img.src = track.art_url;
}

function updatePlayer() {
  const track = currentTrack();
  const title = track?.title || 'Nothing playing';
  const subtitle = track ? `${track.artist} · ${track.album}${track.year ? ` · ${track.year}` : ''}` : 'Select a track to start playback';
  $('playerTitle').textContent = title;
  $('playerSub').textContent = subtitle;
  $('overlayTitle').textContent = title;
  $('overlaySub').textContent = subtitle;
  const art = artMarkup(track?.art_url);
  $('playerArt').innerHTML = art;
  $('overlayArt').innerHTML = art;
  $('nowPlayingContext').textContent = currentContextLabel();
  applyNowPlayingTheme(track);
  const playing = !audio.paused && !!track;
  $('playPauseBtn').textContent = playing ? '❚❚' : '▶';
  $('overlayPlayPauseBtn').textContent = playing ? '❚❚' : '▶';
  const shuffleOutline = S.shuffle ? '1px solid rgba(111,149,255,.82)' : '';
  $('shuffleBtn').style.outline = shuffleOutline;
  $('overlayShuffleBtn').style.outline = shuffleOutline;
  const repeatLabel = S.repeat === 'off' ? 'Off' : S.repeat === 'all' ? '∞' : '1';
  $('repeatBtn').textContent = repeatLabel;
  $('overlayRepeatBtn').textContent = repeatLabel;
}

function togglePlayPause() {
  S.userInitiatedPlayback = true;
  if (!currentTrack() && S.lib.tracks.length) {
    setQueue(S.lib.tracks.map((track) => track.id), 0, true, 'All songs', { originType: 'tracks' });
    return;
  }
  if (audio.paused) audio.play().catch(() => null);
  else audio.pause();
  updatePlayer();
}

function nextTrack() {
  S.userInitiatedPlayback = true;
  if (!S.queue.length) return;
  if (S.index < S.queue.length - 1) {
    S.index += 1;
    loadCurrent();
    appendSmartBatch();
    return;
  }
  if (S.repeat === 'all') {
    if (S.queueOriginIds.length) {
      const ids = S.shuffle ? shuffledIds(S.queueOriginIds) : S.queueOriginIds;
      setQueue(ids, 0, true, currentContextLabel(), { originType: S.queueOriginType });
      return;
    }
    S.index = 0;
    loadCurrent();
    return;
  }
  if (appendSmartBatch(true) && S.index < S.queue.length - 1) {
    S.index += 1;
    loadCurrent();
  }
}

function prevTrack() {
  S.userInitiatedPlayback = true;
  if (!S.queue.length) return;
  if (audio.currentTime > 4) {
    audio.currentTime = 0;
    return;
  }
  if (S.index > 0) {
    S.index -= 1;
    loadCurrent();
  }
}

function toggleQueueSheet(open) {
  const sheet = $('overlayQueueSheet');
  const shouldOpen = open ?? !sheet.classList.contains('open');
  sheet.classList.toggle('open', shouldOpen);
}

function toggleNowPlaying(open) {
  const overlay = $('nowPlayingOverlay');
  const shouldOpen = open ?? overlay.classList.contains('hidden');
  overlay.classList.toggle('hidden', !shouldOpen);
  if (!shouldOpen) toggleQueueSheet(false);
  if (window.innerWidth >= 901 && shouldOpen) toggleQueueSheet(true);
}

function renderSidebarPlaylists() {
  const container = $('sidebarPlaylists');
  const current = S.context?.type === 'playlist' ? S.context.value : '';
  container.innerHTML = S.lib.playlists.length
    ? S.lib.playlists.map((playlist) => `
      <div class="sidebar-playlist-item ${playlist.name === current ? 'active' : ''}" data-playlist="${escapeHtml(playlist.name)}">
        ${escapeHtml(playlist.name)}
      </div>
    `).join('')
    : '<div class="muted">No playlists yet</div>';
  container.querySelectorAll('[data-playlist]').forEach((node) => {
    node.onclick = () => navigate('playlists', { type: 'playlist', value: node.dataset.playlist });
  });
}

function collectionActions(type, value, extra = {}) {
  const compactHome = extra.home === true;
  return `
    <div class="section-actions ${compactHome ? 'home-actions' : ''}">
      <button class="ghost-btn js-play-group" data-type="${type}" data-value="${escapeHtml(value)}">Play all</button>
      <button class="ghost-btn js-shuffle-group" data-type="${type}" data-value="${escapeHtml(value)}">Shuffle</button>
      ${compactHome ? '' : `<button class="ghost-btn js-playnext-group" data-type="${type}" data-value="${escapeHtml(value)}">Play next</button>`}
      ${compactHome ? '' : `<button class="ghost-btn js-addqueue-group" data-type="${type}" data-value="${escapeHtml(value)}">Add to queue</button>`}
      ${extra.menu ? `<button class="icon-btn js-open-group-menu" data-type="${type}" data-value="${escapeHtml(value)}">⋯</button>` : ''}
    </div>
  `;
}

function attachCollectionActionHandlers() {
  document.querySelectorAll('.js-play-group').forEach((btn) => {
    btn.onclick = () => {
      S.userInitiatedPlayback = true;
      const ids = resolveCollectionIds(btn.dataset.type, btn.dataset.value);
      setQueue(ids, 0, true, btn.dataset.value, { originType: btn.dataset.type });
    };
  });
  document.querySelectorAll('.js-shuffle-group').forEach((btn) => {
    btn.onclick = () => {
      S.userInitiatedPlayback = true;
      const originIds = resolveCollectionIds(btn.dataset.type, btn.dataset.value);
      setQueue(shuffledIds(originIds), 0, true, btn.dataset.value, { originType: btn.dataset.type });
    };
  });
  document.querySelectorAll('.js-playnext-group').forEach((btn) => insertPlayNext(resolveCollectionIds(btn.dataset.type, btn.dataset.value)));
  document.querySelectorAll('.js-addqueue-group').forEach((btn) => appendToQueue(resolveCollectionIds(btn.dataset.type, btn.dataset.value)));
  document.querySelectorAll('.js-open-group-menu').forEach((btn) => {
    btn.onclick = (event) => openGroupMenu(event.currentTarget, btn.dataset.type, btn.dataset.value);
  });
}

function resolveCollectionIds(type, value) {
  if (type === 'tracks') return S.lib.tracks.map((track) => track.id);
  const list = S.lib[type] || [];
  const item = list.find((entry) => entry.name === value);
  return item?.tracks || [];
}

function renderEntityCard(item, type) {
  const artUrl = item.art_url || item.image_url || null;
  const meta = type === 'albums' ? `${item.count} track(s)${item.artist ? ` · ${item.artist}` : ''}` : `${item.count} track(s)`;
  return `
    <article class="entity-card" data-open-type="${type}" data-open-value="${escapeHtml(item.name)}">
      <div class="entity-art">
        ${artMarkup(artUrl)}
        <button class="icon-btn card-menu-btn js-open-group-menu" data-type="${type}" data-value="${escapeHtml(item.name)}" type="button">⋯</button>
      </div>
      <div class="entity-title">${escapeHtml(item.name)}</div>
      <div class="entity-count">${escapeHtml(meta)}</div>
    </article>
  `;
}

function renderTrackRow(track, extra = {}) {
  return `
    <article class="row-card" data-track-id="${escapeHtml(track.id)}">
      <div class="row-art">${artMarkup(track.art_url)}</div>
      <div class="row-main">
        <div class="row-title">${escapeHtml(track.title)}</div>
        <div class="row-meta">${escapeHtml(track.artist)} · ${escapeHtml(track.album)}${track.year ? ` · ${escapeHtml(track.year)}` : ''}</div>
      </div>
      <div class="row-actions">
        ${extra.groupButtons ? `<button class="ghost-btn small js-playnext-single" data-track-id="${escapeHtml(track.id)}" type="button">Play next</button><button class="ghost-btn small js-addqueue-single" data-track-id="${escapeHtml(track.id)}" type="button">Add to queue</button>` : ''}
        <button class="icon-btn js-track-menu" data-track-id="${escapeHtml(track.id)}" type="button">⋯</button>
      </div>
    </article>
  `;
}

function renderHomeView() {
  const allTracks = S.lib.tracks.filter((track) => matchesSearch(`${track.title} ${track.artist} ${track.album} ${track.folder}`));
  const heroTracks = allTracks.slice(0, 8);
  const albums = S.lib.albums.filter((item) => matchesSearch(`${item.name} ${item.artist || ''}`)).slice(0, 6);
  const artists = S.lib.artists.filter((item) => matchesSearch(item.name)).slice(0, 6);
  $('contextHeader').classList.add('hidden');
  $('contentArea').innerHTML = `
    <section class="home-section">
      <div class="section-head">
        <div>
          <h2 class="section-title">Listen again</h2>
          <div class="section-sub">Local library picks</div>
        </div>
        ${collectionActions('tracks', 'All songs', { home: true })}
      </div>
      <div class="media-row">
        ${heroTracks.map((track) => `
          <article class="media-card" data-track-id="${escapeHtml(track.id)}">
            <div class="media-art">
              ${artMarkup(track.art_url)}
              <button class="play-overlay js-home-card-play" data-track-id="${escapeHtml(track.id)}" type="button" aria-label="Play ${escapeHtml(track.title)}">▶</button>
            </div>
            <div class="media-title">${escapeHtml(track.title)}</div>
            <div class="media-meta">${escapeHtml(track.artist)}</div>
          </article>
        `).join('') || '<div class="muted">No songs found</div>'}
      </div>
    </section>
    <section class="home-section">
      <div class="section-head"><div><h2 class="section-title">Albums</h2><div class="section-sub">${albums.length} shown</div></div></div>
      <div class="entity-grid compact">${albums.map((item) => renderEntityCard(item, 'albums')).join('')}</div>
    </section>
    <section class="home-section">
      <div class="section-head"><div><h2 class="section-title">Artists</h2><div class="section-sub">${artists.length} shown</div></div></div>
      <div class="entity-grid compact">${artists.map((item) => renderEntityCard(item, 'artists')).join('')}</div>
    </section>
  `;
}

function renderCollectionGrid(type, title) {
  const items = S.lib[type].filter((item) => matchesSearch(type === 'artists' ? item.name : `${item.name} ${item.artist || ''}`));
  $('contextHeader').classList.remove('hidden');
  $('contextHeader').textContent = `${items.length} shown`;
  $('contentArea').innerHTML = `
    <section class="home-section">
      <div class="section-head"><div><h2 class="section-title">${title}</h2><div class="section-sub">${items.length} shown</div></div></div>
      <div class="entity-grid compact">${items.map((item) => renderEntityCard(item, type)).join('') || '<div class="muted">No results found</div>'}</div>
    </section>
  `;
}

function renderArtistHeader(artistName, trackIds) {
  const artist = S.lib.artists.find((item) => item.name === artistName);
  return `
    <div class="artist-header-card">
      <div class="artist-header-art">${artMarkup(artist?.image_url || artist?.art_url)}</div>
      <div class="artist-header-copy">
        <div class="artist-header-title">${escapeHtml(artistName)}</div>
        <div class="artist-header-meta">${trackIds.length} track(s)</div>
      </div>
      <div class="artist-actions">${collectionActions('artists', artistName, { menu: true })}</div>
    </div>
  `;
}

function renderTrackCollection(title, subtitle, trackIds, context) {
  const tracks = buildTrackList(trackIds).filter((track) => matchesSearch(`${track.title} ${track.artist} ${track.album} ${track.folder}`));
  $('contextHeader').classList.remove('hidden');
  $('contextHeader').textContent = subtitle || `${tracks.length} track(s)`;
  const showTopActions = context.type !== 'artists';
  $('contentArea').innerHTML = `
    <section class="list-section">
      <div class="section-head ${showTopActions ? '' : 'artist-page-head'}">
        <div>
          <h2 class="section-title">${escapeHtml(title)}</h2>
          <div class="section-sub">${escapeHtml(subtitle || `${tracks.length} track(s)`)}</div>
        </div>
        ${showTopActions ? collectionActions(context.type, context.value, { menu: context.type === 'playlists' || context.type === 'folders' || context.type === 'albums' }) : ''}
      </div>
      ${context.type === 'artists' ? renderArtistHeader(context.value, trackIds) : ''}
      <div class="list-section">${tracks.map((track) => renderTrackRow(track, { groupButtons: false })).join('') || '<div class="muted">No tracks found</div>'}</div>
    </section>
  `;
}

function renderPlaylistsView() {
  if (S.context?.type === 'playlist') {
    const playlist = S.lib.playlists.find((item) => item.name === S.context.value);
    renderTrackCollection(playlist?.name || 'Playlist', `${playlist?.count || 0} track(s)`, playlist?.tracks || [], { type: 'playlists', value: playlist?.name || S.context.value });
    return;
  }
  renderCollectionGrid('playlists', 'Playlists');
}

function renderView() {
  document.querySelectorAll('.nav-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === S.view));
  renderSidebarPlaylists();
  renderChips();

  if (S.view === 'home') renderHomeView();
  if (S.view === 'all') renderTrackCollection('All Songs', `${S.lib.tracks.filter((track) => matchesSearch(`${track.title} ${track.artist} ${track.album}`)).length} shown`, S.lib.tracks.map((track) => track.id), { type: 'tracks', value: 'All songs' });
  if (S.view === 'artists') {
    if (S.context?.type === 'artists') renderTrackCollection(S.context.value, `${resolveCollectionIds('artists', S.context.value).length} track(s)`, resolveCollectionIds('artists', S.context.value), S.context);
    else renderCollectionGrid('artists', 'Artists');
  }
  if (S.view === 'albums') {
    if (S.context?.type === 'albums') renderTrackCollection(S.context.value, `${resolveCollectionIds('albums', S.context.value).length} track(s)`, resolveCollectionIds('albums', S.context.value), S.context);
    else renderCollectionGrid('albums', 'Albums');
  }
  if (S.view === 'folders') {
    if (S.context?.type === 'folders') renderTrackCollection(S.context.value, `${resolveCollectionIds('folders', S.context.value).length} track(s)`, resolveCollectionIds('folders', S.context.value), S.context);
    else renderCollectionGrid('folders', 'Folders');
  }
  if (S.view === 'playlists') renderPlaylistsView();

  attachGeneralHandlers();
  attachCollectionActionHandlers();
}

function renderChips() {
  // Category chips are intentionally removed on both desktop and mobile.
  // The same navigation is available from the sidebar/hamburger menu.
  const chipsBar = $('chipsBar');
  if (!chipsBar) return;
  chipsBar.innerHTML = '';
  chipsBar.classList.add('hidden');
}

function navigate(view, context = null) {
  S.view = view;
  S.context = context;
  renderView();
  closeSidebar();
}

function attachGeneralHandlers() {
  document.querySelectorAll('[data-open-type]').forEach((node) => {
    node.onclick = (event) => {
      if (event.target.closest('.js-open-group-menu')) return;
      navigate(node.dataset.openType, { type: node.dataset.openType, value: node.dataset.openValue });
    };
  });
  document.querySelectorAll('[data-track-id]').forEach((node) => {
    if (node.classList.contains('queue-item')) return;
    node.onclick = (event) => {
      if (event.target.closest('.js-track-menu') || event.target.closest('.js-playnext-single') || event.target.closest('.js-addqueue-single')) return;
      const homeCard = node.classList.contains('media-card') || !!node.closest('.media-card');
      const listRoot = node.closest('.list-section') || node.closest('.media-row');
      const sourceNodes = Array.from(listRoot?.querySelectorAll('[data-track-id]') || []).filter((item) => !item.classList.contains('queue-item') && !item.classList.contains('js-home-card-play'));
      const ids = uniqueIds(sourceNodes.map((item) => item.dataset.trackId));
      const index = ids.indexOf(node.dataset.trackId);
      S.userInitiatedPlayback = true;
      setQueue(ids.length ? ids : [node.dataset.trackId], Math.max(0, index), true, homeCard ? 'Local library' : (S.context?.value || currentContextLabel()), { originType: homeCard ? 'tracks' : (S.context?.type || 'tracks') });
    };
  });
  document.querySelectorAll('.js-track-menu').forEach((btn) => {
    btn.onclick = (event) => {
      event.stopPropagation();
      openTrackMenu(event.currentTarget, btn.dataset.trackId);
    };
  });
  document.querySelectorAll('.js-playnext-single').forEach((btn) => btn.onclick = (event) => { event.stopPropagation(); insertPlayNext([btn.dataset.trackId]); });
  document.querySelectorAll('.js-addqueue-single').forEach((btn) => btn.onclick = (event) => { event.stopPropagation(); appendToQueue([btn.dataset.trackId]); });
}

function openMenuAt(anchor, html) {
  const menu = $('trackMenu');
  menu.innerHTML = html;
  menu.classList.remove('hidden');
  requestAnimationFrame(() => {
    const rect = anchor.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const left = Math.min(window.innerWidth - menuRect.width - 12, Math.max(12, rect.right - menuRect.width));
    const top = Math.min(window.innerHeight - menuRect.height - 12, Math.max(12, rect.bottom + 6));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  });
}

function closeMenu() {
  $('trackMenu').classList.add('hidden');
  S.menuPayload = null;
}

function openTrackMenu(anchor, trackId) {
  const track = trackById(trackId);
  if (!track) return;
  S.menuPayload = { kind: 'track', trackId };
  openMenuAt(anchor, `
    <button class="menu-item" data-menu-action="playnext">Play next</button>
    <button class="menu-item" data-menu-action="addqueue">Add to queue</button>
    <button class="menu-item" data-menu-action="playlist">Add to playlist</button>
    <button class="menu-item" data-menu-action="metadata">Edit metadata</button>
  `);
  bindMenuActions();
}

function openQueueItemMenu(anchor, trackId) {
  S.menuPayload = { kind: 'queue', trackId };
  openMenuAt(anchor, `
    <button class="menu-item" data-menu-action="remove-queue">Remove from queue</button>
    <button class="menu-item" data-menu-action="playlist">Add to playlist</button>
    <button class="menu-item" data-menu-action="metadata">Edit metadata</button>
  `);
  bindMenuActions();
}

function openGroupMenu(anchor, type, value) {
  S.menuPayload = { kind: 'group', type, value };
  const isArtist = type === 'artists';
  openMenuAt(anchor, `
    <button class="menu-item" data-menu-action="playall">Play all</button>
    <button class="menu-item" data-menu-action="shuffle">Shuffle</button>
    <button class="menu-item" data-menu-action="playnext-group">Play next</button>
    <button class="menu-item" data-menu-action="addqueue-group">Add to queue</button>
    <button class="menu-item" data-menu-action="playlist-group">Add to playlist</button>
    ${isArtist ? `<button class="menu-item" data-menu-action="artist-image">Add artist image link / upload</button>` : ''}
  `);
  bindMenuActions();
}

function bindMenuActions() {
  $('trackMenu').querySelectorAll('[data-menu-action]').forEach((btn) => {
    btn.onclick = async (event) => {
      event.stopPropagation();
      const action = btn.dataset.menuAction;
      const payload = S.menuPayload;
      closeMenu();
      if (!payload) return;

      if (payload.kind === 'track') {
        if (action === 'playnext') insertPlayNext([payload.trackId]);
        if (action === 'addqueue') appendToQueue([payload.trackId]);
        if (action === 'playlist') openPlaylistModal([payload.trackId]);
        if (action === 'metadata') await openMetadataModal(payload.trackId);
      }
      if (payload.kind === 'queue') {
        if (action === 'remove-queue') removeQueueItem(payload.trackId);
        if (action === 'playlist') openPlaylistModal([payload.trackId]);
        if (action === 'metadata') await openMetadataModal(payload.trackId);
      }
      if (payload.kind === 'group') {
        const ids = resolveCollectionIds(payload.type, payload.value);
        if (action === 'playall') { S.userInitiatedPlayback = true; setQueue(ids, 0, true, payload.value, { originType: payload.type }); }
        if (action === 'shuffle') { S.userInitiatedPlayback = true; setQueue(shuffledIds(ids), 0, true, payload.value, { originType: payload.type }); }
        if (action === 'playnext-group') insertPlayNext(ids);
        if (action === 'addqueue-group') appendToQueue(ids);
        if (action === 'playlist-group') openPlaylistModal(ids);
        if (action === 'artist-image') openArtistImageModal(payload.value);
      }
    };
  });
}

function openPlaylistModal(trackIds) {
  S.playlistPayload = { trackIds };
  $('playlistNameInput').value = '';
  renderPlaylistSelectionList();
  $('playlistModal').classList.remove('hidden');
}

function renderPlaylistSelectionList() {
  const container = $('playlistSelectionList');
  const playlists = S.lib.playlists || [];
  container.innerHTML = playlists.length
    ? playlists.map((playlist) => `
      <label class="playlist-option">
        <input type="checkbox" value="${escapeHtml(playlist.name)}" />
        <div>
          <div>${escapeHtml(playlist.name)}</div>
          <small>${playlist.count} track(s)</small>
        </div>
        <span>+</span>
      </label>
    `).join('')
    : '<div class="muted">No playlists yet. Create one below.</div>';
}

async function savePlaylistSelection(force = false) {
  const selected = Array.from(document.querySelectorAll('#playlistSelectionList input:checked')).map((input) => input.value);
  const newName = $('playlistNameInput').value.trim();
  if (!selected.length && !newName) {
    alert('Select an existing playlist or create a new one.');
    return;
  }
  if (newName) {
    await api('/api/playlists', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: newName, tracks: [] }) });
    selected.push(newName);
  }
  let duplicateInfo = null;
  for (const name of selected) {
    try {
      await api('/api/playlists/add-tracks', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, track_ids: S.playlistPayload.trackIds, force }),
      });
    } catch (error) {
      if (error.status === 409) { duplicateInfo = { name, payload: error.payload }; break; }
      throw error;
    }
  }
  if (duplicateInfo && !force) {
    S.duplicatePayload = duplicateInfo;
    $('duplicateText').textContent = `${duplicateInfo.payload.duplicate_count} duplicate track(s) already exist in "${duplicateInfo.name}".`;
    $('duplicateModal').classList.remove('hidden');
    return;
  }
  $('playlistModal').classList.add('hidden');
  $('duplicateModal').classList.add('hidden');
  await loadLibrary();
}

async function openMetadataModal(trackId) {
  const meta = await api(`/api/metadata/${encodeURIComponent(trackId).replace(/%2F/g, '/')}`);
  S.metadataTrackId = trackId;
  $('metaTitle').value = meta.title || '';
  $('metaArtist').value = meta.artist || '';
  $('metaAlbum').value = meta.album || '';
  $('metaYear').value = meta.year || '';
  $('metaArtLink').value = '';
  $('metaArtFile').value = '';
  $('metadataModal').classList.remove('hidden');
}

async function saveMetadata() {
  if (!S.metadataTrackId) return;
  const previousTrackId = S.metadataTrackId;
  const fileData = await fileToDataUrl($('metaArtFile').files[0]);
  const payload = {
    title: $('metaTitle').value.trim(),
    artist: $('metaArtist').value.trim(),
    album: $('metaAlbum').value.trim(),
    year: $('metaYear').value.trim(),
    art_link: $('metaArtLink').value.trim(),
    art_upload_data: fileData,
  };
  const result = await api(`/api/metadata/${encodeURIComponent(S.metadataTrackId).replace(/%2F/g, '/')}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  $('metadataModal').classList.add('hidden');
  await loadLibrary();
  const queuePos = S.queue.findIndex((track) => track.id === previousTrackId);
  const updated = trackById(result.track_id);
  if (queuePos >= 0 && updated) {
    S.queue[queuePos] = updated;
    if (S.index === queuePos) { S.index = queuePos; loadCurrent(false); }
  }
  S.metadataTrackId = result.track_id;
}

function openArtistImageModal(artistName) {
  S.artistImageArtist = artistName;
  $('artistImageLink').value = '';
  $('artistImageFile').value = '';
  $('artistImageModal').classList.remove('hidden');
}

async function saveArtistImage() {
  if (!S.artistImageArtist) return;
  const uploadData = await fileToDataUrl($('artistImageFile').files[0]);
  await api(`/api/artist-image/${encodeURIComponent(S.artistImageArtist)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_link: $('artistImageLink').value.trim(), upload_data: uploadData }),
  });
  $('artistImageModal').classList.add('hidden');
  await loadLibrary();
}

function renderQueue() {
  $('queueSubtitle').textContent = `${S.queue.length} song(s)`;
  $('queueList').innerHTML = S.queue.map((track, index) => `
    <article class="queue-item ${index === S.index ? 'active' : ''}" data-queue-index="${index}" data-track-id="${escapeHtml(track.id)}" draggable="true">
      <div class="queue-item-art">${artMarkup(track.art_url)}</div>
      <div class="queue-body">
        <div class="queue-item-title">${escapeHtml(track.title)}</div>
        <div class="queue-item-sub">${escapeHtml(track.artist)} · ${escapeHtml(track.album)} · ${fmt(track.duration)}</div>
      </div>
      <div class="queue-actions">
        <button class="icon-btn small-icon js-queue-item-menu" data-track-id="${escapeHtml(track.id)}" type="button">⋯</button>
        <span class="drag-handle" title="Drag to reorder">☰</span>
      </div>
    </article>
  `).join('') || '<div class="muted">Queue is empty</div>';

  $('queueList').querySelectorAll('.queue-item').forEach((item) => {
    item.onclick = (event) => {
      if (event.target.closest('.drag-handle') || event.target.closest('.js-queue-item-menu')) return;
      S.userInitiatedPlayback = true;
      S.index = Number(item.dataset.queueIndex);
      loadCurrent();
    };
    item.addEventListener('dragstart', () => { S.dragTrackId = item.dataset.trackId; item.style.opacity = '.5'; });
    item.addEventListener('dragend', () => { S.dragTrackId = null; item.style.opacity = '1'; item.classList.remove('drag-over'); });
    item.addEventListener('dragover', (event) => { event.preventDefault(); item.classList.add('drag-over'); });
    item.addEventListener('dragleave', () => item.classList.remove('drag-over'));
    item.addEventListener('drop', (event) => { event.preventDefault(); item.classList.remove('drag-over'); reorderQueue(S.dragTrackId, item.dataset.trackId); });
  });

  $('queueList').querySelectorAll('.js-queue-item-menu').forEach((btn) => {
    btn.onclick = (event) => { event.stopPropagation(); openQueueItemMenu(event.currentTarget, btn.dataset.trackId); };
  });

  enableTouchReorder();
}

function reorderQueue(sourceId, targetId) {
  if (!sourceId || !targetId || sourceId === targetId) return;
  const from = S.queue.findIndex((track) => track.id === sourceId);
  const to = S.queue.findIndex((track) => track.id === targetId);
  if (from < 0 || to < 0) return;
  const [item] = S.queue.splice(from, 1);
  S.queue.splice(to, 0, item);
  if (S.index === from) S.index = to;
  else if (from < S.index && to >= S.index) S.index -= 1;
  else if (from > S.index && to <= S.index) S.index += 1;
  renderQueue();
}

function enableTouchReorder() {
  let active = null;
  $('queueList').querySelectorAll('.queue-item').forEach((item) => {
    item.addEventListener('touchstart', (event) => {
      const handle = event.target.closest('.drag-handle');
      if (!handle) return;
      const touch = event.touches[0];
      active = { node: item, sourceId: item.dataset.trackId, startY: touch.clientY, lastY: touch.clientY };
      item.classList.add('touch-dragging');
    }, { passive: true });

    item.addEventListener('touchmove', (event) => {
      if (!active || active.node !== item) return;
      const touch = event.touches[0];
      active.lastY = touch.clientY;
      item.style.transform = `translateY(${touch.clientY - active.startY}px) scale(1.02)`;
      const target = document.elementFromPoint(touch.clientX, touch.clientY)?.closest('.queue-item');
      $('queueList').querySelectorAll('.queue-item').forEach((node) => node.classList.remove('drag-over'));
      if (target && target !== item) target.classList.add('drag-over');
    }, { passive: true });

    item.addEventListener('touchend', (event) => {
      if (!active || active.node !== item) return;
      const touch = event.changedTouches[0];
      const target = document.elementFromPoint(touch.clientX, touch.clientY)?.closest('.queue-item');
      item.classList.remove('touch-dragging');
      item.style.transform = '';
      $('queueList').querySelectorAll('.queue-item').forEach((node) => node.classList.remove('drag-over'));
      if (target) reorderQueue(active.sourceId, target.dataset.trackId);
      active = null;
    }, { passive: true });
  });
}

async function loadLibrary() {
  const previousQueueIds = S.queue.map((track) => track.id);
  const currentId = currentTrack()?.id || null;
  const data = await api('/api/library');
  S.lib = data;
  $('logoVersion').textContent = `v${data.app.version}`;
  if (previousQueueIds.length) {
    S.queue = previousQueueIds.map(trackById).filter(Boolean);
    S.index = currentId ? S.queue.findIndex((track) => track.id === currentId) : S.index;
    if (S.index < 0 && S.queue.length) S.index = 0;
  }
  updatePlayer();
  renderView();
  renderQueue();
}

async function initialLoad() {
  hardStopStartupPlayback();
  await loadLibrary();
  hardStopStartupPlayback();
  S.booting = false;
  updatePlayer();
  renderQueue();
}

window.addEventListener('pagehide', hardStopStartupPlayback);
window.addEventListener('beforeunload', hardStopStartupPlayback);

function bindSwipeGestures() {
  const overlay = $('nowPlayingOverlay');
  const handle = $('overlayQueueHandle');
  const sheetHead = $('queueSheetHead');
  const mainPanel = $('nowPlayingPanel');
  let startY = null;
  let mode = null;

  function onStart(event) {
    startY = event.touches[0].clientY;
    if (event.currentTarget === handle) mode = 'queue';
    else if (event.currentTarget === sheetHead) mode = 'sheet';
    else mode = 'overlay';
  }

  function onEnd(event) {
    if (startY == null) return;
    const endY = event.changedTouches[0].clientY;
    const delta = endY - startY;
    if (mode === 'queue' && delta < -40) toggleQueueSheet(true);
    if (mode === 'sheet' && delta > 40) toggleQueueSheet(false);
    if (mode === 'overlay' && delta > 70 && !$('overlayQueueSheet').classList.contains('open')) toggleNowPlaying(false);
    startY = null;
    mode = null;
  }

  [handle, sheetHead, mainPanel].forEach((node) => {
    node.addEventListener('touchstart', onStart, { passive: true });
    node.addEventListener('touchend', onEnd, { passive: true });
  });
}

function wireGlobalEvents() {
  document.querySelectorAll('.nav-btn').forEach((btn) => { btn.onclick = () => navigate(btn.dataset.view); });
  $('menuToggleBtn').onclick = openSidebar;
  $('sidebarCloseBtn').onclick = closeSidebar;
  $('mobileOverlay').onclick = closeSidebar;
  $('searchInput').addEventListener('input', (event) => {
    S.search = event.target.value.trim();
    $('clearSearchBtn').classList.toggle('hidden', !S.search);
    renderView();
  });
  $('clearSearchBtn').onclick = () => {
    $('searchInput').value = '';
    S.search = '';
    $('clearSearchBtn').classList.add('hidden');
    renderView();
  };
  $('refreshBtn').onclick = () => loadLibrary();
  $('shuffleAllBtn').onclick = () => { S.userInitiatedPlayback = true; setQueue(shuffledIds(S.lib.tracks.map((track) => track.id)), 0, true, 'All songs', { originType: 'tracks' }); };
  $('newPlaylistBtn').onclick = () => openPlaylistModal([]);
  $('closePlaylistModalBtn').onclick = () => $('playlistModal').classList.add('hidden');
  $('createPlaylistConfirmBtn').onclick = () => savePlaylistSelection(false);
  $('closeDuplicateModalBtn').onclick = () => $('duplicateModal').classList.add('hidden');
  $('skipDuplicatesBtn').onclick = async () => { $('duplicateModal').classList.add('hidden'); $('playlistModal').classList.add('hidden'); await loadLibrary(); };
  $('addDuplicatesBtn').onclick = async () => savePlaylistSelection(true);
  $('closeMetadataModalBtn').onclick = () => $('metadataModal').classList.add('hidden');
  $('saveMetadataBtn').onclick = () => saveMetadata().catch((error) => alert(error.message));
  $('closeArtistImageModalBtn').onclick = () => $('artistImageModal').classList.add('hidden');
  $('saveArtistImageBtn').onclick = () => saveArtistImage().catch((error) => alert(error.message));

  document.querySelectorAll('.modal').forEach((modal) => {
    modal.addEventListener('click', (event) => {
      if (event.target.classList.contains('modal-backdrop')) modal.classList.add('hidden');
    });
  });

  document.addEventListener('click', (event) => {
    if (!$('trackMenu').contains(event.target) && !event.target.closest('.js-track-menu') && !event.target.closest('.js-open-group-menu') && !event.target.closest('.js-queue-item-menu') && event.target !== $('overlayOptionsBtn')) closeMenu();
  });

  $('miniPlayerInfo').onclick = () => { if (currentTrack()) toggleNowPlaying(true); };
  $('miniPlayerGrabber').onclick = () => { if (currentTrack()) toggleNowPlaying(true); };
  $('closeNowPlayingBtn').onclick = () => toggleNowPlaying(false);
  $('queueBtn').onclick = () => { if (currentTrack()) toggleNowPlaying(true); };
  $('overlayQueueHandle').onclick = () => toggleQueueSheet();
  $('overlayOptionsBtn').onclick = (event) => {
    const track = currentTrack();
    if (track) openQueueItemMenu(event.currentTarget, track.id);
  };

  $('playPauseBtn').onclick = togglePlayPause;
  $('overlayPlayPauseBtn').onclick = togglePlayPause;
  $('nextBtn').onclick = nextTrack;
  $('overlayNextBtn').onclick = nextTrack;
  $('prevBtn').onclick = prevTrack;
  $('overlayPrevBtn').onclick = prevTrack;
  $('shuffleBtn').onclick = $('overlayShuffleBtn').onclick = () => { S.shuffle = !S.shuffle; updatePlayer(); };
  $('repeatBtn').onclick = $('overlayRepeatBtn').onclick = () => { S.repeat = S.repeat === 'off' ? 'all' : S.repeat === 'all' ? 'one' : 'off'; updatePlayer(); };

  const bindSeek = (range, current, total) => {
    range.addEventListener('input', () => {
      S.draggingSeek = true;
      if (audio.duration) current.textContent = fmt((Number(range.value) / 1000) * audio.duration);
    });
    range.addEventListener('change', () => {
      if (audio.duration) audio.currentTime = (Number(range.value) / 1000) * audio.duration;
      S.draggingSeek = false;
    });
  };
  bindSeek($('seekRange'), $('timeCurrent'), $('timeTotal'));
  bindSeek($('overlaySeekRange'), $('overlayTimeCurrent'), $('overlayTimeTotal'));

  audio.addEventListener('timeupdate', () => {
    if (!S.draggingSeek && audio.duration) {
      const value = Math.floor((audio.currentTime / audio.duration) * 1000);
      $('seekRange').value = value;
      $('overlaySeekRange').value = value;
    }
    $('timeCurrent').textContent = fmt(audio.currentTime);
    $('overlayTimeCurrent').textContent = fmt(audio.currentTime);
    $('timeTotal').textContent = fmt(audio.duration || 0);
    $('overlayTimeTotal').textContent = fmt(audio.duration || 0);
  });

  audio.addEventListener('play', updatePlayer);
  audio.addEventListener('pause', updatePlayer);
  audio.addEventListener('ended', () => {
    if (S.repeat === 'one') { audio.currentTime = 0; audio.play().catch(() => null); return; }
    if (S.repeat === 'all') {
      nextTrack();
      return;
    }
    if (S.shuffle && S.queueOriginIds.length) {
      const shuffled = shuffledIds(S.queueOriginIds);
      setQueue(shuffled, 0, true, currentContextLabel(), { originType: S.queueOriginType });
      return;
    }
    nextTrack();
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth >= 901 && !$('nowPlayingOverlay').classList.contains('hidden')) toggleQueueSheet(true);
    if (window.innerWidth >= 901) closeSidebar();
  });

  bindSwipeGestures();
}

wireGlobalEvents();
initialLoad().catch((error) => {
  $('contentArea').innerHTML = `<div class="muted">Failed to load library: ${escapeHtml(error.message)}</div>`;
});
