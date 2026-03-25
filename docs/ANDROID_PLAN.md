# Android APK Integration Plan

## 1. Goal

This workspace should evolve into an integrated Polymarket control system that keeps the current Windows/Linux operating model, but adds:

- one unified control panel for `POLYMARKET_MAKER_copytrade_v2` and `POLY_SMARTMONEY`
- Linux VPS deployment as the primary production mode
- one isolated runtime instance per user
- an Android APK as a remote shell over the control panel

The Android app is not intended to run the trading logic locally. Python strategy processes remain on the VPS. The APK is only a remote control surface.

## 2. Target Product Shape

The final product should work like this:

1. The user has one dedicated VPS instance.
2. The VPS runs Python strategy services for:
   - `copytrade v2`
   - `autorun`
   - `smartmoney / v3 multi`
3. A single web panel manages configuration, status, logs, and service control.
4. The Android APK loads the remote panel and reuses the same backend APIs.

This preserves the current desktop/web workflow while making it mobile-accessible and deployable for multiple users.

## 3. Main Functional Modules

### 3.1 Unified Runtime Management

The panel must be able to:

- start, stop, and restart `copytrade v2`
- start, stop, and restart `autorun`
- start, stop, and restart `smartmoney / v3 multi`
- show current runtime status
- show PID or service activity state
- show recent update time
- show log tail summaries

### 3.2 Unified Configuration Management

The panel must expose user-editable forms for:

- `account.json`
- `copytrade_config.json`
- `global_config.json`
- `run_params.json`
- `strategy_defaults.json`
- `POLY_SMARTMONEY` runtime config
- `accounts.json` for multi-account workflows

The panel should continue to map UI fields to the existing JSON/YAML structures instead of forcing users to edit raw files directly.

### 3.3 Per-User Instance Isolation

Each user instance must have isolated:

- credentials
- config files
- logs
- runtime state
- PID files and temporary run files

The first implementation should remain single-instance-per-deployment, but internal path design should already support instance-specific storage.

### 3.4 Security And Access Control

Before exposing the panel to users, add:

- login authentication
- authenticated API access
- HTTPS reverse proxy
- secret-safe logging
- safe account field handling

### 3.5 Mobile Web Adaptation

The existing panel should be adapted for small screens:

- single-column forms on mobile
- touch-friendly buttons
- compact status cards
- readable log areas
- preserved `v2` and `smartmoney` tab switching

### 3.6 Android APK Shell

The first Android version should include:

- a WebView shell
- server URL configuration or a fixed hosted URL
- login support
- persistent session or token reuse
- basic splash/icon packaging

Do not attempt to move the Python runtime into Android.

## 4. Recommended Architecture

### 4.1 Execution Plane

Python runtime services continue to execute from the source projects:

- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2`
- `D:\AI\vibe_coding4\POLY_SMARTMONEY`

These remain the strategy and execution layer.

### 4.2 Control Plane

The control plane remains centered on the existing panel:

- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\server.py`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\config_store.py`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\runtime_paths.py`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\static\index.html`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\static\app.js`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\static\styles.css`

This panel should become the single user-facing admin surface.

### 4.3 Delivery Plane

The delivery model is:

- Linux VPS as production host
- reverse proxy for HTTPS
- one deployment instance per user
- Android APK as shell over the remote panel

## 5. Implementation Phases

### Phase A: Path And Instance Model

Purpose:

- decouple panel behavior from fixed repo-relative assumptions
- support instance-specific config, logs, and run files

Key work:

- update `runtime_paths.py` to resolve:
  - app root
  - source root
  - instance root
  - v2 root
  - smartmoney root
  - log root
  - run root
- prefer environment variables over fragile relative path guessing

Outcome:

- the panel can run against a chosen instance directory
- later VPS deployments become predictable

### Phase B: Unified Service Control

Purpose:

- control all core services through one backend model

Key work:

- update `server.py`
- define a clear service registry for:
  - `copytrade`
  - `autorun`
  - `v3multi`
- standardize:
  - service name
  - command
  - working directory
  - log path
  - status source

Linux should prefer `systemd`. Local-process fallback should remain for development.

Outcome:

- the panel behaves the same whether viewed from desktop browser, remote browser, or APK shell

### Phase C: Config Store Instance Isolation

Purpose:

- make config and runtime state safe for user-by-user deployment

Key work:

- update `config_store.py`
- move file resolution away from hard-coded project paths
- read and write from instance-scoped locations
- preserve existing config schemas

Recommended instance layout:

```text
instances/<instance_id>/
  account.json
  v2/
    copytrade_config.json
    global_config.json
    run_params.json
    strategy_defaults.json
  smartmoney/
    copytrade_config.json
    accounts.json
  logs/
  run/
