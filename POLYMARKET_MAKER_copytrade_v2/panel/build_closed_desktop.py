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
        "set POLY_DESKTOP_FORCE_BROWSER=1\r\n"
        "set POLY_DESKTOP_APP_MODE=browser\r\n"
        "start \"\" \"%~dp0PolymarketWebPanel.exe\"\r\n"
    )
    (RELEASE_DIR / "LaunchWebPanel.bat").write_text(web_launcher_body, encoding="utf-8")


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
            "--windows-console-mode=disable",
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
    print(f"[BUILD] release ready at {RELEASE_DIR}")


if __name__ == "__main__":
    main()
