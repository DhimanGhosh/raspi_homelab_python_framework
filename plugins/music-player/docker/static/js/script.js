let LIBRARY = null;
let VIEW = "all";
let FILTER_VALUE = "";
let SELECTED = new Set();

let CURRENT_VIEW_TRACKS = [];
let PLAY_QUEUE = [];
let PLAY_INDEX = -1;
let REPEAT = "off";
let MANAGE_PLAYLIST_NAME = null;

const audio = () => document.getElementById("audioPlayer");
const el = (id) => document.getElementById(id);

function fmtTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function closeSidebar() {
  el("sidebar").classList.remove("open");
  el("mobileOverlay").classList.add("hidden");
}

function openSidebar() {
  el("sidebar").classList.add("open");
  el("mobileOverlay").classList.remove("hidden");
}

async function loadLibrary() {
  const res = await fetch("/api/library");
  LIBRARY = await res.json();
  renderSidebarPlaylists();
  renderCurrentView();
}

function allTracks() {
  return (LIBRARY?.tracks || []).slice();
}

function trackMap() {
  return new Map(allTracks().map((track) => [track.id, track]));
}

function tracksByIds(ids) {
  const map = trackMap();
  return ids.map((id) => map.get(id)).filter(Boolean);
}

function filterTracks(items) {
  const q = FILTER_VALUE.trim().toLowerCase();
  if (!q) return items;
  return items.filter((track) =>
    `${track.title} ${track.artist} ${track.folder} ${track.filename}`.toLowerCase().includes(q),
  );
}

