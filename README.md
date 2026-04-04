# Homelab OS

Plugin-based Raspberry Pi homelab control platform.

## Quick start

```bash
cd ~/homelab_os
python3 bootstrap.py
source .venv/bin/activate
pip install -e .
homelabctl build-all-plugins --env-file .env
homelabctl install-plugin build/test_plugin.tgz --env-file .env
```

## Current coverage

- bootstrap flow
- Python packaging
- CLI foundation
- config loading
- plugin validator, builder, installer, runtime metadata, registry
- reverse proxy snippet generation with automatic Caddy validation and reload
- sample `test_plugin`
- Pi-hole plugin with cloudflared sidecar

## Notes

- `install-plugin` uses a positional archive argument.
- Caddy operations use `sudo` only for reading the main Caddyfile, writing snippets, validating config, and reloading the service.
