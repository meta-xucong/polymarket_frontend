from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from runtime_paths import resolve_repo_root, resolve_v2_root, resolve_v3_root


PANEL_DIR = Path(__file__).resolve().parent
DIST_DIR = PANEL_DIR / "dist_closed"
BIN_DIR = DIST_DIR / "bin"
RELEASE_DIR = resolve_repo_root() / "PolymarketDesktop_Final"
WEB_RELEASE_DIR = resolve_repo_root() / "release" / "web" / "PolymarketWebPanel"
APP_ROOT_DIR = RELEASE_DIR / "app_root"
V2_ROOT = resolve_v2_root()
V3_ROOT = resolve_v3_root()
SMARTMONEY_ROOT = resolve_repo_root() / "POLY_SMARTMONEY"


@dataclass(frozen=True)
class ServiceBuildTarget:
    stem: str
    script_path: Path
    mode: str
    pythonpath: tuple[Path, ...]
    include_args: tuple[str, ...]


SERVICE_TARGETS: tuple[ServiceBuildTarget, ...] = (
    ServiceBuildTarget(
        stem="copytrade_v2_service",
        script_path=PANEL_DIR / "desktop_targets" / "copytrade_v2_service.py",
        mode="standalone",
        pythonpath=(V2_ROOT / "copytrade", V2_ROOT),
        include_args=(
            "--include-module=copytrade_run",
            "--include-package=smartmoney_query",
        ),
    ),
    ServiceBuildTarget(
        stem="autorun_v2_service",
        script_path=PANEL_DIR / "desktop_targets" / "autorun_v2_service.py",
        mode="standalone",
        pythonpath=(
            V2_ROOT / "POLYMARKET_MAKER_AUTO",
            V2_ROOT / "POLYMARKET_MAKER_AUTO" / "POLYMARKET_MAKER",
            V2_ROOT,
        ),
        include_args=(
            "--include-module=poly_maker_autorun",
            "--include-package=Crypto",
            "--include-package=eth_hash",
            "--include-package=py_clob_client",
            "--include-package=py_order_utils",
            "--include-package=poly_eip712_structs",
            "--include-module=eth_hash.backends.pycryptodome",
        ),
    ),
    ServiceBuildTarget(
        stem="copytrade_v3_multi_service",
        script_path=PANEL_DIR / "desktop_targets" / "copytrade_v3_multi_service.py",
        mode="skip",
        pythonpath=(V3_ROOT, SMARTMONEY_ROOT),
        include_args=(),
    ),
)


def _run(command: list[str], env_overrides: dict[str, str] | None = None) -> None:
    print("[BUILD]", " ".join(command))
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    subprocess.run(command, check=True, cwd=str(PANEL_DIR), env=env)


def _write_launcher() -> None:
    launcher_body = (
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d %~dp0\r\n"
        "set POLY_APP_ROOT=%~dp0app_root\r\n"
        "set POLY_DESKTOP_BIN_DIR=%~dp0bin\r\n"
        "set POLY_DESKTOP_APP_MODE=desktop\r\n"
        "start \"\" \"%~dp0PolymarketDesktop.exe\"\r\n"
    )
    (RELEASE_DIR / "LaunchDesktop.bat").write_text(launcher_body, encoding="utf-8")

    web_launcher_body = (
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d %~dp0\r\n"
        "set POLY_APP_ROOT=%~dp0app_root\r\n"
        "set POLY_DESKTOP_BIN_DIR=%~dp0bin\r\n"
        "set POLY_FORCE_SOURCE_SERVICES=1\r\n"
        "set PYTHONUTF8=1\r\n"
        "set PYTHONIOENCODING=utf-8\r\n"
        "set POLY_DESKTOP_FORCE_BROWSER=1\r\n"
        "set POLY_DESKTOP_APP_MODE=browser\r\n"
        "set ACCOUNT_JSON=%~dp0app_root\\POLYMARKET_MAKER_copytrade_v2\\account.json\r\n"
        "set ACCOUNT_TEMPLATE=%~dp0app_root\\POLYMARKET_MAKER_copytrade_v2\\account.template.json\r\n"
        "if not exist \"%ACCOUNT_JSON%\" (\r\n"
        "  if exist \"%ACCOUNT_TEMPLATE%\" (\r\n"
        "    copy /Y \"%ACCOUNT_TEMPLATE%\" \"%ACCOUNT_JSON%\" >nul\r\n"
        "  )\r\n"
        ")\r\n"
        "\"%~dp0PolymarketWebPanel.exe\"\r\n"
    )
    (RELEASE_DIR / "LaunchWebPanel.bat").write_text(web_launcher_body, encoding="utf-8")