function shuffleArray(arr) {
  const copy = arr.slice();
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

function setCurrentViewTracks(items) {
  CURRENT_VIEW_TRACKS = items.slice();
}

function currentQueueTrack() {
  if (PLAY_INDEX < 0 || PLAY_INDEX >= PLAY_QUEUE.length) return null;
  return PLAY_QUEUE[PLAY_INDEX];
}

function setQueueFromTracks(items, startTrackId = null, shuffle = false) {
  const ids = items.map((track) => track.id);
  PLAY_QUEUE = shuffle ? shuffleArray(ids) : ids.slice();
  if (!PLAY_QUEUE.length) {
    PLAY_INDEX = -1;
    return;
  }
  PLAY_INDEX = startTrackId && PLAY_QUEUE.includes(startTrackId) ? PLAY_QUEUE.indexOf(startTrackId) : 0;
}

function renderSidebarPlaylists() {
  const box = el("savedPlaylists");
  box.innerHTML = "";
  (LIBRARY?.playlists || []).forEach((playlist) => {
    const btn = document.createElement("button");
    btn.textContent = `${playlist.name} (${playlist.count})`;
    btn.onclick = () => {
      VIEW = "playlist:" + playlist.name;
      renderCurrentView();
      closeSidebar();
    };
    box.appendChild(btn);
  });
}

function renderTrackRows(items, title, meta = "") {
  el("contentTitle").textContent = title;
  el("contentMeta").textContent = meta;
  const box = el("tracksContainer");
  box.innerHTML = "";
  setCurrentViewTracks(items);

  items.forEach((track) => {
    const row = document.createElement("div");
    row.className = "track-row" + (currentQueueTrack() === track.id ? " active" : "");

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = SELECTED.has(track.id);
    cb.onchange = () => {
      if (cb.checked) SELECTED.add(track.id);
      else SELECTED.delete(track.id);
      syncSelectionUI();
    };

    const main = document.createElement("div");
    main.className = "track-main";
    main.onclick = () => playTrackById(track.id, items);
    main.innerHTML = `
      <div class="track-title">${escapeHtml(track.title)}</div>
      <div class="track-sub">${escapeHtml(track.artist || "Unknown Artist")}</div>
      ${track.folder ? `<div class="track-folder">${escapeHtml(track.folder)}</div>` : ""}
    `;

    const actions = document.createElement("div");
    const addBtn = document.createElement("button");
    addBtn.className = "track-mini-btn";
    addBtn.textContent = "Add";
    addBtn.onclick = (event) => {
      event.stopPropagation();
      SELECTED.clear();
      SELECTED.add(track.id);
      syncSelectionUI();
      openPlaylistModal();
    };
    actions.appendChild(addBtn);

    row.append(cb, main, actions);
    box.appendChild(row);
  });
}

function renderItemCards(title, meta, items, typeKey) {
  el("contentTitle").textContent = title;
  el("contentMeta").textContent = meta;
  const box = el("tracksContainer");
  box.innerHTML = "";
  setCurrentViewTracks([]);

  if (typeKey === "playlists" && LIBRARY?.playlist_note) {
    const note = document.createElement("div");
    note.className = "note-box";
    note.textContent = LIBRARY.playlist_note;
    box.appendChild(note);
  }

  if (typeKey === "playlists") {
    const create = document.createElement("div");
    create.className = "item-card";
    create.innerHTML = `<div class="item-card-title">Create playlist</div><div class="item-card-meta">Create a new playlist and add selected songs later.</div>`;
    create.onclick = () => openPlaylistModal();
    box.appendChild(create);
  }

  if (typeKey === "folders") {
    const create = document.createElement("div");
    create.className = "item-card";
    create.innerHTML = `<div class="item-card-title">Create folder</div><div class="item-card-meta">Create a custom folder and move selected songs into it.</div>`;
    create.onclick = () => openFolderModal();
    box.appendChild(create);
  }

  const grid = document.createElement("div");
  grid.className = "item-grid";

  items.forEach((item) => {
    const card = document.createElement("div");
    card.className = "item-card";
    const prefixMap = {
      playlists: "playlist:",
      artists: "artist:",
      folders: "folder:",
    };

    if (typeKey === "playlists") {
      card.innerHTML = `
        <div class="item-card-title">${escapeHtml(item.name)}</div>
        <div class="item-card-meta">${item.count} song(s)</div>
        <div class="item-card-actions">
          <button class="playlist-chip open-btn">Open</button>
          <button class="manage-btn">Manage</button>
        </div>
      `;
      card.querySelector(".open-btn").onclick = () => {
        VIEW = prefixMap[typeKey] + item.name;
        renderCurrentView();
      };
      card.querySelector(".manage-btn").onclick = () => openManagePlaylistModal(item.name);
    } else {
      card.innerHTML = `
        <div class="item-card-title">${escapeHtml(item.name)}</div>
        <div class="item-card-meta">${item.count} song(s)</div>
      `;
      card.onclick = () => {
        VIEW = prefixMap[typeKey] + item.name;
        renderCurrentView();
      };
    }

    grid.appendChild(card);
  });

  box.appendChild(grid);
}

function renderCurrentView() {
  document.querySelectorAll(".nav-btn").forEach((btn) => btn.classList.remove("active"));
  const topView = VIEW.split(":")[0];
  const active = document.querySelector(`[data-view="${topView}"]`);
  if (active) active.classList.add("active");

  if (VIEW === "all") {
    const items = filterTracks(allTracks());
    return renderTrackRows(items, "All Songs", `${items.length} track(s)`);
  }

  if (VIEW === "playlists") {
    const items = (LIBRARY?.playlists || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    return renderItemCards("Playlists", `${items.length} playlist(s)`, items, "playlists");
  }

  if (VIEW === "artists") {
    const items = (LIBRARY?.artists || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    return renderItemCards("Artists", `${items.length} artist(s)`, items, "artists");
  }

  if (VIEW === "folders") {
    const items = (LIBRARY?.folders || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    return renderItemCards("Folders", `${items.length} folder(s)`, items, "folders");
  }

  if (VIEW.startsWith("playlist:")) {
    const name = VIEW.slice("playlist:".length);
    const playlist = (LIBRARY?.playlists || []).find((entry) => entry.name === name);
    const items = filterTracks(tracksByIds(playlist?.tracks || []));
    return renderTrackRows(items, name, `${items.length} track(s)`);
  }

  if (VIEW.startsWith("artist:")) {
    const name = VIEW.slice("artist:".length);
    const artist = (LIBRARY?.artists || []).find((entry) => entry.name === name);
    const items = filterTracks(tracksByIds(artist?.tracks || []));
    return renderTrackRows(items, name, `${items.length} track(s)`);
  }

  if (VIEW.startsWith("folder:")) {
    const name = VIEW.slice("folder:".length);
    const folder = (LIBRARY?.folders || []).find((entry) => entry.name === name);
    const items = filterTracks(tracksByIds(folder?.tracks || []));
    return renderTrackRows(items, name, `${items.length} track(s)`);
  }
}

function syncSelectionUI() {
  const count = SELECTED.size;
  el("selectedCount").textContent = `${count} selected`;
  const actions = document.querySelector(".selection-actions");
  if (actions) actions.classList.toggle("hidden", count === 0);
  el("addToPlaylistBtn").disabled = count === 0;
  el("addToFolderBtn").disabled = count === 0;
  el("clearSelectionBtn").disabled = count === 0;
}

function updateNowPlaying(track) {
  el("nowTitle").textContent = track ? track.title : "Nothing playing";
  el("nowArtist").textContent = track ? (track.artist || "Unknown Artist") : "Select a track";
}

function playTrackById(id, sourceTracks = null) {
  const source = sourceTracks || CURRENT_VIEW_TRACKS || [];
  if (source.length) {
    setQueueFromTracks(source, id, false);
  } else if (!PLAY_QUEUE.includes(id)) {
    PLAY_QUEUE = [id];
    PLAY_INDEX = 0;
  } else {
    PLAY_INDEX = PLAY_QUEUE.indexOf(id);
  }

  const track = trackMap().get(id);
  if (!track) return;

  const player = audio();
  player.src = track.stream_url;
  player.play().catch(() => {});
  updateNowPlaying(track);
  el("playPauseBtn").textContent = "⏸";
  renderCurrentView();
}

function playFromQueue(index) {
  if (!PLAY_QUEUE.length || index < 0 || index >= PLAY_QUEUE.length) return;
  PLAY_INDEX = index;
  const track = trackMap().get(PLAY_QUEUE[PLAY_INDEX]);
  if (!track) return;
  const player = audio();
  player.src = track.stream_url;
  player.play().catch(() => {});
  updateNowPlaying(track);
  el("playPauseBtn").textContent = "⏸";
  renderCurrentView();
}

function playNext() {
  if (!PLAY_QUEUE.length) return;
  let nextIndex = PLAY_INDEX + 1;
  if (nextIndex >= PLAY_QUEUE.length) {
    if (REPEAT === "all") nextIndex = 0;
    else {
      el("playPauseBtn").textContent = "▶";
      return;
    }
  }
  playFromQueue(nextIndex);
}

function playPrev() {
  if (!PLAY_QUEUE.length) return;
  let prevIndex = PLAY_INDEX - 1;
  if (prevIndex < 0) {
    if (REPEAT === "all") prevIndex = PLAY_QUEUE.length - 1;
    else prevIndex = 0;
  }
  playFromQueue(prevIndex);
}

function getLastPlaylist() {
  try {
    return localStorage.getItem("music_player_last_playlist") || "";
  } catch {
    return "";
  }
}

function setLastPlaylist(name) {
  try {
    localStorage.setItem("music_player_last_playlist", name);
  } catch {}
}

function getSelectedIds() {
  return Array.from(SELECTED);
}

async function addToPlaylist(name) {
  const ids = getSelectedIds();
  if (!name || !ids.length) return;

  const res = await fetch("/api/playlists", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, track_ids: ids}),
  });

  if (!res.ok) {
    alert("Failed to add to playlist");
    return;
  }

  setLastPlaylist(name);
  closePlaylistModal();
  await loadLibrary();
  SELECTED.clear();
  syncSelectionUI();
  renderCurrentView();
}

function renderPlaylistModal() {
  const list = el("playlistList");
  list.innerHTML = "";

  (LIBRARY?.playlists || []).forEach((playlist) => {
    const btn = document.createElement("button");
    btn.className = "playlist-chip";
    btn.textContent = `${playlist.name} (${playlist.count})`;
    btn.onclick = () => addToPlaylist(playlist.name);
    list.appendChild(btn);
  });

  const last = getLastPlaylist();
  const lastBlock = el("lastPlaylistBlock");
  const lastBtn = el("lastPlaylistBtn");
  if (last) {
    lastBlock.classList.remove("hidden");
    lastBtn.textContent = last;
    lastBtn.onclick = () => addToPlaylist(last);
  } else {
    lastBlock.classList.add("hidden");
  }
}

function openPlaylistModal() {
  renderPlaylistModal();
  el("playlistModal").classList.remove("hidden");
}

function closePlaylistModal() {
  el("playlistModal").classList.add("hidden");
  el("newPlaylistName").value = "";
}

function openManagePlaylistModal(name) {
  MANAGE_PLAYLIST_NAME = name;
  el("renamePlaylistName").value = name;
  el("managePlaylistModal").classList.remove("hidden");
}

function closeManagePlaylistModal() {
  MANAGE_PLAYLIST_NAME = null;
  el("managePlaylistModal").classList.add("hidden");
  el("renamePlaylistName").value = "";
}

async function renamePlaylist() {
  if (!MANAGE_PLAYLIST_NAME) return;
  const newName = el("renamePlaylistName").value.trim();
  if (!newName) return;

  const res = await fetch("/api/playlists/rename", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({old_name: MANAGE_PLAYLIST_NAME, new_name: newName}),
  });

  if (!res.ok) {
    alert("Failed to rename playlist");
    return;
  }

  closeManagePlaylistModal();
  await loadLibrary();
  VIEW = "playlists";
  renderCurrentView();
}

