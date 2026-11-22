#!/usr/bin/env python3
"""
Ultra-Modern Glassmorphism Desktop UI for ADB Phone Control (PyWebView).
Keeps `ui.py` 100% untouched while reusing all existing Python engines:
`main.py`, `macros.py`, `action_recorder.py`, and `device_profiles.py`.
"""

from __future__ import annotations

import base64
import io
import os
import queue
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from action_recorder import PhysicalActionRecorder
from device_profiles import (
    DeviceProfileStore,
    LOCK_TYPES,
    UNLOCK_PROCESSES,
    _resolve_swipe_coords,
    smart_unlock_device,
)
from macros import MacroStore, execute_macro
from main import ADB, ADBError
from reconnect_watchdog import AutoReconnectWatchdog, scan_subnet_port

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "web_assets")


def _ok(data: Any = None, message: str = "") -> dict:
    return {"ok": True, "data": data, "message": message}


def _err(exc: Any) -> dict:
    return {"ok": False, "error": str(exc)}


class ADBWebAPI:
    """Thread-safe Python bridge exposed to the PyWebView frontend via `window.pywebview.api`."""

    def __init__(self):
        self._adb = ADB(timeout=30)
        self._profile_store = DeviceProfileStore()
        self._macro_store = MacroStore(os.path.join(BASE_DIR, "macros.json"))
        self._window = None

        self._current_fingerprint: str = ""
        self._action_recorder: Optional[PhysicalActionRecorder] = None
        self._recorder_queue: "queue.Queue[dict]" = queue.Queue()
        self._macro_stop_event: Optional[threading.Event] = None
        self._logcat_proc: Optional[subprocess.Popen] = None
        self._logcat_queue: "queue.Queue[str]" = queue.Queue()
        self._auto_sync_stop: Optional[threading.Event] = None
        self._qr_zc = None
        self._qr_status: str = ""
        self._qr_connected: bool = False
        self._wd_queue: "queue.Queue[dict]" = queue.Queue()
        self._watchdog = AutoReconnectWatchdog(
            self._adb,
            on_event=lambda st, log: self._wd_queue.put({"kind": "event", "status": st, "log": log}),
            on_reconnected=lambda target: self._wd_queue.put({"kind": "reconnected", "target": target}),
        )

    def _require_device(self) -> None:
        if not self._adb.serial:
            devs = ADB.list_devices()
            online = [d for d in devs if d.get("state") == "device"]
            if online:
                self._adb.serial = online[0]["serial"]
            else:
                raise ADBError("কোনো Android ডিভাইস কানেক্ট করা নেই। প্রথমে ডিভাইস কানেক্ট ও Refresh করুন।")

    # ─────────────────────────── Device & Live Telemetry ───────────────────────────
    def list_devices(self) -> dict:
        try:
            devs = ADB.list_devices()
            if devs and not self._adb.serial:
                online = [d for d in devs if d.get("state") == "device"]
                if online:
                    self._adb.serial = online[0]["serial"]
            return _ok({"devices": devs, "selected": self._adb.serial or ""})
        except Exception as e:
            return _err(e)

    def select_device(self, serial: str) -> dict:
        try:
            self._adb.serial = (serial or "").strip() or None
            self._current_fingerprint = ""
            if self._adb.serial:
                self._current_fingerprint = self._adb.shell("getprop ro.build.fingerprint", check=False, timeout=5).strip()
            return _ok({"serial": self._adb.serial or "", "fingerprint": self._current_fingerprint}, f"Active Device: {self._adb.serial}")
        except Exception as e:
            return _err(e)

    def device_telemetry(self) -> dict:
        try:
            if not self._adb.serial:
                devs = ADB.list_devices()
                online = [d for d in devs if d.get("state") == "device"]
                if not online:
                    return _ok({"connected": False})
                self._adb.serial = online[0]["serial"]

            model = self._adb.shell("getprop ro.product.model", check=False, timeout=5).strip()
            android_ver = self._adb.shell("getprop ro.build.version.release", check=False, timeout=5).strip()
            fp = self._adb.shell("getprop ro.build.fingerprint", check=False, timeout=5).strip()
            if fp:
                self._current_fingerprint = fp

            bat_raw = self._adb.shell("dumpsys battery", check=False, timeout=5)
            level = None
            temp = None
            charging = False
            m_lvl = re.search(r"level:\s*(\d+)", bat_raw)
            if m_lvl:
                level = int(m_lvl.group(1))
            m_tmp = re.search(r"temperature:\s*(\d+)", bat_raw)
            if m_tmp:
                temp = round(int(m_tmp.group(1)) / 10.0, 1)
            m_st = re.search(r"status:\s*(\d+)", bat_raw)
            if m_st and m_st.group(1) in ("2", "5"):
                charging = True

            is_wifi = ":" in (self._adb.serial or "")
            return _ok(
                {
                    "connected": bool(model or level is not None),
                    "serial": self._adb.serial,
                    "model": model or self._adb.serial,
                    "android": android_ver or "?",
                    "battery": level,
                    "charging": charging,
                    "temp": temp,
                    "connection": "Wi-Fi" if is_wifi else "USB",
                    "fingerprint": fp,
                }
            )
        except Exception as e:
            return _err(e)

    def connect_wifi(self, target: str) -> dict:
        try:
            t = (target or "").strip()
            if not t:
                raise ADBError("IP:PORT লিখুন (যেমন: 192.168.0.105:5555)।")
            out = self._adb.run(["connect", t], check=False, timeout=12)
            return _ok(out, out)
        except Exception as e:
            return _err(e)

    def disconnect_device(self) -> dict:
        try:
            out = self._adb.run(["disconnect"], check=False, timeout=8)
            self._adb.serial = None
            return _ok(out, out or "Disconnected")
        except Exception as e:
            return _err(e)

    def restart_adb(self) -> dict:
        try:
            self._adb.run(["kill-server"], check=False, timeout=8)
            out = self._adb.run(["start-server"], check=False, timeout=12)
            return _ok(out, "ADB Server Restarted")
        except Exception as e:
            return _err(e)

    def reboot_device(self) -> dict:
        try:
            self._require_device()
            out = self._adb.reboot()
            return _ok(out, "ডিভাইস রিবুট কমান্ড পাঠানো হয়েছে।")
        except Exception as e:
            return _err(e)

    def exec_shell(self, command: str) -> dict:
        try:
            self._require_device()
            cmd = (command or "").strip()
            if not cmd:
                raise ADBError("শেল কমান্ড খালি।")
            out = self._adb.shell(cmd, check=False, timeout=20)
            return _ok(out, f"$ {cmd}")
        except Exception as e:
            return _err(e)

    def keyevent(self, code: str) -> dict:
        try:
            self._require_device()
            out = self._adb.keyevent(code)
            return _ok(out, f"Key sent: {code}")
        except Exception as e:
            return _err(e)

    def send_text(self, text: str) -> dict:
        try:
            self._require_device()
            out = self._adb.send_unicode_text(text, auto_paste=True)
            return _ok(out, "Text sent")
        except Exception as e:
            return _err(e)

    def tap(self, x: int, y: int) -> dict:
        try:
            self._require_device()
            out = self._adb.tap(int(x), int(y))
            return _ok(out, f"Tap ({x}, {y})")
        except Exception as e:
            return _err(e)

    def wake_unlock(self) -> dict:
        try:
            self._require_device()
            self._adb.shell("svc power stayon true", check=False)
            self._adb.keyevent("KEYCODE_WAKEUP")
            time.sleep(0.8)
            coords = _resolve_swipe_coords(self._adb, "auto")
            self._adb.swipe(*coords, duration=400)
            return _ok("OK", "Wake & Swipe Up সম্পন্ন")
        except Exception as e:
            return _err(e)

    def open_url(self, url: str) -> dict:
        try:
            self._require_device()
            out = self._adb.open_url(url)
            return _ok(out, f"Opened URL: {url}")
        except Exception as e:
            return _err(e)

    def pull_clipboard(self) -> dict:
        try:
            self._require_device()
            txt = self._adb.get_phone_clipboard()
            return _ok(txt, "Phone clipboard read")
        except Exception as e:
            return _err(e)

    def push_clipboard(self, text: str) -> dict:
        try:
            self._require_device()
            out = self._adb.set_phone_clipboard(text)
            return _ok(out, out)
        except Exception as e:
            return _err(e)

    # ─────────────────────────── Apps & Permissions ───────────────────────────
    def list_launcher_apps(self) -> dict:
        try:
            self._require_device()
            apps = self._adb.list_launcher_apps()
            return _ok(apps, f"{len(apps)}টি লঞ্চার অ্যাপ লোড হয়েছে।")
        except Exception as e:
            return _err(e)

    def launch_app(self, pkg: str) -> dict:
        try:
            self._require_device()
            out = self._adb.launch_app(pkg)
            return _ok(out, f"Launched {pkg}")
        except Exception as e:
            return _err(e)

    def force_stop(self, pkg: str) -> dict:
        try:
            self._require_device()
            out = self._adb.force_stop(pkg)
            return _ok(out, f"Force stopped {pkg}")
        except Exception as e:
            return _err(e)

    def clear_data(self, pkg: str) -> dict:
        try:
            self._require_device()
            out = self._adb.clear_data(pkg)
            return _ok(out, f"Cleared data: {pkg}")
        except Exception as e:
            return _err(e)

    def uninstall_app(self, pkg: str) -> dict:
        try:
            self._require_device()
            out = self._adb.uninstall_app(pkg)
            return _ok(out, f"Uninstalled {pkg}")
        except Exception as e:
            return _err(e)

    def install_apk_dialog(self) -> dict:
        try:
            self._require_device()
            if not self._window:
                raise ADBError("Window ready নয়।")
            import webview

            paths = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("APK Files (*.apk)",))
            if not paths:
                return _ok(None, "Cancelled")
            apk_path = paths[0]
            out = self._adb.install_apk(apk_path)
            return _ok(out, f"Installed {os.path.basename(apk_path)}")
        except Exception as e:
            return _err(e)

    def permission_action(self, pkg: str, perm: str, mode: str) -> dict:
        try:
            self._require_device()
            if mode == "grant":
                out = self._adb.grant_permission(pkg, perm)
            elif mode == "revoke":
                out = self._adb.revoke_permission(pkg, perm)
            else:
                out = self._adb.list_app_permissions(pkg)
            return _ok(out, f"Permission ({mode}): {pkg}")
        except Exception as e:
            return _err(e)

    # ─────────────────────────── Interactive File Explorer & Media ───────────────────────────
    def list_dir(self, remote_dir: str) -> dict:
        try:
            self._require_device()
            entries = self._adb.list_directory_entries(remote_dir)
            return _ok({"path": remote_dir.rstrip("/") or "/", "entries": entries})
        except Exception as e:
            return _err(e)

    def mkdir(self, remote_path: str) -> dict:
        try:
            self._require_device()
            out = self._adb.mkdir(remote_path)
            return _ok(out, f"Folder created: {remote_path}")
        except Exception as e:
            return _err(e)

    def rename_path(self, old_path: str, new_path: str) -> dict:
        try:
            self._require_device()
            out = self._adb.rename_path(old_path, new_path)
            return _ok(out, f"Renamed → {os.path.basename(new_path)}")
        except Exception as e:
            return _err(e)

    def delete_path(self, remote_path: str) -> dict:
        try:
            self._require_device()
            out = self._adb.rm(remote_path)
            return _ok(out, f"Deleted: {remote_path}")
        except Exception as e:
            return _err(e)

    def download_item_dialog(self, remote_path: str, is_dir: bool) -> dict:
        try:
            self._require_device()
            if not self._window:
                raise ADBError("Window ready নয়।")
            import webview

            folders = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not folders:
                return _ok(None, "Cancelled")
            save_dir = folders[0]
            name = os.path.basename(remote_path.rstrip("/")) or "download"
            dst = save_dir if is_dir else os.path.join(save_dir, name)
            out = self._adb.pull(remote_path, dst)
            return _ok({"saved_to": dst, "output": out}, f"ডাউনলোড সম্পন্ন: {name}")
        except Exception as e:
            return _err(e)

    def upload_here_dialog(self, remote_dir: str) -> dict:
        try:
            self._require_device()
            if not self._window:
                raise ADBError("Window ready নয়।")
            import webview

            files = self._window.create_file_dialog(webview.FileDialog.OPEN)
            if not files:
                return _ok(None, "Cancelled")
            local_file = files[0]
            fname = os.path.basename(local_file)
            clean_dir = (remote_dir or "/sdcard").rstrip("/") or "/"
            dst = f"/{fname}" if clean_dir == "/" else f"{clean_dir}/{fname}"
            out = self._adb.push(local_file, dst)
            return _ok({"remote": dst, "output": out}, f"আপলোড সম্পন্ন: {fname}")
        except Exception as e:
            return _err(e)

    @staticmethod
    def _copy_image_to_clipboard(image_path: str) -> bool:
        if os.name != "nt":
            return False
        abs_path = os.path.abspath(image_path).replace("'", "''")
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
            f"$img = [System.Drawing.Image]::FromFile('{abs_path}'); "
            "[System.Windows.Forms.Clipboard]::SetImage($img); "
            "$img.Dispose()"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def take_screenshot(self) -> dict:
        try:
            self._require_device()
            shots_dir = os.path.join(BASE_DIR, "screenshots")
            os.makedirs(shots_dir, exist_ok=True)
            fname = f"shot_{time.strftime('%Y%m%d_%H%M%S')}.png"
            local_path = os.path.join(shots_dir, fname)
            self._adb.screenshot(local_path)
            copied = self._copy_image_to_clipboard(local_path)
            with open(local_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            msg = f"📸 স্ক্রিনশট সেভ ও পিসি ক্লিপবোর্ডে কপি হয়েছে: {fname}" if copied else f"📸 স্ক্রিনশট সেভ হয়েছে: {fname}"
            return _ok(
                {"path": local_path, "filename": fname, "data_url": f"data:image/png;base64,{b64}", "copied": copied},
                msg,
            )
        except Exception as e:
            return _err(e)

    # ─────────────────────────── System Monitor ───────────────────────────
    def monitor_query(self, kind: str) -> dict:
        try:
            self._require_device()
            mapping = {
                "battery": lambda: self._adb.shell("dumpsys battery", check=False),
                "memory": lambda: self._adb.shell("cat /proc/meminfo | head -n 25", check=False),
                "cpu": lambda: self._adb.shell("top -n 1 -b | head -n 25", check=False),
                "storage": lambda: self._adb.shell("df -h", check=False),
                "network": lambda: self._adb.shell("ip addr show wlan0; dumpsys wifi | grep -E 'mWifiInfo|SSID|RSSI' | head -n 12", check=False),
                "processes": lambda: self._adb.shell("ps -A | head -n 45", check=False),
                "props": lambda: self._adb.shell("getprop | grep -E 'model|brand|release|sdk|fingerprint|abi' | head -n 30", check=False),
            }
            fn = mapping.get(kind)
            if not fn:
                raise ADBError(f"Unknown monitor kind: {kind}")
            return _ok(fn(), f"Monitor: {kind}")
        except Exception as e:
            return _err(e)

    # ─────────────────────────── Screen Share, Camera, HID & Virtual Display ───────────────────────────
    def _spawn_scrcpy(self, extra_args: List[str], title: str) -> dict:
        self._require_device()
        args = ["scrcpy", "-s", self._adb.serial] + extra_args
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return _ok(" ".join(args), f"{title} চালু হয়েছে!")

    def start_scrcpy(self, mode: str = "standard", custom_args: str = "") -> dict:
        try:
            presets = {
                "standard": [],
                "smooth": ["-m", "1024", "-b", "4M", "--max-fps", "60"],
                "screen_off": ["-S", "-w"],
                "always_top": ["-w", "--always-on-top"],
                "read_only": ["-n"],
                "hid": ["--no-video", "--no-audio", "--keyboard=uhid", "--mouse=uhid"],
                "cam_back": ["--video-source=camera", "--camera-facing=back", "--camera-size=1280x720", "--orientation=90", "--no-audio", "--window-title=Phone Back Camera"],
                "cam_front": ["--video-source=camera", "--camera-facing=front", "--camera-size=1280x720", "--orientation=270", "--no-audio", "--window-title=Phone Front Camera"],
            }
            if mode == "custom":
                extra = shlex.split(custom_args or "")
            else:
                extra = presets.get(mode, [])
            return self._spawn_scrcpy(extra, f"scrcpy ({mode})")
        except Exception as e:
            return _err(e)

    def launch_virtual_display(self, pkg: str, size: str) -> dict:
        try:
            args = [f"--new-display={size}" if (size and size != "Auto") else "--new-display"]
            if (pkg or "").strip():
                args.append(f"--start-app=+{pkg.strip()}")
            return self._spawn_scrcpy(args, "Virtual Display")
        except Exception as e:
            return _err(e)

    def stop_scrcpy(self) -> dict:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/IM", "scrcpy.exe", "/F"], capture_output=True)
            else:
                subprocess.run(["pkill", "-f", "scrcpy"], capture_output=True)
            return _ok("OK", "সব scrcpy সেশন বন্ধ করা হয়েছে।")
        except Exception as e:
            return _err(e)

    # ─────────────────────────── Quick Controls ───────────────────────────
    def system_toggle(self, kind: str, value: Any) -> dict:
        try:
            self._require_device()
            if kind == "data":
                out = self._adb.set_mobile_data(bool(value))
            elif kind == "bluetooth":
                out = self._adb.set_bluetooth(bool(value))
            elif kind == "location":
                out = self._adb.set_location(bool(value))
            elif kind == "flashlight":
                out = self._adb.set_flashlight(bool(value))
            elif kind == "brightness":
                out = self._adb.set_brightness(int(value))
            elif kind == "timeout":
                out = self._adb.set_screen_timeout(int(value))
            else:
                raise ADBError(f"Unknown control: {kind}")
            return _ok(out, out)
        except Exception as e:
            return _err(e)

    # ─────────────────────────── Profiles & 1-Click Unlock ───────────────────────────
    def list_profiles(self) -> dict:
        try:
            return _ok(
                {
                    "profiles": self._profile_store.list_all(),
                    "lock_types": LOCK_TYPES,
                    "unlock_processes": UNLOCK_PROCESSES,
                }
            )
        except Exception as e:
            return _err(e)

    def detect_profile(self) -> dict:
        try:
            self._require_device()
            fp = self._adb.shell("getprop ro.build.fingerprint", check=False, timeout=6).strip()
            model = self._adb.shell("getprop ro.product.model", check=False, timeout=5).strip()
            self._current_fingerprint = fp
            existing = self._profile_store.get(fp)
            return _ok({"fingerprint": fp, "model": model, "profile": existing})
        except Exception as e:
            return _err(e)

    def save_profile(self, profile: dict) -> dict:
        try:
            fp = (profile.get("fingerprint") or "").strip()
            saved = self._profile_store.upsert(fp, profile)
            return _ok(saved, f"Profile সেভ হয়েছে: {saved['name']}")
        except Exception as e:
            return _err(e)

    def delete_profile(self, fingerprint: str) -> dict:
        try:
            ok = self._profile_store.delete(fingerprint)
            return _ok(ok, "Profile ডিলিট করা হয়েছে।" if ok else "Profile পাওয়া যায়নি।")
        except Exception as e:
            return _err(e)

    def one_click_unlock(self) -> dict:
        try:
            self._require_device()
            fp = self._current_fingerprint or self._adb.shell("getprop ro.build.fingerprint", check=False, timeout=5).strip()
            prof = self._profile_store.get(fp)
            if not prof:
                raise ADBError("এই ডিভাইসের কোনো Saved Profile নেই। Profiles ট্যাবে গিয়ে আগে সেভ করুন।")
            out = smart_unlock_device(self._adb, prof)
            return _ok(out, f"🔓 {prof['name']} আনলক সম্পন্ন!")
        except Exception as e:
            return _err(e)

    # ─────────────────────────── Comms (Notifications, SMS/OTP & Calls) ───────────────────────────
    def list_notifications(self) -> dict:
        try:
            self._require_device()
            items = self._adb.list_notifications(limit=40)
            return _ok(items, f"🔔 {len(items)}টি নোটিফিকেশন লোড হয়েছে।")
        except Exception as e:
            return _err(e)

    def list_sms(self) -> dict:
        try:
            self._require_device()
            items = self._adb.list_sms(limit=25)
            return _ok(items, f"📩 {len(items)}টি SMS লোড হয়েছে।")
        except Exception as e:
            return _err(e)

    def list_call_logs(self) -> dict:
        try:
            self._require_device()
            items = self._adb.list_call_logs(limit=25)
            return _ok(items, f"☎️ {len(items)}টি কল লগ লোড হয়েছে।")
        except Exception as e:
            return _err(e)

    def dial_number(self, number: str) -> dict:
        try:
            self._require_device()
            out = self._adb.dial_number(number)
            return _ok(out, f"📞 Dialing {number}")
        except Exception as e:
            return _err(e)

    def call_action(self, action: str) -> dict:
        try:
            self._require_device()
            out = self._adb.call_action(action)
            return _ok(out, f"Call {action}")
        except Exception as e:
            return _err(e)

    # ─────────────────────────── Macros & Physical Action Recorder ───────────────────────────
    def list_macros(self) -> dict:
        try:
            self._macro_store.load()
            return _ok({"names": self._macro_store.names(), "macros": self._macro_store.macros})
        except Exception as e:
            return _err(e)

    def save_macro(self, name: str, steps: List[dict]) -> dict:
        try:
            self._macro_store.set_macro(name, steps)
            return _ok({"names": self._macro_store.names()}, f"💾 '{name}' ({len(steps)} steps) সেভ হয়েছে।")
        except Exception as e:
            return _err(e)

    def delete_macro(self, name: str) -> dict:
        try:
            self._macro_store.delete_macro(name)
            return _ok({"names": self._macro_store.names()}, f"🗑️ '{name}' ডিলিট হয়েছে।")
        except Exception as e:
            return _err(e)

    def start_recording(self, mode: str, record_waits: bool, track_apps: bool) -> dict:
        try:
            self._require_device()
            if self._action_recorder is not None:
                self._action_recorder.stop()
                self._action_recorder = None
            while not self._recorder_queue.empty():
                try:
                    self._recorder_queue.get_nowait()
                except queue.Empty:
                    break

            direct = mode == "direct"
            smart = mode == "smart"

            rec = PhysicalActionRecorder(
                self._adb,
                on_step=lambda step, log: self._recorder_queue.put({"kind": "step", "step": step, "log": log}),
                on_status=lambda msg: self._recorder_queue.put({"kind": "status", "message": msg}),
                on_replace_last=lambda step, log: self._recorder_queue.put({"kind": "replace", "step": step, "log": log}),
                smart_mode=smart,
                direct_mode=direct,
                record_waits=bool(record_waits),
                track_apps=bool(track_apps),
            )
            self._action_recorder = rec
            cal = rec.start()
            return _ok(
                {
                    "mode": mode,
                    "screen": f"{cal.screen_w}x{cal.screen_h}",
                    "max": f"{cal.max_x}x{cal.max_y}",
                    "nav_mode": cal.nav_mode,
                },
                f"🔴 Recording শুরু হয়েছে ({mode})",
            )
        except Exception as e:
            self._action_recorder = None
            return _err(e)

    def toggle_pause_recording(self) -> dict:
        if self._action_recorder is None:
            return _err("কোনো রেকর্ডিং চালু নেই।")
        if self._action_recorder.is_paused:
            self._action_recorder.resume()
            return _ok({"paused": False}, "▶️ Recording Resumed")
        self._action_recorder.pause()
        return _ok({"paused": True}, "⏸️ Recording Paused")

    def stop_recording(self) -> dict:
        if self._action_recorder is not None:
            self._action_recorder.stop()
            self._action_recorder = None
        return _ok(True, "⏹️ Recording বন্ধ হয়েছে।")

    def poll_recorder_events(self) -> dict:
        events: List[dict] = []
        while len(events) < 50:
            try:
                events.append(self._recorder_queue.get_nowait())
            except queue.Empty:
                break
        return _ok(
            {
                "recording": self._action_recorder is not None,
                "paused": self._action_recorder.is_paused if self._action_recorder else False,
                "events": events,
            }
        )

    def run_macro(self, steps: List[dict], repeat: int = 1) -> dict:
        try:
            self._require_device()
            if self._action_recorder is not None:
                raise ADBError("ম্যাক্রো চালানোর আগে Recording বন্ধ করুন।")
            prof = self._profile_store.get(self._current_fingerprint) if self._current_fingerprint else None
            stop_ev = threading.Event()
            self._macro_stop_event = stop_ev
            out = execute_macro(self._adb, steps, profile=prof, repeat=int(repeat or 1), stop_event=stop_ev)
            return _ok(out, "Macro সম্পন্ন")
        except Exception as e:
            return _err(e)

    def stop_macro(self) -> dict:
        if self._macro_stop_event is not None:
            self._macro_stop_event.set()
        return _ok(True, "⏹️ Macro থামানোর সিগন্যাল পাঠানো হয়েছে।")

    # ─────────────────────────── Full Parity Features (Logs, Apps, Files, Monitor, scrcpy, WiFi & Live) ───────────────────────────
    def check_connection(self) -> dict:
        try:
            self._require_device()
            model = self._adb.shell("getprop ro.product.model", check=False).strip()
            brand = self._adb.shell("getprop ro.product.manufacturer", check=False).strip()
            ver = self._adb.shell("getprop ro.build.version.release", check=False).strip()
            sdk = self._adb.shell("getprop ro.build.version.sdk", check=False).strip()
            fp = self._adb.shell("getprop ro.build.fingerprint", check=False).strip()
            info = (
                f"Serial      : {self._adb.serial}\n"
                f"Brand/Model : {brand} {model}\n"
                f"Android/SDK : Android {ver} (API {sdk})\n"
                f"Fingerprint : {fp}"
            )
            return _ok(info, f"Connected: {model}")
        except Exception as e:
            return _err(e)

    def wait_device(self) -> dict:
        try:
            out = self._adb.run(["wait-for-device"], check=False, timeout=25)
            return _ok(out or "Device Online!", "ডিভাইস কানেক্ট হয়েছে!")
        except Exception as e:
            return _err(e)

    def fetch_logs(self, lines: int = 150, mode: str = "All", keyword: str = "") -> dict:
        try:
            self._require_device()
            n = max(10, min(int(lines or 150), 2000))
            lvl_flag = ""
            if mode == "Errors":
                lvl_flag = "*:E"
            elif mode == "Warnings":
                lvl_flag = "*:W"
            cmd = f"logcat -d -t {n} {lvl_flag}".strip()
            raw = self._adb.shell(cmd, check=False, timeout=15)
            kw = (keyword or "").strip().lower()
            if kw:
                filtered = [ln for ln in raw.splitlines() if kw in ln.lower()]
                raw = "\n".join(filtered) or f"No log lines matching '{keyword}'"
            return _ok(raw, f"Fetched {n} log lines ({mode})")
        except Exception as e:
            return _err(e)

    def batch_install_dialog(self) -> dict:
        try:
            self._require_device()
            if not self._window:
                raise ADBError("Window ready নয়।")
            import webview

            folders = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not folders:
                return _ok(None, "Cancelled")
            folder = folders[0]
            apks = [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.lower().endswith(".apk")]
            if not apks:
                raise ADBError("এই ফোল্ডারে কোনো .apk ফাইল পাওয়া যায়নি।")
            results = []
            for apk in apks:
                out = self._adb.install_apk(apk)
                results.append(f"• {os.path.basename(apk)}: {out.strip()}")
            return _ok("\n".join(results), f"{len(apks)}টি APK ইনস্টল সম্পন্ন!")
        except Exception as e:
            return _err(e)

    def current_activity(self) -> dict:
        try:
            self._require_device()
            out = self._adb.current_activity()
            return _ok(out, "Current Activity")
        except Exception as e:
            return _err(e)

    def package_info(self, pkg: str) -> dict:
        try:
            self._require_device()
            out = self._adb.package_info(pkg)
            return _ok(out[:6000], f"Package Info: {pkg}")
        except Exception as e:
            return _err(e)

    def backup_apk_dialog(self, pkg: str) -> dict:
        try:
            self._require_device()
            if not pkg.strip():
                raise ADBError("প্যাকেজের নাম সিলেক্ট করুন।")
            if not self._window:
                raise ADBError("Window ready নয়।")
            import webview

            raw_path = self._adb.shell(f"pm path {shlex.quote(pkg.strip())}", check=False)
            apk_remote = ""
            for ln in raw_path.splitlines():
                if ln.startswith("package:"):
                    apk_remote = ln.split("package:", 1)[1].strip()
                    break
            if not apk_remote:
                raise ADBError(f"APK পাথ পাওয়া যায়নি: {pkg}")

            folders = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not folders:
                return _ok(None, "Cancelled")
            dst = os.path.join(folders[0], f"{pkg.strip()}.apk")
            out = self._adb.pull(apk_remote, dst)
            return _ok(out, f"💾 APK ব্যাকআপ সেভ হয়েছে: {pkg.strip()}.apk")
        except Exception as e:
            return _err(e)

    def burst_screenshots(self, count: int = 3) -> dict:
        try:
            self._require_device()
            n = max(2, min(int(count or 3), 10))
            shots_dir = os.path.join(BASE_DIR, "screenshots")
            os.makedirs(shots_dir, exist_ok=True)
            saved = []
            for i in range(1, n + 1):
                fname = f"burst_{time.strftime('%Y%m%d_%H%M%S')}_{i}.png"
                lp = os.path.join(shots_dir, fname)
                self._adb.screenshot(lp)
                saved.append(lp)
                if i < n:
                    time.sleep(0.5)
            return _ok("\n".join(saved), f"🎞️ {n}টি Burst Screenshot সেভ হয়েছে!")
        except Exception as e:
            return _err(e)

    def record_screen(self, seconds: int = 10) -> dict:
        try:
            self._require_device()
            sec = max(2, min(int(seconds or 10), 180))
            rec_dir = os.path.join(BASE_DIR, "recordings")
            os.makedirs(rec_dir, exist_ok=True)
            fname = f"screen_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
            lp = os.path.join(rec_dir, fname)
            out = self._adb.screenrecord(lp, sec)
            return _ok(out, f"🎥 {sec}s স্ক্রিন রেকর্ডিং সেভ হয়েছে: {fname}")
        except Exception as e:
            return _err(e)

    def sync_folder_dialog(self, remote_dir: str, direction: str = "pull") -> dict:
        try:
            self._require_device()
            if not self._window:
                raise ADBError("Window ready নয়।")
            import webview

            folders = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not folders:
                return _ok(None, "Cancelled")
            local_dir = folders[0]
            rem = (remote_dir or "/sdcard/Download").strip()
            if direction == "push":
                out = self._adb.push(local_dir, rem)
                return _ok(out, f"⬆️ PC → Phone Sync সম্পন্ন ({rem})")
            out = self._adb.pull(rem, local_dir)
            return _ok(out, f"⬇️ Phone → PC Backup সম্পন্ন ({local_dir})")
        except Exception as e:
            return _err(e)

    def toggle_auto_sync(self, remote_dir: str) -> dict:
        try:
            if self._auto_sync_stop is not None:
                self._auto_sync_stop.set()
                self._auto_sync_stop = None
                return _ok({"active": False}, "⏹️ Auto-Sync Watch বন্ধ করা হয়েছে।")

            self._require_device()
            if not self._window:
                raise ADBError("Window ready নয়।")
            import webview

            folders = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not folders:
                return _ok({"active": False}, "Cancelled")
            local_dir = folders[0]
            rem = (remote_dir or "/sdcard/Download").strip().rstrip("/") or "/sdcard/Download"
            stop_ev = threading.Event()
            self._auto_sync_stop = stop_ev

            def watcher():
                seen: Dict[str, float] = {}
                for f in os.listdir(local_dir):
                    fp = os.path.join(local_dir, f)
                    if os.path.isfile(fp):
                        seen[fp] = os.path.getmtime(fp)
                while not stop_ev.is_set():
                    try:
                        for f in os.listdir(local_dir):
                            fp = os.path.join(local_dir, f)
                            if not os.path.isfile(fp):
                                continue
                            mt = os.path.getmtime(fp)
                            if fp not in seen or mt > seen[fp]:
                                seen[fp] = mt
                                self._adb.push(fp, f"{rem}/{f}")
                                self._logcat_queue.put(f"👁️ [Auto-Sync] Pushed {f} → {rem}/{f}")
                    except Exception:
                        pass
                    stop_ev.wait(2.5)

            threading.Thread(target=watcher, daemon=True).start()
            return _ok({"active": True, "local": local_dir, "remote": rem}, f"👁️ Auto-Sync চালু: {os.path.basename(local_dir)} → {rem}")
        except Exception as e:
            return _err(e)

    def apps_with_size(self) -> dict:
        try:
            self._require_device()
            out = self._adb.shell(
                "pm list packages -3 -f | head -n 40",
                check=False,
                timeout=15,
            )
            return _ok(out, "3rd-Party Apps & APK Paths")
        except Exception as e:
            return _err(e)

    def perf_snapshot(self) -> dict:
        try:
            self._require_device()
            bat = self._adb.shell("dumpsys battery | grep -E 'level|temperature|status'", check=False, timeout=5)
            mem = self._adb.shell("cat /proc/meminfo | head -n 5", check=False, timeout=5)
            cpu = self._adb.shell("top -n 1 -b | head -n 10", check=False, timeout=8)
            snap = f"=== BATTERY ===\n{bat}\n\n=== MEMORY ===\n{mem}\n\n=== TOP CPU ===\n{cpu}"
            return _ok(snap)
        except Exception as e:
            return _err(e)

    def multi_device_scrcpy(self) -> dict:
        try:
            devs = [d["serial"] for d in ADB.list_devices() if d.get("state") == "device"]
            if not devs:
                raise ADBError("কোনো অনলাইন ডিভাইস নেই।")
            for s in devs:
                subprocess.Popen(
                    ["scrcpy", "-s", s],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            return _ok(f"Launched for: {', '.join(devs)}", f"📱📱 {len(devs)}টি ডিভাইসে scrcpy চালু হয়েছে!")
        except Exception as e:
            return _err(e)

    def scrcpy_record_dialog(self) -> dict:
        try:
            self._require_device()
            rec_dir = os.path.join(BASE_DIR, "recordings")
            os.makedirs(rec_dir, exist_ok=True)
            fname = f"scrcpy_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
            lp = os.path.join(rec_dir, fname)
            return self._spawn_scrcpy(["--record", lp], f"scrcpy Recording ({fname})")
        except Exception as e:
            return _err(e)

    def scrcpy_sessions(self) -> dict:
        try:
            if os.name == "nt":
                out = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq scrcpy.exe"], text=True, errors="replace")
            else:
                out = subprocess.check_output(["pgrep", "-af", "scrcpy"], text=True, errors="replace")
            return _ok(out.strip(), "Active scrcpy Sessions")
        except Exception as e:
            return _ok("কোনো scrcpy সেশন চলছে না।", "No active scrcpy")

    def check_scrcpy(self) -> dict:
        try:
            out = subprocess.check_output(["scrcpy", "--version"], text=True, errors="replace", timeout=6)
            return _ok(out.strip(), "scrcpy OK")
        except Exception as e:
            return _err(f"scrcpy পাওয়া যায়নি: {e}")

    # ─── WiFi & Live Tools ───
    def enable_tcpip(self, port: int = 5555) -> dict:
        try:
            self._require_device()
            p = int(port or 5555)
            out = self._adb.tcpip(p)
            ip_raw = self._adb.shell("ip route | grep wlan0", check=False)
            m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", ip_raw)
            ip_hint = f"\nPhone Wi-Fi IP: {m.group(1)}:{p}" if m else ""
            return _ok((out or f"TCP/IP port {p} enabled") + ip_hint, f"TCP/IP ({p}) চালু হয়েছে!")
        except Exception as e:
            return _err(e)

    def transfer_all_tcpip(self, port: int = 5555) -> dict:
        try:
            p = int(port or 5555)
            devs = [d["serial"] for d in ADB.list_devices() if d.get("state") == "device" and ":" not in d["serial"]]
            if not devs:
                raise ADBError("কোনো USB কানেক্টেড ডিভাইস পাওয়া যায়নি।")
            logs = []
            for s in devs:
                a = ADB(s)
                a.tcpip(p)
                ip_raw = a.shell("ip route | grep wlan0", check=False)
                m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", ip_raw)
                if m:
                    target = f"{m.group(1)}:{p}"
                    time.sleep(0.8)
                    c_out = a.run(["connect", target], check=False, timeout=10)
                    logs.append(f"• {s} → {target}: {c_out}")
                else:
                    logs.append(f"• {s}: TCP/IP {p} enabled (Wi-Fi IP not found)")
            return _ok("\n".join(logs), f"📡 {len(devs)}টি USB ডিভাইস Wi-Fi-এ ট্রান্সফার করা হয়েছে!")
        except Exception as e:
            return _err(e)

    def wifi_status(self) -> dict:
        try:
            self._require_device()
            out = self._adb.shell(
                "ip addr show wlan0; echo '---'; dumpsys wifi | grep -E 'Wi-Fi is|mWifiInfo|SSID|Link speed' | head -n 15",
                check=False,
                timeout=10,
            )
            return _ok(out, "Wi-Fi Diagnostics")
        except Exception as e:
            return _err(e)

    def pair_wifi_manual(self, ip_port: str, code: str) -> dict:
        try:
            t = (ip_port or "").strip()
            c = (code or "").strip()
            if not t or not c:
                raise ADBError("Pairing IP:PORT এবং ৬-ডিজিট Pairing Code দুটিই দিন।")
            out = self._adb.run(["pair", t, c], check=False, timeout=15)
            return _ok(out, out)
        except Exception as e:
            return _err(e)

    def start_qr_pairing(self) -> dict:
        try:
            self.stop_qr_pairing()
            try:
                import qrcode
            except ImportError:
                raise ADBError("'qrcode' প্যাকেজ ইনস্টল নেই (pip install qrcode)।")

            service_name = f"studio-{secrets.token_hex(4)}"
            password = secrets.token_hex(6)
            qr_payload = f"WIFI:T:ADB;S:{service_name};P:{password};;"

            # Pure-Python SVG rendering via qr.get_matrix() — zero Pillow (PIL) dependency!
            qr = qrcode.QRCode(version=None, box_size=1, border=2)
            qr.add_data(qr_payload)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            n = len(matrix)
            rects = [
                f'<rect x="{x}" y="{y}" width="1" height="1"/>'
                for y, row in enumerate(matrix)
                for x, cell in enumerate(row)
                if cell
            ]
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
                f'<rect width="100%" height="100%" fill="#ffffff"/>'
                f'<g fill="#000000">{"".join(rects)}</g></svg>'
            )
            b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
            self._qr_status = "⏳ ফোনে Wireless Debugging → Pair device with QR code স্ক্যান করুন..."
            self._qr_connected = False

            try:
                from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

                zc = Zeroconf()
                self._qr_zc = zc

                def on_state(zeroconf, service_type, name, state_change):
                    if state_change != ServiceStateChange.Added:
                        return
                    info = zeroconf.get_service_info(service_type, name, timeout=3000)
                    if not info or not info.parsed_addresses():
                        return
                    ip = info.parsed_addresses()[0]
                    port = info.port
                    target = f"{ip}:{port}"
                    if "_adb-tls-pairing." in service_type:
                        self._qr_status = f"🔗 Pairing {target}..."
                        p_out = self._adb.run(["pair", target, password], check=False, timeout=15)
                        if "successfully" in p_out.lower():
                            self._qr_status = f"✔ Paired ({target})! Connection পোর্ট খোঁজা হচ্ছে..."
                        else:
                            self._qr_status = f"⚠️ Pair output: {p_out}"
                    elif "_adb-tls-connect." in service_type and not self._qr_connected:
                        c_out = self._adb.run(["connect", target], check=False, timeout=12)
                        if "connected" in c_out.lower():
                            self._qr_connected = True
                            self._adb.serial = target
                            self._qr_status = f"✔ সফলভাবে Wi-Fi ADB কানেক্ট হয়েছে: {target}"

                ServiceBrowser(zc, "_adb-tls-pairing._tcp.local.", handlers=[on_state])
                ServiceBrowser(zc, "_adb-tls-connect._tcp.local.", handlers=[on_state])
            except ImportError:
                self._qr_status = "ℹ️ 'zeroconf' না থাকায় শুধু QR জেনারেট হয়েছে; ম্যানুয়ালি IP:PORT দিয়ে Pair করুন।"

            return _ok(
                {"data_url": f"data:image/svg+xml;base64,{b64}", "status": self._qr_status},
                "QR Pairing Code তৈরি হয়েছে!",
            )
        except Exception as e:
            return _err(e)

    def poll_qr_status(self) -> dict:
        return _ok({"status": self._qr_status, "connected": self._qr_connected})

    def stop_qr_pairing(self) -> dict:
        if self._qr_zc is not None:
            try:
                self._qr_zc.close()
            except Exception:
                pass
            self._qr_zc = None
        return _ok(True)

    def start_live_logcat(self, keyword: str = "") -> dict:
        try:
            self._require_device()
            self.stop_live_logcat()
            kw = (keyword or "").strip().lower()
            cmd = self._adb._cmd() + ["logcat", "-v", "time"]
            self._logcat_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            def reader():
                proc = self._logcat_proc
                if not proc or not proc.stdout:
                    return
                for line in proc.stdout:
                    s = line.strip()
                    if not s:
                        continue
                    if kw and kw not in s.lower():
                        continue
                    self._logcat_queue.put(s)

            threading.Thread(target=reader, daemon=True).start()
            return _ok(True, "▶️ Live Logcat স্ট্রিম শুরু হয়েছে!")
        except Exception as e:
            return _err(e)

    def stop_live_logcat(self) -> dict:
        if self._logcat_proc is not None:
            try:
                self._logcat_proc.terminate()
            except Exception:
                pass
            self._logcat_proc = None
        return _ok(True, "⏹️ Live Logcat বন্ধ করা হয়েছে।")

    def poll_logcat(self) -> dict:
        lines: List[str] = []
        while len(lines) < 60:
            try:
                lines.append(self._logcat_queue.get_nowait())
            except queue.Empty:
                break
        return _ok({"running": self._logcat_proc is not None, "lines": lines})

    def set_timeout(self, sec: int) -> dict:
        try:
            val = max(5, min(int(sec or 30), 600))
            self._adb.timeout = val
            return _ok(val, f"Command Timeout সেট হয়েছে: {val}s")
        except Exception as e:
            return _err(e)

    def save_console_dialog(self, text: str) -> dict:
        try:
            if not self._window:
                raise ADBError("Window ready নয়।")
            import webview

            path = self._window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=f"adb_console_{time.strftime('%Y%m%d_%H%M%S')}.txt",
            )
            if not path:
                return _ok(None, "Cancelled")
            target = path[0] if isinstance(path, (list, tuple)) else path
            with open(target, "w", encoding="utf-8") as f:
                f.write(text or "")
            return _ok(target, f"💾 Console লগ সেভ হয়েছে: {os.path.basename(target)}")
        except Exception as e:
            return _err(e)

    # ─── Auto-Reconnect Watchdog & Fast 5555 Scan ───
    def toggle_watchdog(self, keep_alive: bool = True) -> dict:
        try:
            if self._watchdog.is_running:
                msg = self._watchdog.stop()
                return _ok({"running": False, "status": "Idle"}, msg)
            msg = self._watchdog.start(keep_alive=bool(keep_alive))
            return _ok({"running": True, "status": self._watchdog.status_text}, msg)
        except Exception as e:
            return _err(e)

    def poll_watchdog(self) -> dict:
        events: List[dict] = []
        while len(events) < 20:
            try:
                events.append(self._wd_queue.get_nowait())
            except queue.Empty:
                break
        return _ok(
            {
                "running": self._watchdog.is_running,
                "status": self._watchdog.status_text,
                "events": events,
            }
        )

    def scan_connect_5555(self, port: int = 5555) -> dict:
        try:
            p = int(port or 5555)
            found = scan_subnet_port(port=p, timeout=0.35)
            if not found:
                raise ADBError(f"লোকাল নেটওয়ার্কে পোর্ট {p} খোলা আছে এমন কোনো ফোন পাওয়া যায়নি।")
            connected = []
            for target in found:
                out = self._adb.run(["connect", target], check=False, timeout=8)
                low = (out or "").lower()
                if "connected to" in low or "already connected" in low:
                    self._adb.serial = target
                    self._watchdog.remember_device(target)
                    connected.append(target)
            if not connected:
                raise ADBError(f"পাওয়া গেছে ({', '.join(found)}), কিন্তু adb connect সফল হয়নি।")
            return _ok(connected, f"🟢 স্ক্যান করে কানেক্ট হয়েছে: {', '.join(connected)}")
        except Exception as e:
            return _err(e)

    def setup_companion_permissions(self) -> dict:
        """1-Click grants WRITE_SECURE_SETTINGS and whitelists ADB Companion from Android Doze/Freezer."""
        try:
            self._require_device()
            pkg = "com.adbcontrol.companion"
            cmds = [
                f"pm grant {pkg} android.permission.WRITE_SECURE_SETTINGS",
                f"pm grant {pkg} android.permission.ACCESS_FINE_LOCATION",
                f"pm grant {pkg} android.permission.ACCESS_COARSE_LOCATION",
                f"pm grant {pkg} android.permission.ACCESS_BACKGROUND_LOCATION",
                f"dumpsys deviceidle whitelist +{pkg}",
                f"cmd appops set {pkg} RUN_IN_BACKGROUND allow",
                f"cmd appops set {pkg} RUN_ANY_IN_BACKGROUND allow",
                f"cmd appops set {pkg} WAKE_LOCK allow",
            ]
            logs = []
            for c in cmds:
                out = self._adb.shell(c, check=False, timeout=10).strip()
                logs.append(f"$ {c}" + (f"\n  {out}" if out else " -> OK"))
            return _ok("\n".join(logs), "🛠️ Companion পারমিশন ও Whitelist ১-ক্লিকে সম্পন্ন!")
        except Exception as e:
            return _err(e)

    def install_companion_apk(self) -> dict:
        """1-Click installs `app-debug.apk` from project root (or picker), grants permissions, and launches it."""
        try:
            self._require_device()
            candidates = [
                os.path.join(BASE_DIR, "app-debug.apk"),
                os.path.join(BASE_DIR, "android_companion", "app", "build", "outputs", "apk", "debug", "app-debug.apk"),
            ]
            apk_path = next((p for p in candidates if os.path.exists(p)), None)
            if not apk_path:
                if not self._window:
                    raise ADBError("প্রজেক্ট ফোল্ডারে app-debug.apk পাওয়া যায়নি।")
                import webview

                paths = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("APK Files (*.apk)",))
                if not paths:
                    return _ok(None, "Cancelled")
                apk_path = paths[0]

            inst_out = self._adb.install_apk(apk_path)
            setup_res = self.setup_companion_permissions()
            self._adb.launch_app("com.adbcontrol.companion")
            summary = f"APK: {os.path.basename(apk_path)}\n{inst_out}\n\n{setup_res.get('data', '')}"
            return _ok(summary, "📲 Companion APK ইনস্টল, Whitelist এবং লঞ্চ সম্পন্ন!")
        except Exception as e:
            return _err(e)

    def _shutdown(self) -> None:
        if self._action_recorder:
            try:
                self._action_recorder.stop()
            except Exception:
                pass
        if self._macro_stop_event:
            self._macro_stop_event.set()
        if self._auto_sync_stop:
            self._auto_sync_stop.set()
        self._watchdog.stop()
        self.stop_live_logcat()
        self.stop_qr_pairing()


def main():
    try:
        import webview
    except ImportError:
        print("❌ 'pywebview' ইনস্টল করা নেই। ভার্চুয়াল এনভায়রনমেন্ট চালু করে নিচের কমান্ডটি চালান:")
        print("   pip install pywebview")
        sys.exit(1)

    index_html = os.path.join(ASSETS_DIR, "index.html")
    if not os.path.exists(index_html):
        print(f"❌ Missing UI asset: {index_html}")
        sys.exit(1)

    api = ADBWebAPI()
    window = webview.create_window(
        title="⚡ ADB Control Pro — Ultra-Modern Desktop Suite",
        url=index_html,
        js_api=api,
        width=1420,
        height=880,
        min_size=(1100, 680),
        background_color="#070b14",
    )
    api._window = window
    window.events.closed += api._shutdown
    webview.start(debug=False)


if __name__ == "__main__":
    main()
