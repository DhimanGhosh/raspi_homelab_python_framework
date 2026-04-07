# Homelab OS

Plugin-based Raspberry Pi homelab control platform.

## status

This repo currently includes:

- bootstrap flow
- Python packaging
- CLI foundation
- config loading
- FastAPI core app foundation
- runtime directory initialization

Plugin builder, installer, runtime, reverse proxy, and migrated plugins will be added in the next steps.

## Quick start

```bash
cd ~/homelab_os
python3 bootstrap.py
source .venv/bin/activate
homelabctl bootstrap-host --env-file .env

homelabctl build-all-plugins --env-file .env

homelabctl install-plugin build/music-player.tgz --env-file .env
homelabctl start-plugin music-player --env-file .env
```