async function deletePlaylist() {
  if (!MANAGE_PLAYLIST_NAME) return;
  const res = await fetch("/api/playlists/delete", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name: MANAGE_PLAYLIST_NAME}),
  });

  if (!res.ok) {
    alert("Failed to delete playlist");
    return;
  }

  closeManagePlaylistModal();
  await loadLibrary();
  VIEW = "playlists";
  renderCurrentView();
}

async function addToFolder(name) {
  const ids = getSelectedIds();
  if (!name || !ids.length) return;

  const res = await fetch("/api/folders/add", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, track_ids: ids}),
  });

  if (!res.ok) {
    alert("Failed to add to folder");
    return;
  }

  closeFolderModal();
  await loadLibrary();
  SELECTED.clear();
  syncSelectionUI();
  VIEW = "folder:" + name;
  renderCurrentView();
}

function renderFolderModal() {
  const list = el("folderList");
  list.innerHTML = "";
  (LIBRARY?.folders || []).forEach((folder) => {
    const btn = document.createElement("button");
    btn.className = "playlist-chip";
    btn.textContent = `${folder.name} (${folder.count})`;
    btn.onclick = () => addToFolder(folder.name);
    list.appendChild(btn);
  });
}

function openFolderModal() {
  renderFolderModal();
  el("folderModal").classList.remove("hidden");
}

