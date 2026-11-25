#!/usr/bin/env python3
"""
Nuitka Secure Native EXE Builder for ADB Control Pro v1.0.0
Developer: Sabbir Ahmmed (https://github.com/sabbir-repo)
"""

import os
import subprocess
import sys

APP_NAME = "ADB Control Pro"
APP_VERSION = "1.0.0.0"
APP_DEVELOPER = "Sabbir Ahmmed (https://github.com/sabbir-repo)"
OUTPUT_DIR = "dist_nuitka"
OUTPUT_EXE = "ADBControlPro_v1.0.0.exe"


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--onefile",
        "--standalone",
        "--windows-console-mode=disable",
        "--enable-plugin=no-qt",
        "--disable-plugin=pywebview",
        "--prefer-source-code",
        "--include-data-dir=web_assets=web_assets",
        "--include-package-data=webview",
        "--include-module=webview.platforms.win32",
        "--include-module=webview.platforms.winforms",
        "--include-module=webview.platforms.edgechromium",
        "--include-package=clr",
        "--include-package=pythonnet",
        "--include-package=clr_loader",
        "--include-package=zeroconf",
        "--include-package=qrcode",
        "--nofollow-import-to=tkinter,customtkinter,rich,PIL,matplotlib,numpy,pytest,webview.platforms.android,webview.platforms.cocoa,webview.platforms.gtk,webview.platforms.qt,webview.platforms.cef",
        f"--product-name={APP_NAME}",
        "--file-description=ADB Control Pro - Modern Android ADB Desktop Suite",
        f"--company-name={APP_DEVELOPER}",
        f"--file-version={APP_VERSION}",
        f"--product-version={APP_VERSION}",
        "--copyright=Copyright (c) 2026 Sabbir Ahmmed - https://github.com/sabbir-repo",
        f"--output-dir={OUTPUT_DIR}",
        f"--output-filename={OUTPUT_EXE}",
        "--remove-output",
        "--assume-yes-for-downloads",
        "web_ui.py",
    ]
    print("🚀 Starting Nuitka Native Build:")
    print("  ", " ".join(cmd))
    proc = subprocess.run(cmd)
    if proc.returncode == 0:
        out_path = os.path.abspath(os.path.join(OUTPUT_DIR, OUTPUT_EXE))
        print(f"\n✅ Build succeeded: {out_path}")
    else:
        print(f"\n❌ Nuitka exited with code {proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
