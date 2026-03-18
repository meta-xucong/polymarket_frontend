# Polymarket Panel

## Run

```bash
cd POLYMARKET_MAKER_copytrade_v2/panel
python server.py --host 0.0.0.0 --port 8787
```

Open:

`http://127.0.0.1:8787`

## Desktop Wrapper

Local desktop wrapper entry:

```bash
cd POLYMARKET_MAKER_copytrade_v2/panel
python desktop_launcher.py
```

If `pywebview` is available, this opens an embedded desktop window.
Otherwise it falls back to the system browser.

## Closed Build

Install desktop build dependencies:

```bash
pip install -r requirements-desktop.txt
```

Build the closed local desktop bundle:

```bash
cd POLYMARKET_MAKER_copytrade_v2/panel
python build_closed_desktop.py
```

Output layout:

- `dist_closed/PolymarketDesktop.exe`
- `dist_closed/bin/copytrade_v2_service.exe`
- `dist_closed/bin/autorun_v2_service.exe`
- `dist_closed/bin/copytrade_v3_multi_service.exe`
- `../../PolymarketDesktop_Final/PolymarketDesktop.exe`
- `../../PolymarketDesktop_Final/bin/*.exe`
- `../../PolymarketDesktop_Final/LaunchDesktop.bat`

Quick launch after build:

- Double-click `PolymarketDesktop_Final/LaunchDesktop.bat`
- Or double-click `LaunchPolymarketDesktop.bat` at the repo root

## Current Scope

- Edit `account.json`
- Edit a selected subset of existing config files
- Switch between `POLYMARKET_MAKER_copytrade_v2` and `POLY_SMARTMONEY/copytrade_v3_muti`
- View runtime summary
- View local log tails
- Call `systemctl start/stop/restart` when available
- Desktop wrapper can reuse the same panel and prefer compiled service binaries when present

## Notes

- This panel does not introduce a new business config layer.
- `account.json` remains the only new config file.
- `copytrade_v3_muti` is loaded from `POLY_SMARTMONEY/copytrade_v3_muti`.
- Local process stderr/stdout mirrors are written under each strategy's own log directory.
- If `systemctl` is unavailable in the current environment, service controls fall back to local process control.
- Desktop wrapper resolves workspace root from `POLY_APP_ROOT` when set, otherwise from the current repo layout.