def _write_web_support_files(target_dir: Path) -> None:
    start_body = (
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d %~dp0\r\n"
        "echo [INFO] Starting Polymarket Web Panel...\r\n"
        "echo [INFO] Keep this terminal open while using the panel.\r\n"
        "call \"%~dp0LaunchWebPanel.bat\"\r\n"
    )
    (target_dir / "START_WEB_PANEL.bat").write_text(start_body, encoding="utf-8")

    quickstart = (
        "Polymarket Web Panel Quick Start\r\n"
        "================================\r\n\r\n"
        "1) Unzip the package to a normal folder.\r\n"
        "2) Double-click PolymarketWebPanel.exe.\r\n"
        "3) Keep the terminal window open while using the panel.\r\n"
        "4) Open http://127.0.0.1:8787 if browser does not open automatically.\r\n"
        "5) First login is admin/admin and must change credentials immediately.\r\n\r\n"
        "Important\r\n"
        "---------\r\n"
        "- If you see Failed to fetch, close all panel windows and run PolymarketWebPanel.exe again.\r\n"
        "- If 8787 is occupied, free the port and restart.\r\n"
    )
    (target_dir / "QUICKSTART.txt").write_text(quickstart, encoding="utf-8")


def _clean_runtime_artifacts(root: Path) -> None:
    patterns = (
        "run_params_*.json",
        "*.log",
        "*.pid",
        "*.tmp",
        "*.lock",
        "autorun_status.json",
        "copytrade_sell_signals.json",
        "copytrade_state.json",
        "exit_tokens.json",
        "handled_topics.json",
        "orphan_tokens.json",
        "positions_cache.json",
        "stoploss_reentry_state.json",
        "stoploss_reentry_state.bak.json",
        "token_cycle_gate.json",
        "tokens_from_copytrade.json",
        "ws_cache.json",
        "ws_cache.lock",
    )
    for pattern in patterns:
        for path in root.rglob(pattern):
            if path.is_file():
                path.unlink()


def _prepare_web_release_from_built_release() -> None:
    if WEB_RELEASE_DIR.exists():
        shutil.rmtree(WEB_RELEASE_DIR)
    WEB_RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    (WEB_RELEASE_DIR / "run").mkdir(parents=True, exist_ok=True)

    shutil.copy2(RELEASE_DIR / "PolymarketWebPanel.exe", WEB_RELEASE_DIR / "PolymarketWebPanel.exe")
    shutil.copy2(RELEASE_DIR / "LaunchWebPanel.bat", WEB_RELEASE_DIR / "LaunchWebPanel.bat")
    shutil.copy2(RELEASE_DIR / "README.md", WEB_RELEASE_DIR / "README.md")
    shutil.copytree(RELEASE_DIR / "bin", WEB_RELEASE_DIR / "bin")
    shutil.copytree(RELEASE_DIR / "app_root", WEB_RELEASE_DIR / "app_root")
    shutil.copytree(PANEL_DIR / "static", WEB_RELEASE_DIR / "static")
    _write_web_support_files(WEB_RELEASE_DIR)
    _clean_runtime_artifacts(WEB_RELEASE_DIR / "app_root")


