# Homelab OS

**Version 3.1.2** · Plugin-based Raspberry Pi homelab control platform · Python ≥ 3.11

Homelab OS turns a Raspberry Pi into a personal cloud and application platform. A single bootstrap command sets up the entire system. Each app runs as an isolated Docker container managed through a unified Control Center dashboard or the `homelabctl` CLI. All services are exposed securely over HTTPS via Tailscale — no port forwarding required.

---

## Table of Contents

- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration (.env)](#configuration-env)
- [CLI Reference](#cli-reference)
- [Plugin Catalog](#plugin-catalog)
- [Plugin Development](#plugin-development)
- [Auto-Recovery & Watchdog](#auto-recovery--watchdog)
- [Self-Heal](#self-heal)
- [Storage Layout](#storage-layout)
- [Networking & Remote Access](#networking--remote-access)
- [Updating the System](#updating-the-system)
- [Troubleshooting](#troubleshooting)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Control Center                      │
│              (Dashboard · Plugin Marketplace)           │
└───────────────────────┬─────────────────────────────────┘
                        │
          ┌─────────────▼─────────────┐
          │       homelabctl CLI      │
          │  (Bootstrap · Install ·   │
          │   Build · Heal · Logs)    │
          └──────┬──────────┬─────────┘
                 │          │
         ┌───────▼──┐  ┌────▼───────────────┐
         │  Caddy   │  │  Plugin Runtime    │
         │ Reverse  │  │  (Docker Compose   │
         │  Proxy   │  │   per plugin)      │
         └───────┬──┘  └────────────────────┘
                 │
    ┌────────────▼────────────────────────┐
    │         Tailscale VPN               │
    │  https://<fqdn>:<port>  (HTTPS)     │
    └─────────────────────────────────────┘
```

**Key principles:**

- Each plugin is a self-contained Docker Compose stack isolated by project name
- Caddy reverse-proxy handles TLS termination using Tailscale certificates; no self-signed certs
- All runtime data lives on `/mnt/nas` (external HDD/NAS) — the SD card is never written to during operation
- The watchdog systemd service continuously monitors and restarts failed plugins
- Self-Heal detects and repairs corrupted Docker storage without touching healthy running containers

---

## Quick Start

### 1. Clone / copy the repo to your Raspberry Pi

```bash
cd ~
# place homelab_os/ in your home directory
cd homelab_os
```

### 2. Run bootstrap

```bash
python3 bootstrap.py
```

This single command:
- Creates and installs the project into `.venv`
- Patches `.env` with any missing required keys (e.g. `PIHOLE_PASSWORD`, `TZ`)
- Runs `homelabctl bootstrap-host` which:
  - Initialises all runtime directories under `/mnt/nas/homelab/`
  - Writes and validates the main Caddyfile
  - Installs the Control Center systemd service and starts it
  - Reconciles Caddy routes for all installed plugins
  - Installs and enables the `homelab-watchdog` systemd service

### 3. Activate the virtual environment

```bash
source .venv/bin/activate
```

### 4. Build all plugin archives

```bash
homelabctl build-all-plugins --env-file .env
```

This creates versioned archives in `build/`, e.g. `build/music-player.v8.4.33.tgz`.

### 5. Install a plugin

```bash
homelabctl install-plugin build/<plugin-name>.v<version>.tgz --env-file .env
```

The plugin is installed **and started automatically** — no separate start command needed.

---

## Configuration (.env)

All configuration is read from `.env` in the project root. `bootstrap.py` auto-creates and patches this file on first run.

| Key | Default | Description |
|-----|---------|-------------|
| `HOSTNAME` | `pi-nas` | Hostname of your Raspberry Pi |
| `LAN_IP` | `192.168.88.10` | LAN IP address of the Pi |
| `TAILSCALE_IP` | `100.66.127.27` | Tailscale IP address of the Pi |
| `TAILSCALE_FQDN` | `pi-nas.taild4713b.ts.net` | Tailscale fully-qualified domain name |
| `NAS_MOUNT` | `/mnt/nas` | Mount point of the external HDD/NAS |
| `HOMELAB_ROOT` | `/mnt/nas/homelab` | Root of all homelab runtime data |
| `DOCKER_ROOT_DIR` | `/mnt/nas/homelab/docker` | Docker data root (never on SD card) |
| `CONTROL_CENTER_BIND` | `127.0.0.1` | Address the Control Center binds to |
| `CONTROL_CENTER_PORT` | `9000` | Internal port for the Control Center |
| `CONTROL_CENTER_PUBLIC_PORT` | `8444` | External HTTPS port for the Control Center |
| `BUILD_DIR` | `build` | Output directory for plugin archives |
| `PLUGINS_DIR` | `plugins` | Source directory for plugin definitions |
| `MANIFESTS_DIR` | `manifests` | Plugin state manifest directory |
| `RUNTIME_DIR` | `runtime` | Installed plugin state directory |
| `LOGS_DIR` | `/mnt/nas/homelab/logs` | Log output directory |
| `BACKUPS_DIR` | `/mnt/nas/homelab/backups` | Backup output directory |
| `CADDYFILE` | `/etc/caddy/Caddyfile` | Path to the main Caddyfile |
| `CADDY_APPS_DIR` | `/etc/caddy/apps` | Directory for per-plugin Caddy snippets |
| `CADDY_DISABLED_DIR` | `/etc/caddy/apps.disabled` | Snippets directory for disabled plugins |
| `TAILSCALE_CERT_DIR` | `/etc/caddy/certs/tailscale` | Directory containing Tailscale TLS certs |
| `PIHOLE_PASSWORD` | `admin` | Pi-hole web admin password (always `admin`) |
| `PIHOLE_UPSTREAMS` | `1.1.1.1;1.0.0.1` | Upstream DNS servers for Pi-hole |
| `TZ` | `Asia/Kolkata` | Timezone for all containers |

> **Rule:** `NAS_MOUNT`, `HOMELAB_ROOT`, and `DOCKER_ROOT_DIR` must always point to the external HDD. Never change these to SD card paths.

---

## CLI Reference

All commands are available via `homelabctl` after activating `.venv`.

### Bootstrap & Setup

```bash
# Full bootstrap (install venv + host setup + watchdog)
python3 bootstrap.py

# Host-only bootstrap (skips venv installation)
homelabctl bootstrap-host --env-file .env

# Install or reinstall the watchdog service
homelabctl install-watchdog --env-file .env
```

### Plugin Build

```bash
# Build all plugins
homelabctl build-all-plugins --env-file .env

# Build a single plugin
homelabctl build-plugin <plugin-id> --env-file .env
```

### Plugin Lifecycle

```bash
# Install plugin archive AND start it immediately
homelabctl install-plugin build/<plugin>.v<version>.tgz --env-file .env

# Stop a running plugin
homelabctl stop-plugin <plugin-id> --env-file .env

# Restart a plugin
homelabctl restart-plugin <plugin-id> --env-file .env

# Uninstall a plugin (stops containers and removes runtime data)
homelabctl uninstall-plugin <plugin-id> --env-file .env

# List all installed plugins and their status
homelabctl list-plugins --env-file .env
```

### Monitoring & Logs

```bash
# Tail logs for a plugin
homelabctl logs <plugin-id> --env-file .env

# Show system health summary
homelabctl health --env-file .env
```

### Recovery

```bash
# Run self-heal (detects and repairs Docker storage + restarts failed services)
homelabctl self-heal --env-file .env
```

### Routing

```bash
# Reconcile Caddy routes for all installed plugins
homelabctl reconcile-routes --env-file .env
```

---

## Plugin Catalog

| Plugin | ID | Public Port | Access URL |
|--------|----|-------------|------------|
| Control Center | `control-center` | 8444 | `https://<fqdn>:8444/` |
| Pi-hole | `pihole` | 8447 | `https://<fqdn>:8447/admin/` |
| Pi Status Board | `status` | 8451 | `https://<fqdn>:8451/` |
| Pi Voice AI | `voice-ai` | 8452 | `https://<fqdn>:8452/` |
| Homarr | `homarr` | 8453 | `https://<fqdn>:8453/` |
| Personal Library | `personal-library` | 8454 | `https://<fqdn>:8454/` |
| Dictionary | `dictionary` | 8455 | `https://<fqdn>:8455/` |
| API Gateway | `api-gateway` | 8456 | `https://<fqdn>:8456/docs` |
| Music Player | `music-player` | 8459 | `https://<fqdn>:8459/` |
| Media Downloader | `link-downloader` | 8460 | `https://<fqdn>:8460/` |
| Song Downloader | `song-downloader` | 8445 | `https://<fqdn>:8445/` |
| Files | `files` | 8449 | `https://<fqdn>:8449/` |

Replace `<fqdn>` with your `TAILSCALE_FQDN` value (e.g. `pi-nas.taild4713b.ts.net`).

### Default Credentials

| Service | Username | Password |
|---------|----------|----------|
| Pi-hole | — | `admin` |
| Control Center | — | set during bootstrap |

---

## Plugin Development

### Directory structure

```
plugins/
└── my-plugin/
    ├── plugin.json
    └── docker/
        ├── docker-compose.yml
        └── app/
            ├── app.py          # Flask/FastAPI entry point
            ├── app/
            │   ├── config.py
            │   ├── routes.py   # routes_bp = Blueprint("routes", __name__)
            │   └── ...
            └── Dockerfile
```

### plugin.json format

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "runtime_type": "docker",
  "network": {
    "internal_port": 8200,
    "public_port": 8461
  },
  "entrypoint": {
    "type": "web",
    "path": "/"
  }
}
```

**Required fields:**

- `id` — unique kebab-case identifier, must match the directory name under `plugins/`
- `name` — human-readable display name
- `version` — semver string
- `runtime_type` — always `"docker"` for containerised plugins
- `network.internal_port` — the port the Docker container listens on
- `network.public_port` — the HTTPS port Caddy exposes externally (must be registered in `config/app_catalog.json`)
- `entrypoint.path` — the URL path the browser should open when launching the plugin

### Flask app pattern

```python
# app.py
from flask import Flask
from app.routes import routes_bp

def create_app():
    app = Flask(__name__)
    app.register_blueprint(routes_bp)
    return app

if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=8200)
```

```python
# app/routes.py
from flask import Blueprint

routes_bp = Blueprint("routes", __name__)

@routes_bp.get("/")
def index():
    return {"status": "ok"}
```

### Streaming plugins

If your plugin streams binary data (audio, large file downloads), add the plugin ID to `_STREAMING_PLUGINS` in `homelab_os/core/services/reverse_proxy.py`. This adds `flush_interval -1` to the Caddy snippet so bytes reach the browser immediately instead of being buffered.

### Build and install workflow

```bash
# After editing plugin source
homelabctl build-plugin my-plugin --env-file .env
homelabctl uninstall-plugin my-plugin --env-file .env   # if already installed
homelabctl install-plugin build/my-plugin.v1.0.0.tgz --env-file .env
```

---

## Auto-Recovery & Watchdog

The `homelab-watchdog` systemd service is installed automatically during `bootstrap.py`. It runs continuously in the background and restarts any plugin whose Docker container has stopped unexpectedly.

```bash
# Check watchdog status
sudo systemctl status homelab-watchdog

# View watchdog logs
sudo journalctl -u homelab-watchdog -f

# Reinstall the watchdog (e.g. after settings change)
homelabctl install-watchdog --env-file .env
```

The watchdog is also re-applied automatically every time `homelabctl self-heal` runs, so it is always in sync with the current installed plugin set.

---

## Self-Heal

Self-Heal inspects the Docker daemon, storage layer, and all installed plugins and attempts to repair any issues it finds.

```bash
homelabctl self-heal --env-file .env
```

**What it does:**

1. Checks for Docker storage corruption using a conservative set of error signatures (layer-missing errors, mount failures). Normal Docker BuildKit output such as `"failed to solve"` is explicitly excluded to prevent false positives.
2. Before wiping Docker storage, checks `docker ps -q` — if any containers are running the repair is aborted to protect healthy services.
3. Checks Pi-hole is running and resets its admin password to the value in `.env` using both the v5 (`pihole setpassword`) and v6 (`pihole-FTL --config webserver.api.password`) methods.
4. Reconciles Caddy routes for all installed plugins.
5. Reinstalls and re-enables the watchdog service.

---

## Storage Layout

All runtime data lives under `/mnt/nas` (the external HDD). The SD card is only used for the OS and the homelab_os source code.

```
/mnt/nas/
└── homelab/
    ├── docker/          # Docker data root (DOCKER_ROOT_DIR)
    ├── logs/            # Application and system logs
    ├── backups/         # Plugin and config backups
    └── runtime/
        ├── installed_plugins/   # Installed plugin manifests
        ├── pihole/
        │   └── data/
        │       ├── etc-pihole/
        │       └── etc-dnsmasq.d/
        └── ...

/mnt/nas/media/
└── music/               # Music files for Music Player
```

---

## Networking & Remote Access

Homelab OS uses Tailscale for zero-config remote access with full HTTPS — no port forwarding, no dynamic DNS, no self-signed certificates.

**How it works:**

1. Tailscale creates a private VPN tunnel between your devices.
2. Caddy uses the Tailscale-issued TLS certificate (`TAILSCALE_CERT_DIR`) to serve all services over HTTPS.
3. Each plugin gets its own Caddy snippet installed at `/etc/caddy/apps/<plugin-id>.caddy`.
4. DNS queries can be routed through Pi-hole by setting your device's DNS to `TAILSCALE_IP` (port 53 is bound to `LAN_IP` and `TAILSCALE_IP` only, avoiding conflicts with `systemd-resolved`).

**Accessing services:**

All services are available at `https://<TAILSCALE_FQDN>:<public_port>` from any device connected to your Tailscale network.

---

## Updating the System

### Update Control Center / core code only

```bash
cd ~/homelab_os
python3 bootstrap.py
source .venv/bin/activate
sudo systemctl restart homelab-os-core.service
```

### Update a plugin

```bash
source .venv/bin/activate
homelabctl build-plugin <plugin-id> --env-file .env
homelabctl uninstall-plugin <plugin-id> --env-file .env
homelabctl install-plugin build/<plugin-id>.v<new-version>.tgz --env-file .env
```

### Update everything

```bash
cd ~/homelab_os
python3 bootstrap.py
source .venv/bin/activate
sudo systemctl restart homelab-os-core.service
homelabctl build-all-plugins --env-file .env
# Then uninstall + install each plugin with the new archive
```

The Control Center UI also shows update buttons when a newer version of an installed plugin is available in `build/`.

---

## Troubleshooting

### Pi-hole admin page not opening

1. Check Pi-hole is running: `docker ps | grep pihole`
2. Run self-heal: `homelabctl self-heal --env-file .env` — this resets the password and reconciles the Caddy route.
3. Verify the Caddy snippet exists: `sudo cat /etc/caddy/apps/pihole.caddy`
4. Check Caddy is running: `sudo systemctl status caddy`

### Self-Heal wiped my Docker images

This was a known bug (fixed in v3.1.2). The phrase `"failed to solve"` appeared in BuildKit logs and was incorrectly detected as storage corruption. Update to v3.1.2 or later — the detection signatures now only match genuine storage errors (`layer does not exist`, `failed to register layer`, etc.). Additionally, self-heal will abort the Docker wipe if any containers are currently running.

### Music Player shows no songs

1. Confirm files are in `/mnt/nas/media/music/` and have supported extensions (`.mp3`, `.flac`, `.ogg`, `.m4a`, etc.).
2. Check the Music Player container logs: `docker logs music-player`
3. Reinstall the plugin to pick up the latest `library.py` (which URL-encodes stream paths for files with spaces or Unicode characters in their names).

### Plugin installed but not accessible

1. Check the Caddy snippet was written: `sudo cat /etc/caddy/apps/<plugin-id>.caddy`
2. Reconcile routes: `homelabctl reconcile-routes --env-file .env`
3. Validate Caddy config: `sudo caddy validate --config /etc/caddy/Caddyfile`
4. Reload Caddy: `sudo systemctl reload caddy`

### Port 53 conflict (Pi-hole DNS not working)

Raspberry Pi OS runs `systemd-resolved` on `127.0.0.53:53`. Pi-hole's docker-compose.yml binds port 53 only to `${LAN_IP}` and `${TAILSCALE_IP}`, not `0.0.0.0`. Ensure both values are set correctly in `.env`.

### Watchdog not running

```bash
sudo systemctl status homelab-watchdog
homelabctl install-watchdog --env-file .env
sudo systemctl status homelab-watchdog
```

### Control Center not reachable after reboot

```bash
sudo systemctl status homelab-os-core.service
sudo systemctl restart homelab-os-core.service
```

If it fails to start, check logs: `sudo journalctl -u homelab-os-core.service -n 50`
