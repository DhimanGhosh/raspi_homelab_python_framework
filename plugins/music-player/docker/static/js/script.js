let LIBRARY = null;
let VIEW = "all";
let FILTER_VALUE = "";
let SELECTED = new Set();
let CURRENT_LIST = [];
let CURRENT_INDEX = -1;
let SHUFFLE = false;
let REPEAT = "off";

const audio = () => document.getElementById("audioPlayer");
const el = (id) => document.getElementById(id);

function fmtTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

async function loadLibrary() {
  const res = await fetch("/api/library");
  LIBRARY = await res.json();
  renderSidebarPlaylists();
  renderCurrentView();
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

function allTracks() {
  return (LIBRARY?.tracks || []).slice();
}

function tracksByIds(ids) {
  const map = new Map(allTracks().map((track) => [track.id, track]));
  return ids.map((id) => map.get(id)).filter(Boolean);
}

function filterTracks(items) {
  const q = FILTER_VALUE.trim().toLowerCase();
  if (!q) return items;
  return items.filter((track) =>
    `${track.title} ${track.artist} ${track.folder} ${track.filename}`.toLowerCase().includes(q),
  );
}

function setCurrentList(items) {
  CURRENT_LIST = items.slice();
  if (CURRENT_INDEX >= CURRENT_LIST.length) CURRENT_INDEX = -1;
}

function renderSidebarPlaylists() {
  const box = el("savedPlaylists");
  box.innerHTML = "";
  const playlists = LIBRARY?.playlists || [];
  playlists.forEach((playlist) => {
    const btn = document.createElement("button");
    btn.textContent = `${playlist.name} (${playlist.count})`;
    btn.onclick = () => {
      VIEW = "playlist:" + playlist.name;
      renderCurrentView();
    };
    box.appendChild(btn);
  });
}

function renderTrackRows(items, title, meta = "") {
  el("contentTitle").textContent = title;
  el("contentMeta").textContent = meta;
  const box = el("tracksContainer");
  box.innerHTML = "";
  setCurrentList(items);

  items.forEach((track, idx) => {
    const row = document.createElement("div");
    row.className = "track-row" + (idx === CURRENT_INDEX ? " active" : "");

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
    main.onclick = () => playTrackById(track.id);
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

function renderItemCards(title, meta, items, type) {
  el("contentTitle").textContent = title;
  el("contentMeta").textContent = meta;
  const box = el("tracksContainer");
  box.innerHTML = "";
  setCurrentList([]);

  if (type === "playlists" && LIBRARY?.playlist_note) {
    const note = document.createElement("div");
    note.className = "note-box";
    note.textContent = LIBRARY.playlist_note;
    box.appendChild(note);
  }

  if (type === "playlists") {
    const create = document.createElement("div");
    create.className = "item-card";
    create.innerHTML = `<div class="item-card-title">Create playlist</div><div class="item-card-meta">Create a new playlist and add selected songs later.</div>`;
    create.onclick = () => openPlaylistModal();
    box.appendChild(create);
  }

  if (type === "folders") {
    const create = document.createElement("div");
    create.className = "item-card";
    create.innerHTML = `<div class="item-card-title">Create folder</div><div class="item-card-meta">Create a custom folder and move selected songs into it.</div>`;
    create.onclick = () => openFolderModal();
    box.appendChild(create);
  }

  const grid = document.createElement("div");
  grid.className = "item-grid";

  items.forEach((item) => {
    const card = document.createElement("button");
    card.className = "item-card item-chip";
    card.innerHTML = `<div class="item-card-title">${escapeHtml(item.name)}</div><div class="item-card-meta">${item.count} song(s)</div>`;
    card.onclick = () => {
      VIEW = `${type}:${item.name}`;
      renderCurrentView();
    };
    grid.appendChild(card);
  });

  box.appendChild(grid);
}

function renderCurrentView() {
  document.querySelectorAll(".nav-btn").forEach((btn) => btn.classList.remove("active"));
  const active = document.querySelector(`[data-view="${VIEW.split(":")[0]}"]`);
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
    return renderItemCards("Artists", `${items.length} artist(s)`, items, "artist");
  }

  if (VIEW === "folders") {
    const items = (LIBRARY?.folders || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    return renderItemCards("Folders", `${items.length} folder(s)`, items, "folder");
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

function playTrackById(id) {
  const idx = CURRENT_LIST.findIndex((track) => track.id === id);
  if (idx === -1) return;
  CURRENT_INDEX = idx;
  const track = CURRENT_LIST[idx];
  const player = audio();
  player.src = track.stream_url;
  player.play().catch(() => {});
  el("nowTitle").textContent = track.title;
  el("nowArtist").textContent = track.artist || "Unknown Artist";
  el("playPauseBtn").textContent = "⏸";
  renderCurrentView();
}

function playNext() {
  if (!CURRENT_LIST.length) return;
  if (SHUFFLE) {
    CURRENT_INDEX = Math.floor(Math.random() * CURRENT_LIST.length);
  } else {
    CURRENT_INDEX += 1;
    if (CURRENT_INDEX >= CURRENT_LIST.length) {
      if (REPEAT === "all") CURRENT_INDEX = 0;
      else {
        CURRENT_INDEX = CURRENT_LIST.length - 1;
        el("playPauseBtn").textContent = "▶";
        return;
      }
    }
  }
  playTrackById(CURRENT_LIST[CURRENT_INDEX].id);
}

function playPrev() {
  if (!CURRENT_LIST.length) return;
  CURRENT_INDEX = Math.max(0, CURRENT_INDEX - 1);
  playTrackById(CURRENT_LIST[CURRENT_INDEX].id);
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
  loadLibrary();

  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.onclick = () => {
      VIEW = btn.dataset.view;
      renderCurrentView();
    };
  });

  el("searchInput").addEventListener("input", (event) => {
    FILTER_VALUE = event.target.value || "";
    renderCurrentView();
  });

  el("refreshBtn").onclick = loadLibrary;

  el("shuffleAllBtn").onclick = () => {
    VIEW = "all";
    renderCurrentView();
    SHUFFLE = true;
    const items = filterTracks(allTracks());
    if (items.length) {
      setCurrentList(items);
      CURRENT_INDEX = Math.floor(Math.random() * items.length);
      playTrackById(items[CURRENT_INDEX].id);
    }
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
  document.querySelectorAll("[data-close]").forEach((node) => {
    node.onclick = () => {
      if (node.dataset.close === "playlist") closePlaylistModal();
      if (node.dataset.close === "folder") closeFolderModal();
    };
  });

  el("createPlaylistBtn").onclick = () => {
    const name = el("newPlaylistName").value.trim();
    if (name) addToPlaylist(name);
  };

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
    if (!player.src && CURRENT_LIST.length) {
      if (CURRENT_INDEX < 0) CURRENT_INDEX = 0;
      return playTrackById(CURRENT_LIST[CURRENT_INDEX].id);
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
    SHUFFLE = !SHUFFLE;
    el("shuffleToggleBtn").style.opacity = SHUFFLE ? "1" : "0.6";
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