```

Outcome:

- user deployments are isolated without changing the current UI model

### Phase D: Authentication And HTTPS

Purpose:

- make the panel safe enough for real user access

Key work:

- add login handling to the panel backend
- protect API routes
- support session or token-based auth
- deploy behind Nginx or Caddy
- keep secrets out of logs and API error messages

Outcome:

- remote access becomes production-usable

### Phase E: Mobile Web Adaptation

Purpose:

- make the current panel usable on phone screens before packaging APK

Key work:

- update `index.html`
- update `styles.css`
- update `app.js`
- keep the current information architecture
- reduce visual density for small screens

Outcome:

- the panel works in a mobile browser
- APK packaging becomes straightforward

### Phase F: Android APK Packaging

Purpose:

- wrap the now-mobile-ready panel into an Android app

Recommended first approach:

- use Capacitor
- create a minimal Android shell
- load the remote panel URL
- support login persistence

Outcome:

- deliverable APK with minimal maintenance overhead

## 6. Product-Level Requirements Summary

### Required In Version 1

- unified panel for `v2` and `smartmoney`
- VPS deployment
- remote configuration editing
- service start/stop/restart
- runtime status display
- log viewing
- login auth
- mobile-friendly panel
- Android shell APK

### Explicitly Not Required In Version 1

- Android local Python runtime
- Android-native trading execution
- shared multi-tenant backend
- user self-service automatic provisioning
- advanced push notifications
- native rewrite of the panel

## 7. Technical Step-By-Step Execution Plan

1. Refactor path resolution in `runtime_paths.py`.
2. Refactor config path usage in `config_store.py`.
3. Normalize service definitions and state reporting in `server.py`.
4. Define instance directory structure under `instances/`.
5. Add backend auth and session handling.
6. Add reverse-proxy deployment examples under `deploy/`.
7. Adapt static panel files for mobile.
8. Verify the panel in a phone browser.
9. Create Android shell project under `android/`.
10. Package first APK only after web behavior is stable.

## 8. Minimal Code Change List

Prioritize these files first:

- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\runtime_paths.py`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\config_store.py`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\server.py`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\static\index.html`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\static\styles.css`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\static\app.js`

Recommended new top-level folders:

- `D:\AI\vibe_coding4\docs`
- `D:\AI\vibe_coding4\deploy`
- `D:\AI\vibe_coding4\android`
- `D:\AI\vibe_coding4\instances`

## 9. Minimal Environment Variable List

Required for the integrated panel:

- `POLY_APP_ROOT`
- `POLY_INSTANCE_ROOT`
- `POLY_PANEL_HOST`
- `POLY_PANEL_PORT`
- `POLY_AUTH_USERNAME`
- `POLY_AUTH_PASSWORD`
- `POLY_SESSION_SECRET`

Recommended support variables:

- `POLY_LOG_ROOT`
- `POLY_RUN_ROOT`
- `POLY_FORCE_SOURCE_SERVICES`
- `POLY_DESKTOP_BIN_DIR`
- `POLY_REVERSE_PROXY_MODE`

## 10. Minimal Runtime And Data Checklist

Per instance, the deployment should provide:

- `account.json`
- `v2/copytrade_config.json`
- `v2/global_config.json`
- `v2/run_params.json`
- `v2/strategy_defaults.json`
- `smartmoney/copytrade_config.json`
- `smartmoney/accounts.json`
- `logs/`
- `run/`

## 11. Difficulty Estimate

- Integrated VPS control panel: medium
- Per-user isolated deployment: medium to high
- Security and auth hardening: medium to high
- Android APK shell: low
- Android local execution of strategies: not recommended

## 12. Immediate Next Implementation Step

The first code task should be:

- introduce instance-aware path resolution in `runtime_paths.py`

Then continue in this order:

- `config_store.py`
- `server.py`
- mobile adaptation
- deployment scripts
- APK shell
