# Deploy Guide (Integrated Panel)

This folder contains deployment scaffolding for the integrated control panel.

## Layout

- `linux/install_instance.sh`: bootstrap one instance on Linux VPS
- `nginx/polymarket_panel.conf.example`: reverse-proxy HTTPS template
- `panel.env.example`: environment variable template for panel runtime

## Baseline assumptions

1. Source code is deployed at a stable path, for example: `/opt/polyapp/current`.
2. One VPS instance serves one user.
3. You configure per-instance variables through an env file.
4. Nginx (or Caddy) provides public HTTPS access and proxies to local panel port.

## Quick steps

1. Run `linux/install_instance.sh` with required parameters.
2. Fill generated panel env file with auth credentials and session secret.
3. Enable/restart the generated systemd service.
4. Add a reverse-proxy site config based on `nginx/polymarket_panel.conf.example`.

## Security notes

- Set strong values for `POLY_AUTH_PASSWORD` and `POLY_SESSION_SECRET`.
- Keep panel binding on localhost (`127.0.0.1`) behind reverse proxy.
- Never expose private keys in logs.
