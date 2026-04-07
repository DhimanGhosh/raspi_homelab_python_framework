const state = { jobs: [], selectedJobId: null, ws: null };

function byId(id) { return document.getElementById(id); }

async function getJson(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
}

function renderSummary(summary) {
    const installedCount = Object.keys(summary.installed_plugins || {}).length;
    byId("summaryLine").textContent =
        `Installed plugins: ${installedCount} • Jobs: ${(summary.jobs || []).length} • FQDN: ${summary.tailscale_fqdn}`;
}

function pluginCard(pluginId, plugin) {
    const publicUrl = plugin.public_url;
    const openButton = publicUrl ? `<button data-open="${publicUrl}">Open</button>` : "";
    return `
        <div class="card">
            <h3>${plugin.name}</h3>
            <div class="meta">
                ID: ${pluginId}<br>
                Version: ${plugin.version}<br>
                Installed: ${plugin.installed_dir}<br>
                URL: ${publicUrl || "N/A"}
            </div>
            <div class="actions">
                <button data-action="start" data-plugin="${pluginId}">Start</button>
                <button data-action="stop" data-plugin="${pluginId}">Stop</button>
                <button data-action="restart" data-plugin="${pluginId}">Restart</button>
                <button data-action="healthcheck" data-plugin="${pluginId}">Healthcheck</button>
                ${openButton}
            </div>
        </div>
    `;
}

function renderPlugins(installedPlugins) {
    const container = byId("pluginsContainer");
    const entries = Object.entries(installedPlugins || {});
    if (!entries.length) {
        container.innerHTML = "<div class='subtext'>No plugins installed yet.</div>";
        return;
    }
    container.innerHTML = entries.map(([pluginId, plugin]) => pluginCard(pluginId, plugin)).join("");
}

function jobCard(job) {
    const statusClass = job.status === "running" ? "status-running" : (job.status === "failed" ? "status-failed" : "");
    return `
        <div class="job-card" data-job="${job.job_id}">
            <h3>${job.job_type}</h3>
            <div class="meta">
                Job ID: ${job.job_id}<br>
                Target: ${job.target}<br>
                Status: <span class="${statusClass}">${job.status}</span><br>
                Progress: ${job.progress}%
            </div>
            <div class="progress"><div style="width: ${job.progress || 0}%"></div></div>
        </div>
    `;
}

function renderJobs(jobs) {
    state.jobs = jobs || [];
    const container = byId("jobsContainer");
    if (!state.jobs.length) {
        container.innerHTML = "<div class='subtext'>No jobs yet.</div>";
        return;
    }
    container.innerHTML = state.jobs.map(jobCard).join("");
}

async function refreshSummary() {
    const summary = await getJson("/api/control-center/summary");
    renderSummary(summary);
    renderPlugins(summary.installed_plugins);
    renderJobs(summary.jobs);
    wireDynamicActions();
}

async function selectJob(jobId) {
    state.selectedJobId = jobId;
    byId("selectedJobLabel").textContent = `Selected Job: ${jobId}`;
    const payload = await getJson(`/api/jobs/${jobId}/logs`);
    byId("jobLogBox").textContent = payload.logs || "";

    if (state.ws) {
        state.ws.close();
        state.ws = null;
    }

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${location.host}/api/jobs/ws/${jobId}`;
    state.ws = new WebSocket(wsUrl);

    state.ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.logs !== undefined) byId("jobLogBox").textContent = data.logs || "";
        if (data.job) refreshSummary().catch(console.error);
    };
}

async function pluginAction(pluginId, action) {
    const response = await getJson(`/api/control-center/plugins/${pluginId}/${action}`, { method: "POST" });
    if (response.job_id) {
        await refreshSummary();
        await selectJob(response.job_id);
    }
}

function wireDynamicActions() {
    document.querySelectorAll("[data-action]").forEach((button) => {
        button.onclick = async () => {
            const pluginId = button.getAttribute("data-plugin");
            const action = button.getAttribute("data-action");
            try { await pluginAction(pluginId, action); } catch (error) { alert(String(error)); }
        };
    });

    document.querySelectorAll("[data-open]").forEach((button) => {
        button.onclick = () => {
            const url = button.getAttribute("data-open");
            window.open(url, "_blank");
        };
    });

    document.querySelectorAll("[data-job]").forEach((card) => {
        card.onclick = async () => {
            const jobId = card.getAttribute("data-job");
            await selectJob(jobId);
        };
    });
}

byId("refreshBtn").onclick = () => refreshSummary().catch(console.error);
byId("copyLogBtn").onclick = async () => {
    const content = byId("jobLogBox").textContent || "";
    await navigator.clipboard.writeText(content);
};

refreshSummary().catch(console.error);
