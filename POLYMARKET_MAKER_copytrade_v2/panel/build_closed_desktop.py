from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from runtime_paths import resolve_repo_root


PANEL_DIR = Path(__file__).resolve().parent
DIST_DIR = PANEL_DIR / "dist_closed"
BIN_DIR = DIST_DIR / "bin"
RELEASE_DIR = resolve_repo_root() / "PolymarketDesktop_Final"


def _run(command: list[str]) -> None:
    print("[BUILD]", " ".join(command))
    subprocess.run(command, check=True, cwd=str(PANEL_DIR))


def _write_launcher() -> None:
    launcher_body = (
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d %~dp0\r\n"
        "set POLY_APP_ROOT=%~dp0..\\\r\n"
        "set POLY_DESKTOP_BIN_DIR=%~dp0bin\r\n"
        "set POLY_FORCE_SOURCE_SERVICES=1\r\n"
        "start \"\" \"%~dp0PolymarketDesktop.exe\"\r\n"
    )
    (RELEASE_DIR / "LaunchDesktop.bat").write_text(launcher_body, encoding="utf-8")


def _prepare_release() -> None:
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    (RELEASE_DIR / "bin").mkdir(parents=True, exist_ok=True)


def _copy_release_artifacts() -> None:
    shutil.copy2(DIST_DIR / "PolymarketDesktop.exe", RELEASE_DIR / "PolymarketDesktop.exe")
    for exe_path in BIN_DIR.glob("*.exe"):
        shutil.copy2(exe_path, RELEASE_DIR / "bin" / exe_path.name)
    shutil.copy2(PANEL_DIR / "README.md", RELEASE_DIR / "README.md")
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

    service_targets = [
        ("copytrade_v2_service.exe", PANEL_DIR / "desktop_targets" / "copytrade_v2_service.py"),
        ("autorun_v2_service.exe", PANEL_DIR / "desktop_targets" / "autorun_v2_service.py"),
        ("copytrade_v3_multi_service.exe", PANEL_DIR / "desktop_targets" / "copytrade_v3_multi_service.py"),
    ]
    for output_name, script_path in service_targets:
        _run(
            common
            + [
                "--onefile",
                "--windows-console-mode=disable",
                f"--output-dir={BIN_DIR}",
                f"--output-filename={output_name}",
                str(script_path),
            ]
        )

    _copy_release_artifacts()
    print(f"[BUILD] release ready at {RELEASE_DIR}")


if __name__ == "__main__":
    main()