function closeFolderModal() {
  el("folderModal").classList.add("hidden");
  el("newFolderName").value = "";
}

window.addEventListener("DOMContentLoaded", () => {
  document.addEventListener("contextmenu", (event) => event.preventDefault());

  loadLibrary();

  el("menuToggle").onclick = openSidebar;
  el("closeSidebarBtn").onclick = closeSidebar;
  el("mobileOverlay").onclick = closeSidebar;

  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.onclick = () => {
      VIEW = btn.dataset.view;
      renderCurrentView();
      closeSidebar();
    };
  });

  el("searchInput").addEventListener("input", (event) => {
    FILTER_VALUE = event.target.value || "";
    renderCurrentView();
  });

  el("refreshBtn").onclick = loadLibrary;

  el("shuffleAllBtn").onclick = () => {
    const items = CURRENT_VIEW_TRACKS.slice();
    if (!items.length) return;
    setQueueFromTracks(items, null, true);
    playFromQueue(0);
  };

  el("clearSelectionBtn").onclick = () => {
    SELECTED.clear();
    syncSelectionUI();
    renderCurrentView();
  };

  el("addToPlaylistBtn").onclick = openPlaylistModal;
  el("addToFolderBtn").onclick = openFolderModal;

  el("closePlaylistModalBtn").onclick = closePlaylistModal;
  el("closeFolderModalBtn").onclick = closeFolderModal;
  el("closeManagePlaylistModalBtn").onclick = closeManagePlaylistModal;
  document.querySelectorAll("[data-close]").forEach((node) => {
    node.onclick = () => {
      if (node.dataset.close === "playlist") closePlaylistModal();
      if (node.dataset.close === "folder") closeFolderModal();
      if (node.dataset.close === "manage-playlist") closeManagePlaylistModal();
    };
  });

  el("createPlaylistBtn").onclick = () => {
    const name = el("newPlaylistName").value.trim();
    if (name) addToPlaylist(name);
  };

  el("renamePlaylistBtn").onclick = renamePlaylist;
  el("deletePlaylistBtn").onclick = deletePlaylist;

  el("createFolderBtn").onclick = async () => {
    const name = el("newFolderName").value.trim();
    if (!name) return;

    const createRes = await fetch("/api/folders/create", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name}),
    });

    if (!createRes.ok) {
      alert("Failed to create folder");
      return;
    }

    if (SELECTED.size) {
      addToFolder(name);
    } else {
      closeFolderModal();
      await loadLibrary();
      VIEW = "folders";
      renderCurrentView();
    }
  };

  el("playPauseBtn").onclick = () => {
    const player = audio();
    if (!player.src && PLAY_QUEUE.length) {
      if (PLAY_INDEX < 0) PLAY_INDEX = 0;
      return playFromQueue(PLAY_INDEX);
    }
    if (player.paused) {
      player.play().catch(() => {});
      el("playPauseBtn").textContent = "⏸";
    } else {
      player.pause();
      el("playPauseBtn").textContent = "▶";
    }
  };

  el("nextBtn").onclick = playNext;
  el("prevBtn").onclick = playPrev;

  el("shuffleToggleBtn").onclick = () => {
    const currentTrackId = currentQueueTrack();
    if (!CURRENT_VIEW_TRACKS.length) return;
    setQueueFromTracks(CURRENT_VIEW_TRACKS, currentTrackId, true);
    if (PLAY_INDEX < 0 && PLAY_QUEUE.length) PLAY_INDEX = 0;
  };

  el("repeatToggleBtn").onclick = () => {
    REPEAT = REPEAT === "off" ? "all" : "off";
    el("repeatToggleBtn").textContent = REPEAT === "all" ? "All" : "Off";
  };

  const player = audio();
  player.addEventListener("timeupdate", () => {
    const current = player.currentTime || 0;
    const total = player.duration || 0;
    el("currentTime").textContent = fmtTime(current);
    el("totalTime").textContent = fmtTime(total);
    el("seekBar").value = total ? String((current / total) * 100) : "0";
  });

  player.addEventListener("ended", playNext);
  player.addEventListener("loadedmetadata", () => {
    el("totalTime").textContent = fmtTime(player.duration || 0);
  });

  el("seekBar").addEventListener("input", (event) => {
    const total = player.duration || 0;
    if (!total) return;
    player.currentTime = (Number(event.target.value) / 100) * total;
  });

  syncSelectionUI();
});
