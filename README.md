# Homelab OS

Plugin-based Raspberry Pi homelab control platform.

## Status

This repo includes:

- bootstrap flow
- Python packaging
- CLI foundation
- config loading
- FastAPI core app foundation
- runtime directory initialization

## Quick start

```bash
cd ~/homelab_os
python3 bootstrap.py
source .venv/bin/activate
sudo systemctl restart homelab-os-core.service

homelabctl bootstrap-host --env-file .env

homelabctl build-all-plugins --env-file .env
```


## Install and start a plugin

```bash
homelabctl install-plugin build/music-player.tgz --env-file .env
homelabctl start-plugin music-player --env-file .env
```

## Reload the Control Center (for any core change only)

```bash
cd ~/homelab_os
python3 bootstrap.py
source .venv/bin/activate
sudo systemctl restart homelab-os-core.service
```