def _prepare_release() -> None:
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    (RELEASE_DIR / "bin").mkdir(parents=True, exist_ok=True)
    APP_ROOT_DIR.mkdir(parents=True, exist_ok=True)


def _copy_portable_tree(src: Path, dst: Path) -> None:
    ignore = shutil.ignore_patterns(
        "__pycache__",
        ".pytest_cache",
        "*.pyc",
        "*.pyo",
        "*.log",
        "*.pid",
        "*.tmp",
        "*.lock",
        "dist_closed",
        "run",
        "logs",
        "auth.json",
        "stoploss_reentry_state.json",
        "stoploss_reentry_state.bak.json",
        "copytrade_sell_signals.json",
        "copytrade_state.json",
        "tokens_from_copytrade.json",
        "cost_anchors",
    )
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore)


def _copy_portable_app_root() -> None:
    _copy_portable_tree(V2_ROOT, APP_ROOT_DIR / "POLYMARKET_MAKER_copytrade_v2")
    smartmoney_dst = APP_ROOT_DIR / "POLY_SMARTMONEY"
    smartmoney_dst.mkdir(parents=True, exist_ok=True)
    _copy_portable_tree(V3_ROOT, smartmoney_dst / "copytrade_v3_muti")


def _copy_release_artifacts() -> None:
    shutil.copy2(DIST_DIR / "PolymarketDesktop.exe", RELEASE_DIR / "PolymarketDesktop.exe")
    shutil.copy2(DIST_DIR / "PolymarketWebPanel.exe", RELEASE_DIR / "PolymarketWebPanel.exe")
    for target in SERVICE_TARGETS:
        if target.mode == "standalone":
            dist_dir = BIN_DIR / f"{target.stem}.dist"
            dest_dir = RELEASE_DIR / "bin" / target.stem
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(dist_dir, dest_dir)
        elif target.mode == "onefile":
            src_exe = BIN_DIR / f"{target.stem}.exe"
            if src_exe.exists():
                shutil.copy2(src_exe, RELEASE_DIR / "bin" / src_exe.name)
    shutil.copy2(PANEL_DIR / "README.md", RELEASE_DIR / "README.md")
    _copy_portable_app_root()
    _write_launcher()


def main() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    _prepare_release()

    python = sys.executable
    common = [
        python,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        f"--output-dir={DIST_DIR}",
    ]

    _run(
        common
        + [
            "--onefile",
            "--windows-console-mode=disable",
            f"--include-data-dir={PANEL_DIR / 'static'}=static",
            f"--include-data-file={PANEL_DIR / 'README.md'}=README.md",
            "--output-filename=PolymarketDesktop.exe",
            str(PANEL_DIR / "desktop_launcher.py"),
        ]
    )

    _run(
        common
        + [
            "--onefile",
            "--windows-console-mode=force",
            f"--include-data-dir={PANEL_DIR / 'static'}=static",
            f"--include-data-file={PANEL_DIR / 'README.md'}=README.md",
            "--output-filename=PolymarketWebPanel.exe",
            str(PANEL_DIR / "webpanel_entry.py"),
        ]
    )

    service_common = [
        python,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--windows-console-mode=disable",
        f"--output-dir={BIN_DIR}",
    ]
    for target in SERVICE_TARGETS:
        if target.mode == "skip":
            continue
        pythonpath = os.pathsep.join(str(path) for path in target.pythonpath)
        target_cmd = list(service_common)
        if target.mode == "onefile":
            target_cmd.append("--onefile")
        _run(
            target_cmd
            + [
                f"--output-filename={target.stem}.exe",
                *target.include_args,
                str(target.script_path),
            ],
            env_overrides={"PYTHONPATH": pythonpath},
        )

    _copy_release_artifacts()
    _clean_runtime_artifacts(RELEASE_DIR / "app_root")
    _prepare_web_release_from_built_release()
    print(f"[BUILD] release ready at {RELEASE_DIR}")


if __name__ == "__main__":
    main()
