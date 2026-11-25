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
    DeviceSimStore,
    LOCK_TYPES,
    UNLOCK_PROCESSES,
    _resolve_swipe_coords,
    smart_unlock_device,
)
from macros import MacroStore, execute_macro
from main import ADB, ADBError
from reconnect_watchdog import AutoReconnectWatchdog, scan_subnet_port

APP_VERSION = "1.0.0"
APP_DEVELOPER = "Sabbir Ahmmed"
APP_GITHUB = "https://github.com/sabbir-repo"

BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
_IS_FROZEN = bool("__compiled__" in globals() or getattr(sys, "frozen", False))
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if _IS_FROZEN else BUNDLE_DIR
ASSETS_DIR = os.path.join(BUNDLE_DIR, "web_assets")


def _ok(data: Any = None, message: str = "") -> dict:
    return {"ok": True, "data": data, "message": message}


def _err(exc: Any) -> dict:
    return {"ok": False, "error": str(exc)}


class ADBWebAPI:
    """Thread-safe Python bridge exposed to the PyWebView frontend via `window.pywebview.api`."""

    def __init__(self):
        self._adb = ADB(timeout=30)
        self._profile_store = DeviceProfileStore(os.path.join(BASE_DIR, "device_profiles.json"))
        self._sim_store = DeviceSimStore(os.path.join(BASE_DIR, "device_sim_numbers.json"))
        self._macro_store = MacroStore(os.path.join(BASE_DIR, "macros.json"))
        self._window = None

        self._current_fingerprint: str = ""
        self._device_meta_cache: Dict[str, dict] = {}
        self._action_recorder: Optional[PhysicalActionRecorder] = None
        self._recorder_queue: "queue.Queue[dict]" = queue.Queue(maxsize=500)
        self._macro_stop_event: Optional[threading.Event] = None
        self._logcat_proc: Optional[subprocess.Popen] = None
        self._logcat_queue: "queue.Queue[str]" = queue.Queue(maxsize=1000)
        self._auto_sync_stop: Optional[threading.Event] = None
        self._auto_sync_queue: "queue.Queue[str]" = queue.Queue(maxsize=300)
        self._transfer_lock = threading.Lock()
        self._transfer_cancel: Optional[threading.Event] = None
        self._preview_lock = threading.Lock()
        self._preview_cancel: Optional[threading.Event] = None
        self._transfer_state: Dict[str, Any] = {
            "active": False,
            "label": "",
            "pct": 0.0,
            "done": "",
            "total": "",
            "speed": "",
            "error": "",
            "finished_msg": "",
            "refresh_dir": "",
        }
        self._qr_zc = None
        self._qr_status: str = ""
        self._qr_connected: bool = False
        self._wd_queue: "queue.Queue[dict]" = queue.Queue(maxsize=200)
        self._watchdog = AutoReconnectWatchdog(
            self._adb,
            on_event=lambda st, log: self._put_bounded(self._wd_queue, {"kind": "event", "status": st, "log": log}),
            on_reconnected=lambda target: self._put_bounded(self._wd_queue, {"kind": "reconnected", "target": target}),
            state_path=os.path.join(BASE_DIR, "reconnect_state.json"),
        )

    @staticmethod
    def _put_bounded(q: "queue.Queue", item: Any) -> None:
        """Non-blocking rolling queue put: drops oldest entry when full to prevent memory leaks."""
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(item)
            except queue.Full:
                pass

    def _require_device(self) -> None:
        if not self._adb.serial:
            devs = ADB.list_devices()
            online = [d for d in devs if d.get("state") == "device"]
            if online:
                self._adb.serial = online[0]["serial"]
            else:
                raise ADBError("কোনো Android ডিভাইস কানেক্ট করা নেই। প্রথমে ডিভাইস কানেক্ট ও Refresh করুন।")

    def _pinned_adb(self, timeout: Optional[int] = None) -> ADB:
        """Returns a dedicated ADB instance locked to the currently selected serial (multi-device safe)."""
        self._require_device()
        return ADB(serial=self._adb.serial, timeout=timeout if timeout is not None else self._adb.timeout)

    # ─────────────────────────── Device & Live Telemetry ───────────────────────────
    def list_devices(self) -> dict:
        try:
            devs = ADB.list_devices()
            online = [d["serial"] for d in devs if d.get("state") == "device"]
            all_serials = {d["serial"] for d in devs}
            if devs:
                if not self._adb.serial or (self._adb.serial not in all_serials and online):
                    if online:
                        self._adb.serial = online[0]
                        self._current_fingerprint = ""
            else:
                self._adb.serial = None
                self._current_fingerprint = ""
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
            serial = self._adb.serial
            if not serial:
                devs = ADB.list_devices()
                online = [d for d in devs if d.get("state") == "device"]
                if not online:
                    return _ok({"connected": False})
                serial = online[0]["serial"]
                self._adb.serial = serial

            pinned = ADB(serial=serial, timeout=10)
            cached = self._device_meta_cache.get(serial)

            try:
                if cached:
                    # Lightweight single shell call for battery + Wi-Fi RSSI
                    model = cached["model"]
                    android_ver = cached["android"]
                    fp = cached["fingerprint"]
                    is_rooted = bool(cached.get("rooted", False))
                    raw = pinned.shell(
                        "dumpsys battery; echo '__WIFI__'; cmd wifi status 2>/dev/null || cat /proc/net/wireless 2>/dev/null",
                        check=False,
                        timeout=10,
                    )
                    w_parts = raw.split("__WIFI__")
                    bat_raw = w_parts[0]
                    wifi_raw = w_parts[1] if len(w_parts) > 1 else ""
                else:
                    # Single combined shell call instead of 5+ sequential round-trips
                    combo = pinned.shell(
                        "getprop ro.product.model; echo '__SEP__';"
                        "getprop ro.build.version.release; echo '__SEP__';"
                        "getprop ro.build.fingerprint; echo '__SEP__';"
                        "dumpsys battery; echo '__SEP__';"
                        "cmd wifi status 2>/dev/null || cat /proc/net/wireless 2>/dev/null; echo '__SEP__';"
                        "timeout 2 su -c id 2>/dev/null || echo ''",
                        check=False,
                        timeout=12,
                    )
                    parts = combo.split("__SEP__")
                    model = parts[0].strip() if len(parts) > 0 else ""
                    android_ver = parts[1].strip() if len(parts) > 1 else ""
                    fp = parts[2].strip() if len(parts) > 2 else ""
                    bat_raw = parts[3] if len(parts) > 3 else ""
                    wifi_raw = parts[4] if len(parts) > 4 else ""
                    root_raw = parts[5] if len(parts) > 5 else ""
                    is_rooted = "uid=0" in root_raw
                    if model or fp:
                        self._device_meta_cache[serial] = {
                            "model": model,
                            "android": android_ver,
                            "fingerprint": fp,
                            "rooted": is_rooted,
                        }
            except ADBError:
                # Temporary Wi-Fi sleep/lag — keep HUD graceful without spamming Console errors
                return _ok({"connected": False, "timeout": True})

            if fp and self._adb.serial == serial:
                self._current_fingerprint = fp

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

            # Parse live Wi-Fi signal strength (RSSI in dBm)
            wifi_rssi = None
            wifi_quality = ""
            m_rssi = re.search(r"RSSI:\s*(-\d+)", wifi_raw)
            if not m_rssi:
                m_rssi = re.search(r"wlan\d+:\s+\d+\s+\d+\.?\s+(-\d+)", wifi_raw)
            if m_rssi:
                val = int(m_rssi.group(1))
                if -120 <= val <= -10:
                    wifi_rssi = val
                    if val >= -65:
                        wifi_quality = "Strong"
                    elif val >= -75:
                        wifi_quality = "Fair"
                    else:
                        wifi_quality = "Weak"

            is_wifi = ":" in serial
            return _ok(
                {
                    "connected": bool(model or level is not None),
                    "serial": serial,
                    "model": model or serial,
                    "android": android_ver or "?",
                    "battery": level,
                    "charging": charging,
                    "temp": temp,
                    "connection": "Wi-Fi" if is_wifi else "USB",
                    "wifi_rssi": wifi_rssi,
                    "wifi_quality": wifi_quality,
                    "fingerprint": fp,
                    "rooted": is_rooted,
                }
            )
        except Exception:
            return _ok({"connected": False})

    def connect_wifi(self, target: str) -> dict:
        try:
            t = (target or "").strip()
            if not t:
                raise ADBError("IP:PORT লিখুন (যেমন: 192.168.0.105:5555)।")
            self._watchdog.clear_manual_disconnect(t)
            # Weak Wi-Fi resilient connect: clear zombie `offline` socket first + 2 adaptive retries (12s → 16s)
            self._watchdog._clear_offline_zombie(t)
            out = ""
            for attempt_idx, t_sec in enumerate((12, 16)):
                try:
                    out = ADB.run_global(["connect", t], check=False, timeout=t_sec)
                except ADBError as exc:
                    out = str(exc)
                low = (out or "").lower()
                if "connected to" in low or "already connected" in low:
                    # Confirm not stuck in offline zombie state
                    online = {d["serial"] for d in ADB.list_devices() if d.get("state") == "device"}
                    if t in online:
                        self._watchdog.remember_device(t)
                        return _ok(out, out)
                    self._watchdog._clear_offline_zombie(t)
                if attempt_idx == 0:
                    time.sleep(0.8)
            return _ok(out, out or f"Connect attempted: {t}")
        except Exception as e:
            return _err(e)

    def disconnect_device(self, target: str = "") -> dict:
        try:
            serial = (target or self._adb.serial or "").strip()
            if not serial:
                raise ADBError("ডিসকানেক্ট করার জন্য কোনো ডিভাইস সিলেক্ট করা নেই।")
            if ":" not in serial:
                raise ADBError(
                    f"'{serial}' একটি USB ডিভাইস — `adb disconnect` শুধু Wi-Fi (IP:PORT) ডিভাইসের জন্য কাজ করে। USB কেবল খুলে ডিসকানেক্ট করুন।"
                )
            self._watchdog.mark_manual_disconnect(serial)
            out = ADB.run_global(["disconnect", serial], check=False, timeout=8)
            if self._adb.serial == serial:
                self._adb.serial = None
                self._current_fingerprint = ""
            return _ok(out, out or f"Disconnected: {serial}")
        except Exception as e:
            return _err(e)

    def restart_adb(self) -> dict:
        try:
            ADB.run_global(["kill-server"], check=False, timeout=8)
            out = ADB.run_global(["start-server"], check=False, timeout=12)
            self._adb.serial = None
            self._current_fingerprint = ""
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

    def exec_shell(self, command: str, as_root: bool = False) -> dict:
        try:
            pinned = self._pinned_adb()
            cmd = (command or "").strip()
            if not cmd:
                raise ADBError("শেল কমান্ড খালি।")
            if as_root:
                out = pinned.root_shell(cmd, check=False, timeout=25)
                return _ok(out, f"# (root) {cmd}")
            out = pinned.shell(cmd, check=False, timeout=20)
            return _ok(out, f"$ {cmd}")
        except Exception as e:
            return _err(e)

    def check_root_status(self) -> dict:
        try:
            pinned = self._pinned_adb()
            st = pinned.root_status()
            if pinned.serial in self._device_meta_cache:
                self._device_meta_cache[pinned.serial]["rooted"] = bool(st.get("rooted"))
            lines = [
                f"Root Access (su): {'✅ GRANTED (uid=0)' if st['rooted'] else '❌ Not Rooted / Denied'}",
                f"su Binary: {st['su_path'] or 'Not found'} ({st['su_version'] or '-'})",
                f"Magisk: {st['magisk_version'] or '-'}",
                f"Bootloader: {'🔓 Unlocked' if st['bootloader_unlocked'] else '🔒 Locked / Unknown'}",
                f"SELinux: {st['selinux'] or '-'}",
            ]
            msg = "👑 Root সক্রিয় আছে!" if st["rooted"] else "ডিভাইসটি রুটেড নয় (বা Magisk Grant করা নেই)।"
            return _ok({"status": st, "summary": "\n".join(lines)}, msg)
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
    def list_launcher_apps(self, mode: str = "launcher") -> dict:
        try:
            pinned = self._pinned_adb()
            apps = pinned.list_apps_by_mode(mode or "launcher")
            lbl_map = {
                "launcher": "লঞ্চার অ্যাপ",
                "user": "ইউজার অ্যাপ",
                "system": "সিস্টেম/ব্লোটওয়্যার অ্যাপ",
                "disabled": "ফ্রিজ/ডিসেবল অ্যাপ",
            }
            lbl = lbl_map.get((mode or "launcher").lower(), "অ্যাপ")
            return _ok(apps, f"{len(apps)}টি {lbl} লোড হয়েছে।")
        except Exception as e:
            return _err(e)

    def freeze_app(self, pkg: str, freeze: bool = True) -> dict:
        try:
            pinned = self._pinned_adb()
            out = pinned.freeze_app(pkg, freeze=bool(freeze))
            action_txt = "❄️ Frozen (Disabled)" if freeze else "🔥 Unfrozen (Enabled)"
            return _ok(out, f"{action_txt}: {pkg}")
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
    def list_dir(self, remote_dir: str, use_root: bool = False) -> dict:
        try:
            pinned = self._pinned_adb()
            entries = pinned.list_directory_entries(remote_dir, use_root=bool(use_root))
            return _ok({"path": remote_dir.rstrip("/") or "/", "entries": entries, "root": bool(use_root)})
        except Exception as e:
            return _err(e)

    def mkdir(self, remote_path: str, use_root: bool = False) -> dict:
        try:
            pinned = self._pinned_adb()
            out = pinned.mkdir(remote_path, use_root=bool(use_root))
            return _ok(out, f"Folder created: {remote_path}")
        except Exception as e:
            return _err(e)

    def rename_path(self, old_path: str, new_path: str, use_root: bool = False) -> dict:
        try:
            pinned = self._pinned_adb()
            out = pinned.rename_path(old_path, new_path, use_root=bool(use_root))
            return _ok(out, f"Renamed → {os.path.basename(new_path)}")
        except Exception as e:
            return _err(e)

    def delete_path(self, remote_path: str, use_root: bool = False) -> dict:
        try:
            pinned = self._pinned_adb()
            out = pinned.rm(remote_path, use_root=bool(use_root))
            return _ok(out, f"Deleted: {remote_path}")
        except Exception as e:
            return _err(e)

    def delete_paths(self, paths: List[str], use_root: bool = False) -> dict:
        """Deletes multiple selected files/folders on the pinned device."""
        try:
            pinned = self._pinned_adb()
            clean = [str(p).strip() for p in (paths or []) if str(p).strip()]
            if not clean:
                raise ADBError("ডিলিট করার জন্য কোনো ফাইল/ফোল্ডার সিলেক্ট করা নেই।")
            for p in clean:
                pinned.rm(p, use_root=bool(use_root))
            return _ok({"count": len(clean)}, f"🗑️ {len(clean)}টি আইটেম ডিলিট সম্পন্ন!")
        except Exception as e:
            return _err(e)

    def download_items_dialog(self, items: List[dict], use_root: bool = False) -> dict:
        """Downloads multiple selected files/folders into one chosen PC folder with live progress."""
        try:
            pinned = self._pinned_adb()
            if not self._window:
                raise ADBError("Window ready নয়।")
            valid = [it for it in (items or []) if isinstance(it, dict) and (it.get("path") or "").strip()]
            if not valid:
                raise ADBError("ডাউনলোডের জন্য কোনো ফাইল/ফোল্ডার সিলেক্ট করা নেই।")
            import webview

            folders = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not folders:
                return _ok(None, "Cancelled")
            save_dir = folders[0]
            total_items = len(valid)
            root_flag = bool(use_root)

            def _job(cb, cancel_ev):
                done_count = 0
                for idx, it in enumerate(valid, 1):
                    if cancel_ev.is_set():
                        raise ADBError(f"ডাউনলোড বাতিল হয়েছে ({done_count}/{total_items} সম্পন্ন)।")
                    rpath = str(it.get("path")).strip()
                    is_dir = bool(it.get("is_dir") or it.get("type") == "dir")
                    name = os.path.basename(rpath.rstrip("/")) or f"item_{idx}"
                    dst = save_dir if is_dir else os.path.join(save_dir, name)
                    with self._transfer_lock:
                        self._transfer_state["label"] = f"Downloading ({idx}/{total_items}): {name}"

                    def _item_cb(done_b, total_b, pct, speed_bps, _i=idx):
                        overall = (((_i - 1) + (pct / 100.0)) / total_items) * 100.0
                        cb(done_b, total_b, overall, speed_bps)

                    if root_flag:
                        pinned.pull_root(rpath, dst, progress_callback=_item_cb, cancel_event=cancel_ev)
                    else:
                        pinned.pull(rpath, dst, progress_callback=_item_cb, cancel_event=cancel_ev)
                    done_count += 1
                return f"⬇️ {done_count}টি আইটেম ডাউনলোড সম্পন্ন → {save_dir}"

            return self._run_transfer_bg(f"Downloading {total_items} items", _job)
        except Exception as e:
            return _err(e)

    def _run_transfer_bg(self, label: str, worker_fn, refresh_dir: str = "") -> dict:
        """Runs a file push/pull/APK install in background with live progress polling & cancellation."""
        with self._transfer_lock:
            if self._transfer_state.get("active"):
                raise ADBError("ইতিমধ্যেই একটি ফাইল ট্রান্সফার চলছে। শেষ হলে বা Cancel করে চেষ্টা করুন।")
            cancel_ev = threading.Event()
            self._transfer_cancel = cancel_ev
            self._transfer_state = {
                "active": True,
                "label": label,
                "pct": 0.0,
                "done": "0 B",
                "total": "...",
                "speed": "0 B/s",
                "error": "",
                "finished_msg": "",
                "refresh_dir": refresh_dir,
            }

        def progress_cb(done_b: int, total_b: int, pct: float, speed_bps: float) -> None:
            with self._transfer_lock:
                self._transfer_state["pct"] = round(min(100.0, max(0.0, pct)), 1)
                self._transfer_state["done"] = ADB.format_bytes(done_b)
                self._transfer_state["total"] = ADB.format_bytes(total_b) if total_b > 0 else "?"
                self._transfer_state["speed"] = f"{ADB.format_bytes(speed_bps)}/s"

        def runner() -> None:
            try:
                msg = worker_fn(progress_cb, cancel_ev)
                with self._transfer_lock:
                    self._transfer_state["pct"] = 100.0
                    self._transfer_state["finished_msg"] = msg or f"✔ {label} সম্পন্ন!"
                    self._transfer_state["active"] = False
            except Exception as exc:
                with self._transfer_lock:
                    self._transfer_state["error"] = str(exc)
                    self._transfer_state["active"] = False
            finally:
                with self._transfer_lock:
                    self._transfer_cancel = None

        threading.Thread(target=runner, daemon=True).start()
        return _ok({"started": True, "label": label}, f"⏳ {label} শুরু হয়েছে...")

    def poll_transfer_progress(self) -> dict:
        with self._transfer_lock:
            snap = dict(self._transfer_state)
            if not snap["active"] and (snap["finished_msg"] or snap["error"]):
                self._transfer_state["finished_msg"] = ""
                self._transfer_state["error"] = ""
        return _ok(snap)

    def cancel_transfer(self) -> dict:
        with self._transfer_lock:
            ev = self._transfer_cancel
        if ev is not None:
            ev.set()
            return _ok(True, "⏹️ ট্রান্সফার বাতিল (Cancel) করা হচ্ছে...")
        return _ok(False, "কোনো সক্রিয় ট্রান্সফার চলছে না।")

    def download_item_dialog(self, remote_path: str, is_dir: bool, use_root: bool = False) -> dict:
        try:
            pinned = self._pinned_adb()
            if not self._window:
                raise ADBError("Window ready নয়।")
            import webview

            folders = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not folders:
                return _ok(None, "Cancelled")
            save_dir = folders[0]
            name = os.path.basename(remote_path.rstrip("/")) or "download"
            dst = save_dir if is_dir else os.path.join(save_dir, name)
            root_flag = bool(use_root)

            def _job(cb, cancel_ev):
                if root_flag:
                    pinned.pull_root(remote_path, dst, progress_callback=cb, cancel_event=cancel_ev)
                else:
                    pinned.pull(remote_path, dst, progress_callback=cb, cancel_event=cancel_ev)
                return f"⬇️ ডাউনলোড সম্পন্ন: {name} → {dst}"

            return self._run_transfer_bg(f"Downloading {name}", _job)
        except Exception as e:
            return _err(e)

    def upload_here_dialog(self, remote_dir: str, use_root: bool = False) -> dict:
        try:
            pinned = self._pinned_adb()
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
            root_flag = bool(use_root)

            def _job(cb, cancel_ev):
                if root_flag:
                    pinned.push_root(local_file, dst, progress_callback=cb, cancel_event=cancel_ev)
                else:
                    pinned.push(local_file, dst, progress_callback=cb, cancel_event=cancel_ev)
                return f"⬆️ আপলোড সম্পন্ন: {fname} → {dst}"

            return self._run_transfer_bg(f"Uploading {fname}", _job, refresh_dir=clean_dir)
        except Exception as e:
            return _err(e)

    def upload_dropped_file(self, remote_dir: str, filename: str, data_b64: str) -> dict:
        """Handles Drag & Drop files from Windows Explorer: auto-installs .apk, uploads all other files."""
        try:
            pinned = self._pinned_adb()
            safe_name = os.path.basename((filename or "file.bin").replace("\\", "/"))
            raw_b64 = (data_b64 or "").split(",", 1)[-1]
            raw_bytes = base64.b64decode(raw_b64)
            tmp_dir = tempfile.mkdtemp(prefix="adb_drop_")
            tmp_path = os.path.join(tmp_dir, safe_name)
            with open(tmp_path, "wb") as f:
                f.write(raw_bytes)

            clean_dir = (remote_dir or "/sdcard/Download").rstrip("/") or "/"
            is_apk = safe_name.lower().endswith(".apk")

            def _job(cb, cancel_ev):
                try:
                    if is_apk:
                        cb(len(raw_bytes) // 2, len(raw_bytes), 50.0, 0.0)
                        out = pinned.install_apk(tmp_path)
                        cb(len(raw_bytes), len(raw_bytes), 100.0, 0.0)
                        return f"📲 Drop APK ইনস্টল সম্পন্ন: {safe_name} ({out.strip()})"
                    dst = f"/{safe_name}" if clean_dir == "/" else f"{clean_dir}/{safe_name}"
                    pinned.push(tmp_path, dst, progress_callback=cb, cancel_event=cancel_ev)
                    return f"⬆️ Drop আপলোড সম্পন্ন: {safe_name} → {dst}"
                finally:
                    try:
                        os.remove(tmp_path)
                        os.rmdir(tmp_dir)
                    except OSError:
                        pass

            lbl = f"Installing {safe_name}" if is_apk else f"Uploading {safe_name}"
            return self._run_transfer_bg(lbl, _job, refresh_dir="" if is_apk else clean_dir)
        except Exception as e:
            return _err(e)

    def cancel_preview(self) -> dict:
        """Immediately terminates any in-flight hover image `adb pull`."""
        with self._preview_lock:
            ev = self._preview_cancel
        if ev is not None:
            ev.set()
            return _ok(True)
        return _ok(False)

    def preview_remote_image(self, remote_path: str, max_mb: float = 15.0) -> dict:
        """Pulls a phone image to a temp file and returns a Base64 data URL (cancels any previous preview pull)."""
        cancel_ev = threading.Event()
        with self._preview_lock:
            if self._preview_cancel is not None:
                self._preview_cancel.set()
            self._preview_cancel = cancel_ev

        try:
            pinned = self._pinned_adb()
            path_clean = (remote_path or "").strip()
            ext = os.path.splitext(path_clean)[1].lower()
            mime_map = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".bmp": "image/bmp",
            }
            mime = mime_map.get(ext)
            if not mime:
                raise ADBError("শুধুমাত্র ইমেজ ফাইল (JPG, PNG, WEBP, GIF, BMP) প্রিভিউ করা যাবে।")

            limit_bytes = int(max(1.0, float(max_mb or 15.0)) * 1024 * 1024)
            sz = pinned.get_remote_size(path_clean)
            if cancel_ev.is_set():
                return _ok({"cancelled": True})
            if sz > limit_bytes:
                raise ADBError(f"ইমেজটি বড় ({ADB.format_bytes(sz)}) — ফুল প্রিভিউ দেখতে ডাবল-ক্লিক বা Preview বাটন চাপুন।")

            fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="adb_prev_")
            os.close(fd)
            try:
                pinned.pull(path_clean, tmp_path, cancel_event=cancel_ev)
                if cancel_ev.is_set():
                    return _ok({"cancelled": True})
                with open(tmp_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                fname = os.path.basename(path_clean.rstrip("/"))
                return _ok(
                    {
                        "filename": fname,
                        "path": path_clean,
                        "size": ADB.format_bytes(sz) if sz else "",
                        "data_url": f"data:{mime};base64,{b64}",
                    },
                    f"🖼️ Preview: {fname}",
                )
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        except ADBError as e:
            if cancel_ev.is_set():
                return _ok({"cancelled": True})
            return _err(e)
        except Exception as e:
            if cancel_ev.is_set():
                return _ok({"cancelled": True})
            return _err(e)
        finally:
            with self._preview_lock:
                if self._preview_cancel is cancel_ev:
                    self._preview_cancel = None

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
            if mode in ("cam_back", "cam_front"):
                pinned = self._pinned_adb()
                sdk_s = pinned.shell("getprop ro.build.version.sdk", check=False, timeout=5).strip()
                if sdk_s.isdigit() and int(sdk_s) < 31:
                    ver = pinned.shell("getprop ro.build.version.release", check=False, timeout=5).strip() or "?"
                    raise ADBError(
                        f"এই ফোনটি Android {ver} (API {sdk_s}) — scrcpy-এর ব্যাকগ্রাউন্ড ক্যামেরা স্ট্রিমিং (BG) শুধুমাত্র Android 12 (API 31) বা তার উপরের ভার্সনে সাপোর্ট করে।"
                    )
            if mode == "mic_listen":
                pinned = self._pinned_adb()
                sdk_s = pinned.shell("getprop ro.build.version.sdk", check=False, timeout=5).strip()
                if sdk_s.isdigit() and int(sdk_s) < 30:
                    ver = pinned.shell("getprop ro.build.version.release", check=False, timeout=5).strip() or "?"
                    raise ADBError(
                        f"এই ফোনটি Android {ver} (API {sdk_s}) — scrcpy মাইক্রোফোন অডিও স্ট্রিমিং Android 11 (API 30)+ এ সাপোর্ট করে।"
                    )
            presets = {
                "standard": [],
                "smooth": ["-m", "1024", "-b", "4M", "--max-fps", "60"],
                "weak_wifi": ["-m", "720", "-b", "1M", "--max-fps", "25", "--video-buffer=100"],
                "screen_off": ["-S", "-w"],
                "always_top": ["-w", "--always-on-top"],
                "read_only": ["-n"],
                "hid": ["--no-video", "--no-audio", "--keyboard=uhid", "--mouse=uhid"],
                "cam_back": ["--video-source=camera", "--camera-facing=back", "--camera-size=1280x720", "--orientation=90", "--no-audio", "--window-title=Phone Back Camera"],
                "cam_front": ["--video-source=camera", "--camera-facing=front", "--camera-size=1280x720", "--orientation=270", "--no-audio", "--window-title=Phone Front Camera"],
                "mic_listen": ["--audio-source=mic", "--no-video", "--audio-bit-rate=128K"],
            }
            if mode == "custom":
                extra = shlex.split(custom_args or "")
            else:
                extra = presets.get(mode, [])
            return self._spawn_scrcpy(extra, f"scrcpy ({mode})")
        except Exception as e:
            return _err(e)

    def start_screen_off_scrcpy(self, opts: Optional[dict] = None) -> dict:
        """Launches scrcpy in Screen-Off (-S) mode with configurable Resolution, FPS, Bitrate, Audio & Codec."""
        try:
            cfg = opts if isinstance(opts, dict) else {}
            args: List[str] = ["-S"]

            res = str(cfg.get("res") or "720").strip()
            if res and res.lower() != "orig" and res.isdigit():
                args += ["-m", res]

            fps = str(cfg.get("fps") or "30").strip()
            if fps and fps.isdigit():
                args += ["--max-fps", fps]

            bitrate = str(cfg.get("bitrate") or "2M").strip()
            if bitrate:
                args += ["-b", bitrate]

            audio = str(cfg.get("audio") or "off").strip()
            if audio == "off":
                args.append("--no-audio")
            elif audio in ("64K", "128K", "192K"):
                args.append(f"--audio-bit-rate={audio}")

            codec = str(cfg.get("codec") or "h264").strip().lower()
            if codec == "h265":
                args.append("--video-codec=h265")

            if cfg.get("stay_awake", True):
                args.append("-w")
            if cfg.get("power_off_close"):
                args.append("--power-off-on-close")
            if cfg.get("always_top"):
                args.append("--always-on-top")
            if cfg.get("read_only"):
                args.append("-n")

            res_lbl = f"{res}p" if res.isdigit() else "Original"
            return self._spawn_scrcpy(args, f"Screen-Off Mirror ({res_lbl} • {fps}fps • {bitrate})")
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
                subprocess.run(
                    ["taskkill", "/IM", "scrcpy.exe", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                subprocess.run(["pkill", "-f", "scrcpy"], capture_output=True)
            return _ok("OK", "সব scrcpy সেশন বন্ধ করা হয়েছে।")
        except Exception as e:
            return _err(e)

    # ─────────────────────────── Quick Controls ───────────────────────────
    def system_toggle(self, kind: str, value: Any) -> dict:
        try:
            pinned = self._pinned_adb()
            if kind == "data":
                out = pinned.set_mobile_data(bool(value))
            elif kind == "bluetooth":
                out = pinned.set_bluetooth(bool(value))
            elif kind == "location":
                out = pinned.set_location(bool(value))
            elif kind == "flashlight":
                out = pinned.set_flashlight(bool(value))
            elif kind == "airplane":
                out = pinned.set_airplane_mode(bool(value))
            elif kind == "hotspot":
                out = pinned.set_hotspot(bool(value))
            elif kind == "brightness":
                out = pinned.set_brightness(int(value))
            elif kind == "timeout":
                out = pinned.set_screen_timeout(int(value))
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
            pinned = self._pinned_adb()
            fp = pinned.shell("getprop ro.build.fingerprint", check=False, timeout=6).strip()
            model = pinned.shell("getprop ro.product.model", check=False, timeout=5).strip()
            if fp and self._adb.serial == pinned.serial:
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
            pinned = self._pinned_adb()
            # Always query LIVE fingerprint from the pinned device (never risk unlocking Phone B with Phone A's cached PIN!)
            fp = pinned.shell("getprop ro.build.fingerprint", check=False, timeout=6).strip()
            if not fp:
                raise ADBError(f"ডিভাইস ({pinned.serial}) থেকে Fingerprint পড়া যায়নি। কানেকশন চেক করুন।")
            if self._adb.serial == pinned.serial:
                self._current_fingerprint = fp
            prof = self._profile_store.get(fp)
            if not prof:
                raise ADBError(f"এই ডিভাইসের ({pinned.serial}) কোনো Saved Profile নেই। Profiles ট্যাবে গিয়ে আগে সেভ করুন।")
            out = smart_unlock_device(pinned, prof)
            return _ok(out, f"🔓 {prof['name']} ({pinned.serial}) আনলক সম্পন্ন!")
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

    def get_phone_numbers(self) -> dict:
        """Reads active SIM slots & phone numbers via ADB and merges per-device saved fallback numbers."""
        try:
            pinned = self._pinned_adb()
            meta = pinned.shell(
                "getprop ro.build.fingerprint; echo '__M__'; getprop ro.product.model",
                check=False,
                timeout=6,
            )
            parts = (meta or "").split("__M__")
            fp = (parts[0] if len(parts) > 0 else "").strip()
            model = (parts[1] if len(parts) > 1 else "").strip() or pinned.serial
            saved_map = self._sim_store.get_for_device(fp)
            sims = pinned.get_sim_info()

            seen_slots = set()
            for s in sims:
                slot_key = str(s["slot"])
                seen_slots.add(slot_key)
                saved_num = (saved_map.get(slot_key) or "").strip()
                if saved_num:
                    # User-saved number fills blank/redacted SIMs (or overrides if edited)
                    s["number"] = saved_num
                    s["source"] = "Saved"

            # Also include any saved slot numbers if phone is temporarily in Airplane mode / no active SIM
            for slot_key, saved_num in sorted(saved_map.items()):
                if slot_key not in seen_slots and saved_num.strip():
                    sims.append(
                        {
                            "slot": int(slot_key) if slot_key.isdigit() else 1,
                            "sub_id": "",
                            "carrier": f"SIM {slot_key}",
                            "country_iso": "",
                            "mcc_mnc": "",
                            "number": saved_num.strip(),
                            "source": "Saved",
                            "redacted": False,
                            "ussd": "",
                        }
                    )

            sims.sort(key=lambda x: int(x.get("slot", 1)))
            primary = next((s["number"] for s in sims if s.get("number")), "")
            return _ok(
                {
                    "serial": pinned.serial,
                    "fingerprint": fp,
                    "model": model,
                    "sims": sims,
                    "primary_number": primary,
                },
                f"📱 {len(sims)}টি SIM স্লট পাওয়া গেছে।" if sims else "কোনো Active SIM পাওয়া যায়নি।",
            )
        except Exception as e:
            return _err(e)

    def save_phone_number(self, slot: int, number: str) -> dict:
        """Saves or clears a phone number for the connected device's SIM slot (keyed by fingerprint)."""
        try:
            pinned = self._pinned_adb()
            fp = pinned.shell("getprop ro.build.fingerprint", check=False, timeout=6).strip()
            if not fp:
                raise ADBError("ডিভাইসের Fingerprint পড়া যায়নি। কানেকশন চেক করুন।")
            num_clean = (number or "").strip()
            self._sim_store.set_slot_number(fp, int(slot), num_clean)
            msg = (
                f"💾 SIM {slot} নম্বর সেভ হয়েছে: {num_clean}"
                if num_clean
                else f"🗑️ SIM {slot}-এর সেভ করা নম্বর মুছে ফেলা হয়েছে।"
            )
            res = self.get_phone_numbers()
            if res.get("ok"):
                res["message"] = msg
            return res
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
            pinned = self._pinned_adb()
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
                pinned,
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
                    "serial": pinned.serial,
                    "screen": f"{cal.screen_w}x{cal.screen_h}",
                    "max": f"{cal.max_x}x{cal.max_y}",
                    "nav_mode": cal.nav_mode,
                },
                f"🔴 Recording শুরু হয়েছে ({mode} @ {pinned.serial})",
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
            pinned = self._pinned_adb()
            if self._action_recorder is not None:
                raise ADBError("ম্যাক্রো চালানোর আগে Recording বন্ধ করুন।")
            fp = pinned.shell("getprop ro.build.fingerprint", check=False, timeout=5).strip()
            prof = self._profile_store.get(fp) if fp else None
            stop_ev = threading.Event()
            self._macro_stop_event = stop_ev
            out = execute_macro(pinned, steps, profile=prof, repeat=int(repeat or 1), stop_event=stop_ev)
            return _ok(out, f"Macro সম্পন্ন ({pinned.serial})")
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

    def capture_ui_inspector(self) -> dict:
        """Captures live phone screenshot + parsed uiautomator XML node tree for Visual UI Inspector."""
        import struct
        import xml.etree.ElementTree as ET

        try:
            pinned = self._pinned_adb(timeout=15)
            fd, tmp_png = tempfile.mkstemp(suffix=".png", prefix="adb_insp_")
            os.close(fd)
            remote_xml = f"/data/local/tmp/__adb_insp_{int(time.time())}.xml"
            try:
                pinned.screenshot(tmp_png)
                with open(tmp_png, "rb") as f:
                    png_bytes = f.read()
                b64 = base64.b64encode(png_bytes).decode("ascii")
                # Read exact PNG dimensions from IHDR header (bytes 16..24)
                img_w, img_h = 1080, 2400
                if len(png_bytes) >= 24 and png_bytes[:8] == b"\x89PNG\r\n\x1a\n":
                    img_w, img_h = struct.unpack(">II", png_bytes[16:24])

                pinned.shell(f"uiautomator dump {remote_xml}", check=False, timeout=10)
                raw_xml = pinned.shell(f"cat {remote_xml}", check=False, timeout=8)
                nodes: List[dict] = []
                if raw_xml and raw_xml.strip().startswith("<"):
                    try:
                        root = ET.fromstring(raw_xml)
                        for node in root.iter("node"):
                            nums = list(map(int, re.findall(r"\d+", node.attrib.get("bounds", ""))))
                            if len(nums) != 4:
                                continue
                            x1, y1, x2, y2 = nums
                            if x2 <= x1 or y2 <= y1:
                                continue
                            area = (x2 - x1) * (y2 - y1)
                            text = (node.attrib.get("text") or "").strip()
                            desc = (node.attrib.get("content-desc") or "").strip()
                            rid = (node.attrib.get("resource-id") or "").strip()
                            cls = (node.attrib.get("class") or "").split(".")[-1]
                            clickable = node.attrib.get("clickable") == "true"
                            nodes.append(
                                {
                                    "x1": x1,
                                    "y1": y1,
                                    "x2": x2,
                                    "y2": y2,
                                    "cx": (x1 + x2) // 2,
                                    "cy": (y1 + y2) // 2,
                                    "text": text,
                                    "desc": desc,
                                    "resource_id": rid,
                                    "class": cls,
                                    "clickable": clickable,
                                    "area": area,
                                }
                            )
                        # Sort smallest-area first so hover hits the tightest UI button
                        nodes.sort(key=lambda n: n["area"])
                    except ET.ParseError:
                        pass

                return _ok(
                    {
                        "serial": pinned.serial,
                        "width": img_w,
                        "height": img_h,
                        "data_url": f"data:image/png;base64,{b64}",
                        "nodes": nodes[:350],
                    },
                    f"🎯 UI Inspector লোড হয়েছে ({len(nodes)} elements)",
                )
            finally:
                pinned.shell(f"rm -f {remote_xml}", check=False, timeout=4)
                try:
                    os.remove(tmp_png)
                except OSError:
                    pass
        except Exception as e:
            return _err(e)

    def battery_telemetry_detail(self) -> dict:
        """Reads detailed live battery health, voltage (V), current (mA), wattage (W), temp (°C) & capacity."""
        try:
            pinned = self._pinned_adb(timeout=10)
            raw = pinned.shell(
                "dumpsys battery; echo '__SYSFS__';"
                "for f in current_now voltage_now charge_counter charge_full charge_full_design cycle_count; do "
                "  v=$(cat /sys/class/power_supply/battery/$f 2>/dev/null); echo \"$f=$v\"; "
                "done",
                check=False,
                timeout=10,
            )
            parts = raw.split("__SYSFS__")
            dump_txt = parts[0] if parts else ""
            sysfs_txt = parts[1] if len(parts) > 1 else ""

            def _m_int(pattern: str, text: str) -> Optional[int]:
                m = re.search(pattern, text)
                return int(m.group(1)) if m else None

            level = _m_int(r"level:\s*(\d+)", dump_txt)
            temp_raw = _m_int(r"temperature:\s*(-?\d+)", dump_txt)
            temp_c = round(temp_raw / 10.0, 1) if temp_raw is not None else None
            volt_mv = _m_int(r"voltage:\s*(\d+)", dump_txt)
            status_code = _m_int(r"status:\s*(\d+)", dump_txt)
            health_code = _m_int(r"health:\s*(\d+)", dump_txt)
            tech_m = re.search(r"technology:\s*([^\r\n]+)", dump_txt)
            technology = tech_m.group(1).strip() if tech_m else ""
            charge_counter_dump = _m_int(r"Charge counter:\s*(\d+)", dump_txt)

            sysfs: Dict[str, Optional[int]] = {}
            for ln in sysfs_txt.splitlines():
                if "=" in ln:
                    k, v = ln.split("=", 1)
                    v = v.strip()
                    try:
                        sysfs[k.strip()] = int(v)
                    except ValueError:
                        sysfs[k.strip()] = None

            # Normalize voltage to Volts
            voltage_v: Optional[float] = None
            v_sys = sysfs.get("voltage_now")
            if v_sys and v_sys > 0:
                voltage_v = round(v_sys / 1_000_000.0 if v_sys > 100_000 else v_sys / 1000.0, 3)
            elif volt_mv and volt_mv > 0:
                voltage_v = round(volt_mv / 1000.0 if volt_mv > 100 else float(volt_mv), 3)

            # Normalize current to mA
            current_ma: Optional[float] = None
            c_sys = sysfs.get("current_now")
            if c_sys is not None:
                current_ma = round(c_sys / 1000.0 if abs(c_sys) > 20_000 else float(c_sys), 1)

            wattage_w: Optional[float] = None
            if voltage_v is not None and current_ma is not None:
                wattage_w = round(voltage_v * abs(current_ma) / 1000.0, 2)

            charge_raw = sysfs.get("charge_counter") or charge_counter_dump
            charge_mah = round(charge_raw / 1000.0) if charge_raw and charge_raw > 10_000 else charge_raw
            full_raw = sysfs.get("charge_full") or sysfs.get("charge_full_design")
            full_mah = round(full_raw / 1000.0) if full_raw and full_raw > 10_000 else full_raw

            status_map = {1: "Unknown", 2: "Charging", 3: "Discharging", 4: "Not Charging", 5: "Full"}
            health_map = {1: "Unknown", 2: "Good", 3: "Overheat", 4: "Dead", 5: "Over Voltage", 6: "Failed", 7: "Cold"}

            return _ok(
                {
                    "serial": pinned.serial,
                    "level": level,
                    "temp_c": temp_c,
                    "voltage_v": voltage_v,
                    "current_ma": current_ma,
                    "wattage_w": wattage_w,
                    "charging": status_code in (2, 5),
                    "status_text": status_map.get(status_code or 1, "Unknown"),
                    "health": health_map.get(health_code or 1, "Good"),
                    "technology": technology,
                    "charge_mah": charge_mah,
                    "full_mah": full_mah,
                    "cycle_count": sysfs.get("cycle_count"),
                    "ts": time.strftime("%H:%M:%S"),
                }
            )
        except Exception as e:
            return _err(e)

    def sync_folder_dialog(self, remote_dir: str, direction: str = "pull") -> dict:
        try:
            pinned = self._pinned_adb()
            if not self._window:
                raise ADBError("Window ready নয়।")
            import webview

            folders = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not folders:
                return _ok(None, "Cancelled")
            local_dir = folders[0]
            rem = (remote_dir or "/sdcard/Download").strip()
            if direction == "push":
                def _push_job(cb, cancel_ev):
                    pinned.push(local_dir, rem, progress_callback=cb, cancel_event=cancel_ev)
                    return f"⬆️ PC → Phone Sync সম্পন্ন ({rem})"
                return self._run_transfer_bg(f"Syncing → {rem}", _push_job, refresh_dir=rem)

            def _pull_job(cb, cancel_ev):
                pinned.pull(rem, local_dir, progress_callback=cb, cancel_event=cancel_ev)
                return f"⬇️ Phone → PC Backup সম্পন্ন ({local_dir})"
            return self._run_transfer_bg(f"Backing up {rem}", _pull_job)
        except Exception as e:
            return _err(e)

    def toggle_auto_sync(self, remote_dir: str) -> dict:
        try:
            if self._auto_sync_stop is not None:
                self._auto_sync_stop.set()
                self._auto_sync_stop = None
                return _ok({"active": False}, "⏹️ Auto-Sync Watch বন্ধ করা হয়েছে।")

            pinned = self._pinned_adb()
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
                                pinned.push(fp, f"{rem}/{f}")
                                self._put_bounded(self._auto_sync_queue, f"👁️ [Auto-Sync @ {pinned.serial}] Pushed {f} → {rem}/{f}")
                    except Exception:
                        pass
                    stop_ev.wait(2.5)

            threading.Thread(target=watcher, daemon=True).start()
            return _ok({"active": True, "local": local_dir, "remote": rem, "serial": pinned.serial}, f"👁️ Auto-Sync চালু ({pinned.serial}): {os.path.basename(local_dir)} → {rem}")
        except Exception as e:
            return _err(e)

    def poll_auto_sync(self) -> dict:
        events: List[str] = []
        while len(events) < 40:
            try:
                events.append(self._auto_sync_queue.get_nowait())
            except queue.Empty:
                break
        return _ok({"active": self._auto_sync_stop is not None, "events": events})

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

    def scrcpy_record_mic(self) -> dict:
        """Streams & records phone microphone audio to recordings/mic_*.opus (Android 11+)."""
        try:
            pinned = self._pinned_adb()
            sdk_s = pinned.shell("getprop ro.build.version.sdk", check=False, timeout=5).strip()
            if sdk_s.isdigit() and int(sdk_s) < 30:
                raise ADBError("মাইক্রোফোন অডিও রেকর্ডিংয়ের জন্য Android 11 (API 30)+ প্রয়োজন।")
            rec_dir = os.path.join(BASE_DIR, "recordings")
            os.makedirs(rec_dir, exist_ok=True)
            fname = f"mic_{time.strftime('%Y%m%d_%H%M%S')}.opus"
            lp = os.path.join(rec_dir, fname)
            return self._spawn_scrcpy(["--audio-source=mic", "--no-video", "--record", lp], f"Mic Recording ({fname})")
        except Exception as e:
            return _err(e)

    def scrcpy_sessions(self) -> dict:
        try:
            if os.name == "nt":
                out = subprocess.check_output(
                    ["tasklist", "/FI", "IMAGENAME eq scrcpy.exe"],
                    text=True,
                    errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                out = subprocess.check_output(["pgrep", "-af", "scrcpy"], text=True, errors="replace")
            return _ok(out.strip(), "Active scrcpy Sessions")
        except Exception as e:
            return _ok("কোনো scrcpy সেশন চলছে না।", "No active scrcpy")

    def check_scrcpy(self) -> dict:
        try:
            out = subprocess.check_output(
                ["scrcpy", "--version"],
                text=True,
                errors="replace",
                timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return _ok(out.strip(), "scrcpy OK")
        except Exception as e:
            return _err(f"scrcpy পাওয়া যায়নি: {e}")

    # ─── WiFi & Live Tools ───
    def enable_tcpip(self, port: int = 5555) -> dict:
        try:
            pinned = self._pinned_adb()
            p = int(port or 5555)
            # Prevent OEM/MagicOS Wi-Fi sleep drops before switching to fixed TCP/IP port
            pinned.shell("settings put global wifi_power_save 0; settings put global wifi_sleep_policy 2", check=False, timeout=6)
            out = pinned.tcpip(p)
            ip_raw = pinned.shell("ip route | grep wlan0", check=False)
            m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", ip_raw)
            ip_hint = f"\nPhone Wi-Fi IP: {m.group(1)}:{p}" if m else ""
            if m:
                self._watchdog.remember_device(f"{m.group(1)}:{p}", wifi_ip=m.group(1))
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
                a.shell("settings put global wifi_power_save 0; settings put global wifi_sleep_policy 2", check=False, timeout=6)
                a.tcpip(p)
                ip_raw = a.shell("ip route | grep wlan0", check=False)
                m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", ip_raw)
                if m:
                    target = f"{m.group(1)}:{p}"
                    time.sleep(0.8)
                    self._watchdog.clear_manual_disconnect(target)
                    c_out = ADB.run_global(["connect", target], check=False, timeout=12)
                    if "connected" in (c_out or "").lower():
                        self._watchdog.remember_device(target)
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
            out = ADB.run_global(["pair", t, c], check=False, timeout=15)
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
                already_online = {d["serial"] for d in ADB.list_devices() if d.get("state") == "device"}
                paired_ips = set()
                seen_connect_by_ip: Dict[str, str] = {}
                qr_lock = threading.Lock()

                def _connect_paired_target(target: str) -> None:
                    self._watchdog.clear_manual_disconnect(target)
                    c_out = ADB.run_global(["connect", target], check=False, timeout=12)
                    if "connected" in (c_out or "").lower():
                        self._qr_connected = True
                        self._watchdog.remember_device(target)
                        if not self._adb.serial or self._adb.serial not in already_online:
                            self._adb.serial = target
                            self._current_fingerprint = ""
                        self._qr_status = f"✔ সফলভাবে Wi-Fi ADB কানেক্ট হয়েছে: {target}"

                def on_state(zeroconf, service_type, name, state_change):
                    if state_change not in (ServiceStateChange.Added, ServiceStateChange.Updated):
                        return
                    if self._qr_connected:
                        return
                    info = zeroconf.get_service_info(service_type, name, timeout=3000)
                    if not info or not info.parsed_addresses():
                        return
                    ip = info.parsed_addresses()[0]
                    port = info.port
                    target = f"{ip}:{port}"
                    if "_adb-tls-pairing." in service_type:
                        self._qr_status = f"🔗 Pairing {target}..."
                        p_out = ADB.run_global(["pair", target, password], check=False, timeout=15)
                        if "successfully" in p_out.lower():
                            with qr_lock:
                                paired_ips.add(ip)
                                cached_conn = seen_connect_by_ip.get(ip)
                            self._qr_status = f"✔ Paired ({target})! Connection পোর্ট খোঁজা হচ্ছে..."
                            if cached_conn and cached_conn not in already_online and not self._qr_connected:
                                _connect_paired_target(cached_conn)
                        else:
                            self._qr_status = f"⚠️ Pair output: {p_out}"
                    elif "_adb-tls-connect." in service_type and not self._qr_connected:
                        # Ignore phones that were already connected before QR pairing started
                        if target in already_online:
                            return
                        with qr_lock:
                            seen_connect_by_ip[ip] = target
                            is_paired = ip in paired_ips
                        if is_paired:
                            _connect_paired_target(target)

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
            pinned = self._pinned_adb()
            self.stop_live_logcat()
            while not self._logcat_queue.empty():
                try:
                    self._logcat_queue.get_nowait()
                except queue.Empty:
                    break
            kw = (keyword or "").strip().lower()
            cmd = pinned._cmd() + ["logcat", "-v", "time"]
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
                    self._put_bounded(self._logcat_queue, s)

            threading.Thread(target=reader, daemon=True).start()
            return _ok(True, f"▶️ Live Logcat ({pinned.serial}) স্ট্রিম শুরু হয়েছে!")
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
            found = scan_subnet_port(port=p, timeout=0.9)
            if not found:
                raise ADBError(f"লোকাল নেটওয়ার্কে পোর্ট {p} খোলা আছে এমন কোনো ফোন পাওয়া যায়নি।")
            online_before = {d["serial"] for d in ADB.list_devices() if d.get("state") == "device"}
            connected = []
            for target in found:
                self._watchdog.clear_manual_disconnect(target)
                self._watchdog._clear_offline_zombie(target)
                out = ADB.run_global(["connect", target], check=False, timeout=12)
                low = (out or "").lower()
                if "connected to" in low or "already connected" in low:
                    self._watchdog.remember_device(target)
                    connected.append(target)
            if not connected:
                raise ADBError(f"পাওয়া গেছে ({', '.join(found)}), কিন্তু adb connect সফল হয়নি।")
            # Keep user's active device if still online; otherwise select the first newly connected phone
            if not self._adb.serial or self._adb.serial not in online_before:
                self._adb.serial = connected[0]
                self._current_fingerprint = ""
            return _ok(connected, f"🟢 স্ক্যান করে কানেক্ট হয়েছে: {', '.join(connected)}")
        except Exception as e:
            return _err(e)

    def _setup_companion_for(self, pinned: ADB) -> str:
        pkg = "com.adbcontrol.companion"
        cmds = [
            f"pm grant {pkg} android.permission.WRITE_SECURE_SETTINGS",
            f"dumpsys deviceidle whitelist +{pkg}",
            f"cmd appops set {pkg} RUN_IN_BACKGROUND allow",
            f"cmd appops set {pkg} RUN_ANY_IN_BACKGROUND allow",
            f"cmd appops set {pkg} WAKE_LOCK allow",
            "settings put global wifi_power_save 0",
            "settings put global wifi_sleep_policy 2",
        ]
        logs = []
        for c in cmds:
            out = pinned.shell(c, check=False, timeout=10).strip()
            logs.append(f"$ {c}" + (f"\n  {out}" if out else " -> OK"))
        return "\n".join(logs)

    def setup_companion_permissions(self) -> dict:
        """1-Click grants WRITE_SECURE_SETTINGS and whitelists ADB Companion from Android Doze/Freezer."""
        try:
            pinned = self._pinned_adb()
            logs = self._setup_companion_for(pinned)
            return _ok(logs, f"🛠️ Companion পারমিশন ও Whitelist সম্পন্ন ({pinned.serial})!")
        except Exception as e:
            return _err(e)

    def install_companion_apk(self) -> dict:
        """1-Click installs `app-debug.apk` from project root (or picker), grants permissions, and launches it."""
        try:
            pinned = self._pinned_adb()
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

            inst_out = pinned.install_apk(apk_path)
            setup_logs = self._setup_companion_for(pinned)
            pinned.launch_app("com.adbcontrol.companion")
            summary = f"Device: {pinned.serial}\nAPK: {os.path.basename(apk_path)}\n{inst_out}\n\n{setup_logs}"
            return _ok(summary, f"📲 Companion APK ইনস্টল, Whitelist এবং লঞ্চ সম্পন্ন ({pinned.serial})!")
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
        title=f"⚡ ADB Control Pro v{APP_VERSION} — by {APP_DEVELOPER}",
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
