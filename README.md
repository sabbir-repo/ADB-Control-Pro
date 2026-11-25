# ⚡ ADB Control Pro `v1.0.0`

[![Version](https://img.shields.io/badge/version-1.0.0-10b981.svg?style=for-the-badge)](release/ADBControlPro_v1.0.0.exe)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg?style=for-the-badge)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg?style=for-the-badge)](release/ADBControlPro_v1.0.0.exe)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?style=for-the-badge)](requirements.txt)

**ADB Control Pro** is an open-source, modern glassmorphism desktop suite for controlling Android smartphones over USB and Wi-Fi using **ADB** and **scrcpy** — featuring multi-device isolation, auto-reconnect watchdog, Screen-Off Mirroring Studio, interactive file explorer with Root Mode, physical macro recording, Visual UI Inspector, live battery wattage graphing, and Root (`su`) superpowers.

Developed by **[Sabbir Ahmmed](https://github.com/sabbir-repo)**

---

## 📥 Download Standalone Windows EXE (No Python Required)

> **[⬇️ Download `ADBControlPro_v1.0.0.exe` (Nuitka Native Build)](release/ADBControlPro_v1.0.0.exe)**

Just double-click `ADBControlPro_v1.0.0.exe` on Windows 10/11 (requires `adb` on your system `PATH`; `scrcpy` optional for mirroring/camera/mic).

---

## ✨ Key Features (8 Focused Workspaces)

### 1. 🎮 Remote & Quick Controls
- **1-Click Smart Unlock** with live lock-screen state detection (won't accidentally type PINs into open apps if already unlocked).
- **Hardware & System Keys**, Unicode text input, URL launcher, exact `(X, Y)` tap, and bidirectional PC ↔ Phone clipboard sync.
- **📱 SIM & Phone Number(s):** Detects active SIM slots/carriers, 1-click Copy in both the Home card and Top HUD pill, carrier USSD quick-dial (`*511#`, `*2#`, `*551#`), and per-device persistent saved numbers.
- **System Toggles:** Mobile Data, Bluetooth, Location, Brightness, Screen Timeout, **Flashlight** (0.1s silent hardware sysfs toggle on Rooted phones, Quick Settings fallback on non-root), plus **Airplane Mode** & **Hotspot** toggles.

### 2. 📶 Connections, Watchdog & Android Companion
- **Multi-Device Safe:** Seamlessly manage multiple USB and Wi-Fi phones simultaneously with pinned-serial isolation.
- **🛡️ Auto-Reconnect Watchdog:** Weak-Wi-Fi resilient heartbeat, zombie `offline` socket cleanup, fast LAN port `5555` subnet scanner, and live Wi-Fi RSSI (`dBm`) HUD signal indicator.
- **Android 11+ Wireless QR Pairing** & 1-click USB → Fixed TCP/IP `5555`.
- **Android Companion App (`android_companion/`):** Event-driven `adb_wifi_enabled` auto-heal service with 10s debounce + LAN UDP beacon.

### 3. 🖥️ Screen Mirroring, Camera, Mic & Capture
- **Screen-Off Mirroring Studio (`-S`):** Full customization for Resolution (`360p` → `Original`), FPS (`15`–`120`), Bitrate (`500K`–`16M`), Audio (`Mute` / `64K`–`192K`), H.264/H.265, Stay Awake, Lock on Close, Always on Top, and Read-Only.
- **Background Camera** (Android 12+ Back/Front camera) & **🎙️ Live Mic Listen / Mic Recording** (Android 11+).
- **Custom `scrcpy` Presets**, Virtual Display (app-in-window), Multi-Device Mirroring, and Auto-Copy Screenshots.

### 4. 📁 Interactive File Explorer & Folder Sync
- **Multi-Select** (`Ctrl+Click`, `Shift+Click`, `Ctrl+A`, Select All) for bulk Download & Delete.
- **Live Transfer Progress Bar + Cancel** (speed, %, bytes) for Upload, Download, Folder Sync, and **Drag & Drop** (auto-installs `.apk` files).
- **Hover Image Preview** with instant-cancel & lightweight canvas LRU thumbnail cache.
- **👑 Root Mode Toggle:** Browse, pull, push, rename, and manage `/data/data`, `/system`, `/data/app`, and `/` with built-in destructive-path safety guards.

### 5. 📦 Apps, Bloatware Freezer & Permissions
- Filter by **Launcher Apps**, **User Installed**, **System / Bloatware**, or **❄️ Frozen / Disabled** with live status badges.
- **❄️ Pro App Freezer:** 1-click Freeze / Unfreeze bloatware apps (with core Android package safety protection).
- Launch, Force Stop, APK Backup, Batch APK Install, Clear Data, Uninstall, and Runtime Permission Grant/Revoke.

### 6. 💬 Comms — Notifications, SMS/OTP & Calls
- Live Notification viewer with 1-click copy.
- **SMS & Auto-OTP Extractor** and **Call Logs + Quick Dialer** — automatically falls back to **👑 Root (`su`) & built-in SQLite reader** (`mmssms.db`, `calllog.db`, `telephony.db`) on rooted devices if Android blocks standard content queries.

### 7. 🤖 Automation Studio — Macro, Recorder & Visual UI Inspector
- **Physical Action Recorder** (Fast Gesture, Smart UI Element, and Direct Activity modes) + Loop Sequencer.
- **🎯 Visual Live UI Inspector:** Interactive screenshot overlay with hover/click XML bounds inspection, live tap, and 1-click Smart/Coordinate Macro step picker.

### 8. 🔧 Monitor, Shell & Unlock Profiles
- **🔋 Live Battery Health, Wattage (`W`), Current (`mA`), Voltage (`V`), Temp (`°C`) & Real-Time SVG Graph.**
- **💻 Custom ADB & `👑 Root (su)` Shell**, Root Diagnostics, and Snapshot/Live Logcat Streamer.
- **🔑 Per-Device Unlock Profiles** keyed by `ro.build.fingerprint` (PIN, Password, Pattern, Custom Shell).

---

## 🛠️ Prerequisites

1. **Android Platform Tools (`adb`)** installed and added to your system `PATH`.
2. *(Optional, recommended)* **[scrcpy](https://github.com/Genymobile/scrcpy)** for screen mirroring, background camera, and live mic:
   ```powershell
   winget install Genymobile.scrcpy
   ```
3. *(Only if running from source)* **Python 3.10+**.

---

## 🚀 Run From Source

```powershell
git clone https://github.com/sabbir-repo/ADB-Control-Pro.git
cd ADB-Control-Pro
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# Launch Modern Glassmorphism Desktop UI (PyWebView)
python web_ui.py

# Or Legacy Terminal CLI
python main.py
```

---

## 🏗️ Build Native Windows EXE (Nuitka)

```powershell
pip install nuitka
python build_exe.py
```
The compiled standalone executable will be generated at `dist_nuitka/ADBControlPro_v1.0.0.exe`.

---

## 🔒 Privacy & Local Data Safety

Sensitive device files are **strictly local and excluded via `.gitignore`**:
- `device_profiles.json` (your saved unlock PINs/patterns)
- `device_sim_numbers.json` (your saved SIM phone numbers)
- `reconnect_state.json` (your local LAN device IPs)
- `screenshots/`, `recordings/`, and `*.apk`

---

## 📜 License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)** — see the [LICENSE](LICENSE) file for full details.

Copyright © 2026 **[Sabbir Ahmmed](https://github.com/sabbir-repo)**
