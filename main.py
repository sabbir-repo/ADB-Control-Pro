#!/usr/bin/env python3
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import secrets
import threading

try:
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, IntPrompt, Confirm
    from rich.columns import Columns
    from rich.text import Text
    console = Console()
except ImportError:
    Console = None
    console = None
    def escape(text: str) -> str:
        return text

try:
    import qrcode
except ImportError:
    qrcode = None

try:
    from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
except ImportError:
    ServiceBrowser = ServiceStateChange = Zeroconf = None


class ADBError(Exception):
    pass


@dataclass
class MenuItem:
    number: int
    title: str
    action: Callable[[], None]


class ADB:
    def __init__(self, serial: Optional[str] = None, timeout: int = 30):
        self.serial = serial
        self.timeout = timeout

    def _cmd(self) -> List[str]:
        base = ["adb"]
        if self.serial:
            base += ["-s", self.serial]
        return base

    def run(self, args: List[str], check: bool = True, timeout: Optional[int] = None) -> str:
        cmd = self._cmd() + args
        if timeout == 0:
            effective_timeout = None
        else:
            effective_timeout = timeout if timeout is not None else self.timeout
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",      # <-- নতুন যোগ করা হয়েছে
                errors="replace",      # <-- আননোন ক্যারেক্টার থাকলে ক্র্যাশ করবে না
                timeout=effective_timeout,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except FileNotFoundError as e:
            raise ADBError("adb পাওয়া যায়নি। Android Platform Tools install করে PATH-এ যোগ করুন।") from e
        except subprocess.TimeoutExpired as e:
            raise ADBError(f"Command timeout: {' '.join(cmd)}") from e

        out = (proc.stdout or proc.stderr or "").strip()
        if check and proc.returncode != 0:
            raise ADBError(out or f"ADB command failed: {' '.join(cmd)}")
        return out

    def shell(self, cmd: str, check: bool = True, timeout: Optional[int] = None) -> str:
        return self.run(["shell", cmd], check=check, timeout=timeout)

    @staticmethod
    def run_global(args: List[str], check: bool = True, timeout: Optional[int] = 30) -> str:
        """Runs a server-level ADB command WITHOUT `-s <serial>` (safe for multi-device)."""
        cmd = ["adb"] + args
        effective_timeout = None if timeout == 0 else timeout
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except FileNotFoundError as e:
            raise ADBError("adb পাওয়া যায়নি। Android Platform Tools install করে PATH-এ যোগ করুন।") from e
        except subprocess.TimeoutExpired as e:
            raise ADBError(f"Command timeout: {' '.join(cmd)}") from e

        out = (proc.stdout or proc.stderr or "").strip()
        if check and proc.returncode != 0:
            raise ADBError(out or f"ADB command failed: {' '.join(cmd)}")
        return out

    @staticmethod
    def list_devices() -> List[dict]:
        try:
            proc = subprocess.run(
                ["adb", "devices", "-l"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except FileNotFoundError as e:
            raise ADBError("adb পাওয়া যায়নি।") from e
        except subprocess.TimeoutExpired as e:
            raise ADBError("adb devices কমান্ড টাইমআউট হয়েছে।") from e

        devices = []
        in_device_section = False
        for raw_line in (proc.stdout or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if not in_device_section:
                if line.lower().startswith("list of devices attached"):
                    in_device_section = True
                continue
            # Ignore any trailing daemon/status messages starting with '*'
            if line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            state = parts[1]
            details = " ".join(parts[2:]) if len(parts) > 2 else ""
            model = ""
            for tok in parts[2:]:
                if tok.startswith("model:"):
                    model = tok.split(":", 1)[1].replace("_", " ").strip()
                    break
            devices.append({
                "serial": serial,
                "state": state,
                "details": details,
                "model": model,
                "connection": "Wi-Fi" if ":" in serial else "USB",
            })
        return devices

    def reconnect(self) -> str:
        return self.run(["reconnect"], check=False)

    def disconnect(self, target: Optional[str] = None) -> str:
        return ADB.run_global(["disconnect"] + ([target] if target else []), check=False)

    def connect(self, target: str) -> str:
        return ADB.run_global(["connect", target], check=False)

    def wait(self) -> str:
        return self.run(["wait-for-device"], timeout=max(self.timeout, 120))

    def root(self) -> str:
        return self.run(["root"], check=False)

    def unroot(self) -> str:
        return self.run(["unroot"], check=False)

    def getprop(self, key: str) -> str:
        return self.shell(f"getprop {shlex.quote(key)}", check=False)

    def info(self) -> dict:
        keys = {
            "Manufacturer": "ro.product.manufacturer",
            "Model": "ro.product.model",
            "Device": "ro.product.device",
            "Android": "ro.build.version.release",
            "SDK": "ro.build.version.sdk",
            "Fingerprint": "ro.build.fingerprint",
        }
        return {k: self.getprop(v) for k, v in keys.items()}

    def battery(self) -> str:
        return self.shell("dumpsys battery", check=False)

    def memory(self) -> str:
        return self.shell("dumpsys meminfo", check=False)

    def cpu(self) -> str:
        return self.shell("top -n 1 | head -40", check=False)

    def storage(self) -> str:
        return self.shell("df -h", check=False)

    def processes(self) -> str:
        return self.shell("ps -A", check=False)

    def netstat(self) -> str:
        return self.shell("ip addr && echo '\n---\n' && ip route", check=False)

    def wifi_status(self) -> str:
        return self.shell("dumpsys wifi | head -120", check=False)

    def service_status(self) -> str:
        return self.shell("service list", check=False)

    def logcat(self, lines: int = 200) -> str:
        return self.run(["logcat", "-d", "-t", str(lines)], check=False)

    def screenshot(self, local_path: str) -> str:
        # ১. সরাসরি exec-out দিয়ে PNG স্ট্রিম (দ্রুততর এবং /sdcard পারমিশন ছাড়াই কাজ করে)
        cmd = self._cmd() + ["exec-out", "screencap", "-p"]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                timeout=max(15, self.timeout),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if res.returncode == 0 and res.stdout and res.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                with open(local_path, "wb") as f:
                    f.write(res.stdout)
                return f"Saved: {local_path}"
        except subprocess.TimeoutExpired:
            pass

        # ২. ফলব্যাক: /data/local/tmp (যদি exec-out কোনো ডিভাইসে সাপোর্ট না করে)
        remote = f"/data/local/tmp/__shot_{int(time.time())}.png"
        try:
            cap = self.shell(f"screencap -p {shlex.quote(remote)}", check=False)
            check_sz = self.shell(f"ls -s {shlex.quote(remote)}", check=False)
            if check_sz and "No such file" not in check_sz and not check_sz.strip().startswith("0 "):
                self.run(["pull", remote, local_path])
                if os.path.exists(local_path) and os.path.getsize(local_path) > 100:
                    return f"Saved: {local_path}"
        finally:
            self.shell(f"rm -f {shlex.quote(remote)}", check=False)

        raise ADBError(
            "এই স্ক্রিন/অ্যাপ থেকে স্ক্রিনশট নেওয়া সম্ভব হয়নি।\n"
            "সম্ভাব্য কারণ:\n"
            "• বর্তমান অ্যাপে Android Security Protection (FLAG_SECURE) চালু আছে (যেমন: ব্যাংকিং/বিকাশ অ্যাপ, Chrome Incognito, পাসওয়ার্ড স্ক্রিন বা DRM ভিডিও)।\n"
            "• অথবা ফোনের ডিসপ্লে সম্পূর্ণ অফ/লক অবস্থায় আছে।"
        )

    def screenrecord(self, local_path: str, seconds: int) -> str:
        seconds = max(1, min(180, seconds))
        remote = f"/sdcard/__rec_{int(time.time())}.mp4"
        self.shell(f"screenrecord --time-limit {seconds} {shlex.quote(remote)}", timeout=seconds + 30)
        self.run(["pull", remote, local_path], timeout=0)
        self.shell(f"rm {shlex.quote(remote)}", check=False)
        return f"Saved: {local_path}"

    @staticmethod
    def format_bytes(num_bytes: float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(num_bytes) < 1024.0 or unit == "TB":
                return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} TB"

    @staticmethod
    def get_local_size(path: str) -> int:
        if not os.path.exists(path):
            return 0
        if os.path.isfile(path):
            try:
                return os.path.getsize(path)
            except OSError:
                return 0
        total = 0
        for root_dir, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root_dir, f))
                except OSError:
                    pass
        return total

    def get_remote_size(self, remote_path: str) -> int:
        q = shlex.quote(remote_path)
        cmd = (
            f"if [ -f {q} ]; then stat -c %s {q} 2>/dev/null; "
            f"elif [ -d {q} ]; then du -sk {q} 2>/dev/null | tail -n 1 | awk '{{print $1 * 1024}}'; "
            f"else echo 0; fi"
        )
        try:
            out = self.shell(cmd, check=False, timeout=8).strip()
            for token in reversed(out.split()):
                if token.isdigit():
                    return int(token)
        except Exception:
            pass
        return 0

    def push(
        self,
        local_path: str,
        remote_path: str,
        progress_callback: Optional[Callable[[int, int, float, float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> str:
        if not os.path.exists(local_path):
            raise ADBError(f"লোকাল ফাইল বা ফোল্ডার পাওয়া যায়নি: {local_path}")

        remote_norm = remote_path.replace("\\", "/")
        total_bytes = self.get_local_size(local_path)

        # ফোনে গন্তব্য পাথ যদি ফোল্ডার হয়, তাহলে টার্গেট ফাইলের পূর্ণ পাথ নির্ণয় করা
        q_rem = shlex.quote(remote_norm)
        is_dir = self.shell(f"[ -d {q_rem} ] && echo 1 || echo 0", check=False, timeout=6).strip() == "1"
        if is_dir or remote_norm.endswith("/"):
            base_name = os.path.basename(os.path.normpath(local_path))
            target_remote = f"{remote_norm.rstrip('/')}/{base_name}"
        else:
            target_remote = remote_norm

        cmd = self._cmd() + ["push", local_path, remote_norm]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except FileNotFoundError as e:
            raise ADBError("adb পাওয়া যায়নি। Android Platform Tools install করে PATH-এ যোগ করুন।") from e

        start_time = time.time()
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                raise ADBError("ফাইল আপলোড বাতিল (Cancel) করা হয়েছে।")
            if progress_callback and total_bytes > 0:
                done_bytes = self.get_remote_size(target_remote)
                elapsed = max(0.1, time.time() - start_time)
                speed_bps = done_bytes / elapsed
                pct = min(99.9, (done_bytes / total_bytes) * 100.0)
                progress_callback(done_bytes, total_bytes, pct, speed_bps)
            time.sleep(0.5)

        stdout, stderr = proc.communicate()
        out = (stdout or stderr or "").strip()
        if proc.returncode != 0:
            raise ADBError(out or f"ADB push ব্যর্থ হয়েছে: {' '.join(cmd)}")

        if progress_callback and total_bytes > 0:
            elapsed = max(0.1, time.time() - start_time)
            progress_callback(total_bytes, total_bytes, 100.0, total_bytes / elapsed)
        return out

    def pull(
        self,
        remote_path: str,
        local_path: str,
        progress_callback: Optional[Callable[[int, int, float, float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> str:
        remote_norm = remote_path.replace("\\", "/")
        total_bytes = self.get_remote_size(remote_norm)

        if os.path.isdir(local_path):
            base_name = os.path.basename(remote_norm.rstrip("/"))
            target_local = os.path.join(local_path, base_name) if base_name else local_path
        else:
            target_local = local_path

        cmd = self._cmd() + ["pull", remote_norm, local_path]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except FileNotFoundError as e:
            raise ADBError("adb পাওয়া যায়নি। Android Platform Tools install করে PATH-এ যোগ করুন।") from e

        start_time = time.time()
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                raise ADBError("ফাইল ডাউনলোড বাতিল (Cancel) করা হয়েছে।")
            if progress_callback:
                done_bytes = self.get_local_size(target_local)
                elapsed = max(0.1, time.time() - start_time)
                speed_bps = done_bytes / elapsed
                pct = min(99.9, (done_bytes / total_bytes) * 100.0) if total_bytes > 0 else 0.0
                progress_callback(done_bytes, total_bytes, pct, speed_bps)
            time.sleep(0.35)

        stdout, stderr = proc.communicate()
        out = (stdout or stderr or "").strip()
        if proc.returncode != 0:
            raise ADBError(out or f"ADB pull ব্যর্থ হয়েছে: {' '.join(cmd)}")

        if progress_callback:
            final_bytes = total_bytes or self.get_local_size(target_local)
            elapsed = max(0.1, time.time() - start_time)
            progress_callback(final_bytes, final_bytes, 100.0, final_bytes / elapsed)
        return out

    def ls(self, remote_path: str) -> str:
        return self.shell(f"ls -la {shlex.quote(remote_path)}", check=False)

    # -------- Root (su) Core Helpers --------
    CRITICAL_SYSTEM_PACKAGES = frozenset(
        {
            "android",
            "com.android.systemui",
            "com.android.settings",
            "com.android.phone",
            "com.android.shell",
            "com.android.providers.settings",
            "com.android.providers.telephony",
            "com.topjohnwu.magisk",
            "com.sec.android.app.launcher",
        }
    )

    _PROTECTED_RM_PATHS = frozenset(
        {"/", "/system", "/data", "/data/data", "/data/app", "/vendor", "/product", "/proc", "/dev", "/sys", "/sdcard"}
    )

    @classmethod
    def _assert_safe_rm_path(cls, remote_path: str) -> str:
        clean = (remote_path or "").strip()
        if clean != "/":
            clean = clean.rstrip("/")
        if not clean or clean in cls._PROTECTED_RM_PATHS:
            raise ADBError(f"নিরাপত্তার কারণে সিস্টেমের মূল পাথ '{clean or '/'}' ডিলিট করা ব্লক করা হয়েছে।")
        return clean

    def has_root(self, timeout: int = 4) -> bool:
        """Returns True if working `su` root (uid=0) is granted to ADB shell without blocking."""
        try:
            out = self.shell("timeout 3 su -c id 2>/dev/null || su -c id 2>/dev/null", check=False, timeout=timeout)
            return "uid=0" in (out or "")
        except Exception:
            return False

    def root_status(self) -> dict:
        """Returns detailed Root / Magisk / Bootloader / SELinux status in one shell round-trip."""
        raw = self.shell(
            "echo '__SU__'; which su 2>/dev/null; "
            "echo '__SUV__'; su -v 2>/dev/null; "
            "echo '__MAG__'; magisk -v 2>/dev/null; "
            "echo '__ID__'; timeout 3 su -c id 2>/dev/null; "
            "echo '__SE__'; getenforce 2>/dev/null; "
            "echo '__LOCK__'; getprop ro.boot.flash.locked",
            check=False,
            timeout=6,
        )

        def _sec(tag: str) -> str:
            marker = f"__{tag}__"
            if marker not in (raw or ""):
                return ""
            return raw.split(marker, 1)[1].split("__", 1)[0].strip()

        su_path = _sec("SU")
        su_ver = _sec("SUV")
        mag_ver = _sec("MAG")
        uid_out = _sec("ID")
        selinux = _sec("SE")
        locked = _sec("LOCK")
        granted = "uid=0" in uid_out
        return {
            "rooted": granted,
            "su_present": bool(su_path),
            "su_path": su_path,
            "su_version": su_ver,
            "magisk_version": mag_ver,
            "uid": uid_out,
            "selinux": selinux,
            "bootloader_unlocked": locked == "0",
        }

    def root_shell(self, command: str, check: bool = True, timeout: Optional[int] = None) -> str:
        """Executes a command via `su -c` on a rooted device."""
        cmd = (command or "").strip()
        if not cmd:
            raise ADBError("Root কমান্ড খালি।")
        if not self.has_root():
            raise ADBError(
                "এই ডিভাইসে Root (`su`) অ্যাক্সেস পাওয়া যায়নি।\n"
                "ডিভাইস রুটেড হলে ফোনের Magisk অ্যাপে 'Shell'-কে Superuser পারমিশন (Grant) দিন।"
            )
        return self.shell(f"su -c {shlex.quote(cmd)}", check=check, timeout=timeout)

    def _read_root_sqlite(self, remote_db_paths: List[str], sql: str) -> List[tuple]:
        """
        Safely copies a protected SQLite DB (+ WAL/SHM if present) via `su -c` to a temporary
        PC directory, queries it using Python's built-in `sqlite3` (no phone sqlite3 binary needed!),
        and cleans up all temporary files immediately.
        """
        import sqlite3
        import tempfile

        if not self.has_root():
            return []
        probe_cmd = " || ".join(f"[ -f {shlex.quote(p)} ] && echo {shlex.quote(p)}" for p in remote_db_paths)
        found = self.root_shell(probe_cmd, check=False, timeout=6).strip()
        db_path = next((ln.strip() for ln in found.splitlines() if ln.strip().startswith("/")), "")
        if not db_path:
            return []

        stamp = f"{int(time.time())}_{secrets.token_hex(3)}"
        stage_dir = f"/data/local/tmp/__adb_sq_{stamp}"
        base_name = os.path.basename(db_path)
        try:
            copy_cmd = (
                f"mkdir -p {stage_dir} && "
                f"cp {shlex.quote(db_path)} {stage_dir}/{shlex.quote(base_name)} && "
                f"([ -f {shlex.quote(db_path + '-wal')} ] && cp {shlex.quote(db_path + '-wal')} {stage_dir}/{shlex.quote(base_name + '-wal')} || true) && "
                f"([ -f {shlex.quote(db_path + '-shm')} ] && cp {shlex.quote(db_path + '-shm')} {stage_dir}/{shlex.quote(base_name + '-shm')} || true) && "
                f"chmod -R 644 {stage_dir}/* 2>/dev/null; chmod 755 {stage_dir}"
            )
            self.root_shell(copy_cmd, check=False, timeout=10)
            with tempfile.TemporaryDirectory() as td:
                self.pull(stage_dir, td)
                local_db = os.path.join(td, os.path.basename(stage_dir), base_name)
                if not os.path.exists(local_db):
                    local_db = os.path.join(td, base_name)
                if not os.path.exists(local_db):
                    return []
                conn = sqlite3.connect(f"file:{local_db}?mode=ro", uri=True)
                try:
                    cur = conn.execute(sql)
                    return cur.fetchall()
                finally:
                    conn.close()
        except Exception:
            return []
        finally:
            self.root_shell(f"rm -rf {stage_dir}", check=False, timeout=5)

    def pull_root(
        self,
        remote_path: str,
        local_path: str,
        progress_callback: Optional[Callable[[int, int, float, float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> str:
        """Pulls a root-protected file/folder by staging a readable copy in /data/local/tmp."""
        clean = (remote_path or "").strip().rstrip("/") or "/"
        base = os.path.basename(clean) or "root_item"
        stamp = f"{int(time.time())}_{secrets.token_hex(3)}"
        stage_dir = f"/data/local/tmp/__adb_rpull_{stamp}"
        stage_item = f"{stage_dir}/{base}"
        try:
            self.root_shell(
                f"mkdir -p {stage_dir} && cp -r {shlex.quote(clean)} {shlex.quote(stage_item)} && chmod -R a+rX {stage_dir}",
                check=True,
                timeout=30,
            )
            return self.pull(stage_item, local_path, progress_callback=progress_callback, cancel_event=cancel_event)
        finally:
            self.root_shell(f"rm -rf {stage_dir}", check=False, timeout=6)

    def push_root(
        self,
        local_path: str,
        remote_path: str,
        progress_callback: Optional[Callable[[int, int, float, float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> str:
        """Pushes a local file to a root-protected remote destination via /data/local/tmp."""
        base = os.path.basename(local_path) or "upload.bin"
        stamp = f"{int(time.time())}_{secrets.token_hex(3)}"
        tmp_remote = f"/data/local/tmp/__adb_rpush_{stamp}_{base}"
        try:
            out = self.push(local_path, tmp_remote, progress_callback=progress_callback, cancel_event=cancel_event)
            self.root_shell(f"mv {shlex.quote(tmp_remote)} {shlex.quote(remote_path)}", check=True, timeout=15)
            return out
        except Exception:
            self.shell(f"rm -f {shlex.quote(tmp_remote)}", check=False, timeout=5)
            raise

    def mkdir(self, remote_path: str, use_root: bool = False) -> str:
        cmd = f"mkdir -p {shlex.quote(remote_path)}"
        return self.root_shell(cmd) if use_root else self.shell(cmd)

    def rm(self, remote_path: str, use_root: bool = False) -> str:
        safe = self._assert_safe_rm_path(remote_path)
        cmd = f"rm -rf {shlex.quote(safe)}"
        return self.root_shell(cmd) if use_root else self.shell(cmd)

    def install_apk(self, apk_path: str) -> str:
        return self.run(["install", "-r", apk_path], timeout=0)

    def uninstall_app(self, package: str) -> str:
        return self.run(["uninstall", package], check=False)

    def list_packages(self) -> str:
        return self.shell("pm list packages", check=False)

    def list_launcher_apps(self) -> List[dict]:
        """Returns only apps with a launcher icon in the app menu (App Name + Package Name)."""
        raw = self.shell(
            "cmd package query-activities -a android.intent.action.MAIN -c android.intent.category.LAUNCHER",
            check=False,
            timeout=20,
        )
        apps: dict[str, str] = {}
        current_pkg: Optional[str] = None
        current_label = ""

        def _flush():
            nonlocal current_pkg, current_label
            if current_pkg:
                existing = apps.get(current_pkg, "")
                if not existing or current_label:
                    apps[current_pkg] = current_label
            current_pkg = None
            current_label = ""

        for line in raw.splitlines():
            s = line.strip()
            if s.startswith("Activity #") or s.startswith("ResolveInfo{"):
                _flush()
            elif "packageName=" in s:
                val = s.split("packageName=", 1)[1].split()[0].strip()
                if val:
                    current_pkg = val
            elif "nonLocalizedLabel=" in s:
                val = s.split("nonLocalizedLabel=", 1)[1].strip()
                if val and val.lower() != "null" and not current_label:
                    current_label = val
        _flush()

        if not apps:
            brief = self.shell(
                "cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER",
                check=False,
            )
            for line in brief.splitlines():
                s = line.strip()
                if "/" in s and not s.startswith("Activity") and not s.startswith("priority"):
                    pkg = s.split("/", 1)[0].strip()
                    if pkg:
                        apps.setdefault(pkg, "")

        result: List[dict] = []
        for pkg, label in apps.items():
            display_name = label.strip()
            if not display_name:
                parts = pkg.split(".")
                tail = parts[-1]
                if tail in ("android", "app", "mobile", "client", "lite") and len(parts) >= 2:
                    tail = parts[-2]
                display_name = tail.replace("_", " ").replace("-", " ").title()
            result.append({"name": display_name, "package": pkg})

        result.sort(key=lambda item: (item["name"].lower(), item["package"].lower()))
        return result

    def list_apps_by_mode(self, mode: str = "launcher") -> List[dict]:
        """
        Lists apps by category (`launcher`, `user`, `system`, `disabled`) with live Frozen/Disabled status.
        """
        m = (mode or "launcher").strip().lower()
        dis_raw = self.shell("pm list packages -d", check=False, timeout=10)
        disabled_set = {
            ln.split("package:", 1)[1].strip()
            for ln in (dis_raw or "").splitlines()
            if ln.strip().startswith("package:")
        }
        sys_raw = self.shell("pm list packages -s", check=False, timeout=10)
        sys_set = {
            ln.split("package:", 1)[1].strip()
            for ln in (sys_raw or "").splitlines()
            if ln.strip().startswith("package:")
        }

        def _pretty_name(pkg: str) -> str:
            parts = pkg.split(".")
            tail = parts[-1]
            if tail in ("android", "app", "mobile", "client", "lite") and len(parts) >= 2:
                tail = parts[-2]
            return tail.replace("_", " ").replace("-", " ").title()

        if m == "launcher":
            base = self.list_launcher_apps()
            for item in base:
                pkg = item["package"]
                item["disabled"] = pkg in disabled_set
                item["is_system"] = pkg in sys_set
            return base

        flag_map = {"user": "-3", "system": "-s", "disabled": "-d"}
        flag = flag_map.get(m, "")
        raw = self.shell(f"pm list packages {flag}".strip(), check=False, timeout=12)
        pkgs = sorted(
            {
                ln.split("package:", 1)[1].strip()
                for ln in (raw or "").splitlines()
                if ln.strip().startswith("package:")
            }
        )
        items: List[dict] = []
        for pkg in pkgs:
            items.append(
                {
                    "name": _pretty_name(pkg),
                    "package": pkg,
                    "disabled": pkg in disabled_set,
                    "is_system": pkg in sys_set,
                }
            )
        items.sort(key=lambda item: (item["name"].lower(), item["package"].lower()))
        return items

    def freeze_app(self, package: str, freeze: bool = True) -> str:
        """Freezes (disables) or unfreezes (enables) a user or system bloatware app (uses Root when available)."""
        pkg = (package or "").strip()
        if not pkg:
            raise ADBError("প্যাকেজের নাম খালি।")
        if freeze and pkg in self.CRITICAL_SYSTEM_PACKAGES:
            raise ADBError(
                f"নিরাপত্তার কারণে কোর সিস্টেম প্যাকেজ '{pkg}' ফ্রিজ করা ব্লক করা হয়েছে (এতে ফোন বুটলুপ হতে পারে)।"
            )

        rooted = self.has_root()
        if freeze:
            cmd = f"pm disable-user --user 0 {shlex.quote(pkg)}"
            out = self.root_shell(cmd, check=False, timeout=10) if rooted else self.shell(cmd, check=False, timeout=10)
            low = (out or "").lower()
            if ("securityexception" in low or "cannot disable" in low or "failed" in low) and rooted:
                out = self.root_shell(f"pm disable {shlex.quote(pkg)}", check=False, timeout=10)
            if "securityexception" in (out or "").lower() or "permission" in (out or "").lower():
                raise ADBError(f"'{pkg}' ফ্রিজ করা যায়নি (নন-রুট সীমাবদ্ধতা): {out}")
            return out or f"❄️ Frozen: {pkg}"
        else:
            cmd = f"pm enable {shlex.quote(pkg)}"
            out = self.root_shell(cmd, check=False, timeout=10) if rooted else self.shell(cmd, check=False, timeout=10)
            return out or f"🔥 Unfrozen: {pkg}"

    def launch_app(self, package: str) -> str:
        """Launches an app reliably via resolve-activity + am start (avoids monkey timeouts on Android 12-14)."""
        pkg = package.strip()
        if not pkg:
            raise ADBError("প্যাকেজের নাম খালি।")
        if "/" in pkg:
            return self.shell(f"am start -n {shlex.quote(pkg)}", check=False, timeout=10)

        resolved = self.shell(
            f"cmd package resolve-activity --brief -c android.intent.category.LAUNCHER {shlex.quote(pkg)}",
            check=False,
            timeout=8,
        )
        comp = ""
        for line in reversed((resolved or "").splitlines()):
            s = line.strip()
            if "/" in s and " " not in s:
                comp = s
                break
        if comp:
            return self.shell(f"am start -n {shlex.quote(comp)}", check=False, timeout=10)
        return self.shell(
            f"am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -p {shlex.quote(pkg)}",
            check=False,
            timeout=10,
        )

    def open_activity(self, component: str) -> str:
        """Opens a specific Android Activity (pkg/activity). Falls back to launch_app if private (SecurityException)."""
        comp = component.strip()
        if not comp:
            raise ADBError("Activity কম্পোনেন্ট খালি।")
        if "/" not in comp:
            return self.launch_app(comp)
        out = self.shell(f"am start -n {shlex.quote(comp)}", check=False, timeout=10)
        low = (out or "").lower()
        if "securityexception" in low or "permission denial" in low or "does not exist" in low:
            pkg = comp.split("/", 1)[0].strip()
            fb = self.launch_app(pkg)
            return f"ℹ️ প্রাইভেট পেজ ({comp}) সরাসরি খোলা যায়নি, তাই মূল অ্যাপ ({pkg}) খোলা হয়েছে।\n{fb}"
        return out or f"Opened {comp}"

    def get_top_component(self) -> tuple[str, str]:
        """Returns (package, 'package/activity') of the currently focused foreground screen."""
        import re

        focus = self.current_activity() or ""
        # Matches e.g. com.android.settings/com.android.settings.Settings$DisplaySettingsActivity
        m = re.search(r"([a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+)/([a-zA-Z0-9_.$]+)", focus)
        if not m:
            return "", ""
        pkg, act = m.group(1), m.group(2)
        return pkg, f"{pkg}/{act}"

    def package_info(self, package: str) -> str:
        return self.shell(f"dumpsys package {shlex.quote(package)}", check=False)

    def clear_data(self, package: str) -> str:
        return self.shell(f"pm clear {shlex.quote(package)}", check=False)

    def force_stop(self, package: str) -> str:
        return self.shell(f"am force-stop {shlex.quote(package)}", check=False)

    def start_app(self, package: str) -> str:
        return self.run(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"], check=False)

    def current_activity(self) -> str:
        return self.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'", check=False)

    def tap(self, x: int, y: int) -> str:
        return self.shell(f"input tap {x} {y}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> str:
        return self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration}")

    def input_text(self, text: str, delay_after_first: float = 1.0) -> str:
        if not text:
            return ""
        # গুগল সার্চবার বা ডায়নামিক ইনপুটে ১ম অক্ষর টাইপ হওয়ার পর নতুন অ্যাক্টিভিটি ওপেন হয়,
        # তাই ১ম অক্ষর পাঠিয়ে পর্যাপ্ত বিরতি (প্রায় ১ সেকেন্ড) দিয়ে বাকিটা চাংক আকারে পাঠালে অক্ষর মিস হয় না।
        if len(text) > 1:
            first = "%s" if text[0] == " " else text[0]
            self.shell(f"input text {shlex.quote(first)}")
            if delay_after_first > 0:
                time.sleep(delay_after_first)
            rest = text[1:]
            chunk_size = 20
            last_out = ""
            for i in range(0, len(rest), chunk_size):
                chunk = rest[i:i + chunk_size].replace(" ", "%s")
                last_out = self.shell(f"input text {shlex.quote(chunk)}")
                if i + chunk_size < len(rest):
                    time.sleep(0.06)
            return last_out
        safe = "%s" if text == " " else text
        return self.shell(f"input text {shlex.quote(safe)}")

    def get_phone_clipboard(self) -> str:
        """Attempts to read current clipboard text from the Android device via dumpsys/service call."""
        import re

        # ১. dumpsys clipboard থেকে ক্লিপবোর্ড ডাটা বের করার চেষ্টা
        dump = self.shell("dumpsys clipboard", check=False, timeout=8)
        if dump:
            # সাধারণত ClipData { text/plain {T:copied text} } বা {T(5):hello} থাকে
            matches = re.findall(r"\{T(?:\(\d+\))?:(.*?)\}", dump, flags=re.DOTALL)
            if matches:
                text = matches[0].strip()
                if text and text.lower() != "null":
                    return text

        # ২. service call clipboard থেকে Parcel UTF-16 ডিকোড করার চেষ্টা (Android 9-13)
        raw_parcel = self.shell("service call clipboard 2 s16 com.android.shell", check=False, timeout=6)
        if raw_parcel and "Parcel(" in raw_parcel and "SecurityException" not in raw_parcel:
            hex_chunks = re.findall(r"([0-9a-fA-F]{8})\s+", raw_parcel)
            if len(hex_chunks) > 2:
                raw_bytes = b"".join(bytes.fromhex(h)[::-1] for h in hex_chunks[2:])
                decoded = raw_bytes.decode("utf-16-le", errors="ignore").replace("\x00", "").strip()
                if decoded:
                    return decoded

        raise ADBError(
            "ফোনের ক্লিপবোর্ড ডাটা পড়া যায়নি।\n"
            "Android 10+ সিকিউরিটি পলিসির কারণে ব্যাকগ্রাউন্ড ADB থেকে অনেক ফোনে ক্লিপবোর্ড ব্লক থাকে।"
        )

    def keyevent(self, key: str) -> str:
        return self.shell(f"input keyevent {shlex.quote(key)}")

    def reboot(self) -> str:
        return self.run(["reboot"], check=False)

    def tcpip(self, port: int = 5555) -> str:
        return self.run(["tcpip", str(port)], check=False)

    def open_url(self, url: str) -> str:
        target = url.strip()
        if not target:
            raise ADBError("অনুগ্রহ করে একটি সঠিক URL লিখুন।")
        if "://" not in target:
            target = "https://" + target
        return self.shell(f"am start -a android.intent.action.VIEW -d {shlex.quote(target)}", check=False)

    # -------- quick system controls --------
    def set_mobile_data(self, enabled: bool) -> str:
        state = "enable" if enabled else "disable"
        return self.shell(f"svc data {state}", check=False) or f"Mobile Data {state}d"

    def set_bluetooth(self, enabled: bool) -> str:
        state = "enable" if enabled else "disable"
        out1 = self.shell(f"svc bluetooth {state}", check=False)
        out2 = self.shell(f"cmd bluetooth_manager {state}", check=False)
        return (out1 or out2 or f"Bluetooth {state}d").strip()

    def set_location(self, enabled: bool) -> str:
        flag = "true" if enabled else "false"
        mode = "3" if enabled else "0"
        self.shell(f"cmd location set-location-enabled {flag}", check=False)
        self.shell(f"settings put secure location_mode {mode}", check=False)
        return f"Location {'ON' if enabled else 'OFF'}"

    def get_flashlight_state(self) -> Optional[bool]:
        """Reads live Flashlight state from SystemUI (`mFlashlightEnabled=true/false`), or None if unavailable."""
        import re

        out = self.shell(
            "dumpsys activity service com.android.systemui | grep -m 1 'mFlashlightEnabled='",
            check=False,
            timeout=6,
        )
        m = re.search(r"mFlashlightEnabled=(true|false)", out or "", flags=re.IGNORECASE)
        if m:
            return m.group(1).lower() == "true"
        return None

    @staticmethod
    def _find_qs_flashlight_node(xml_raw: str) -> Optional[dict]:
        """Parses Quick Settings UI hierarchy XML to locate the Flashlight/Torch tile bounds & checked state."""
        import re
        import xml.etree.ElementTree as ET

        if not xml_raw or not xml_raw.strip().startswith("<"):
            return None
        try:
            root = ET.fromstring(xml_raw)
        except ET.ParseError:
            return None

        keywords = ("flashlight", "torch", "osflashlight", "ফ্ল্যাশলাইট", "টর্চ")
        candidates = []
        for node in root.iter("node"):
            pkg = (node.attrib.get("package") or "").lower()
            desc = (node.attrib.get("content-desc") or "").strip().lower()
            text = (node.attrib.get("text") or "").strip().lower()
            if not desc and not text:
                continue
            matched = any(
                (k in desc and len(desc) <= 40) or (k in text and len(text) <= 40)
                for k in keywords
            )
            if not matched:
                continue
            nums = list(map(int, re.findall(r"\d+", node.attrib.get("bounds", ""))))
            if len(nums) != 4:
                continue
            x1, y1, x2, y2 = nums
            if x2 <= x1 or y2 <= y1:
                continue
            is_sysui = 1 if "systemui" in pkg else 0
            clickable = 1 if node.attrib.get("clickable") == "true" else 0
            checkable = 1 if node.attrib.get("checkable") == "true" else 0
            # Prefer clickable & checkable SystemUI Switch/tile node
            score = (is_sysui, clickable, checkable, (x2 - x1) * (y2 - y1))
            checked_val: Optional[bool] = None
            if checkable:
                checked_val = node.attrib.get("checked") == "true"
            elif text in ("on", "off"):
                checked_val = text == "on"
            candidates.append((score, (x1 + x2) // 2, (y1 + y2) // 2, checked_val))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, cx, cy, checked_val = candidates[0]
        return {"cx": cx, "cy": cy, "checked": checked_val}

    def set_flashlight(self, enabled: bool) -> str:
        """
        Turns Flashlight ON or OFF:
        - Rooted devices: Instant silent sysfs LED hardware toggle (0.1s, ZERO screen wake!)
        - Non-root fallback: SystemUI state + Quick Settings tile via uiautomator
        """
        import re

        target_label = "ON" if enabled else "OFF"

        # 👑 Root Fast-Path: Instant silent hardware LED toggle without waking screen or expanding QS!
        if self.has_root():
            val = "1" if enabled else "0"
            sysfs_cmd = (
                "for p in "
                "/sys/class/camera/flash/rear_flash "
                "/sys/class/leds/flashlight/brightness "
                "/sys/class/leds/torch-light0/brightness "
                "/sys/class/leds/torch-light1/brightness "
                "/sys/class/leds/led:torch_0/brightness "
                "/sys/class/leds/flash_led/brightness "
                "/sys/class/leds/mt6360_pmu_fled1/brightness; do "
                "if [ -w \"$p\" ] || [ -e \"$p\" ]; then "
                "if [ \"$p\" = \"/sys/class/camera/flash/rear_flash\" ]; then "
                f"echo {val} > \"$p\" && echo \"OK:$p\" && exit 0; "
                "else "
                "mb=$(cat \"$(dirname \"$p\")/max_brightness\" 2>/dev/null); "
                "[ -z \"$mb\" ] && mb=128; "
                f"wval={val}; [ \"$wval\" = \"1\" ] && wval=\"$mb\"; "
                "echo \"$wval\" > \"$p\" && echo \"OK:$p\" && exit 0; "
                "fi; fi; done; echo 'NO_NODE'"
            )
            r_out = self.root_shell(sysfs_cmd, check=False, timeout=5)
            if "OK:" in (r_out or ""):
                return f"🔦 Flashlight (👑 Root Instant) সফলভাবে {target_label} করা হয়েছে!"

        cur_state = self.get_flashlight_state()
        if cur_state is not None and cur_state == enabled:
            return f"🔦 Flashlight ইতিমধ্যেই {target_label} আছে।"

        # স্ক্রিন অফ থাকলে সাময়িকভাবে জাগানো (লক স্ক্রিনেও Quick Settings কাজ করে)
        pwr = self.shell("dumpsys power | grep -E 'mWakefulness=|Display Power: state='", check=False, timeout=5)
        if "Asleep" in pwr or "state=OFF" in pwr:
            self.keyevent("KEYCODE_WAKEUP")
            time.sleep(0.45)

        remote_xml = f"/data/local/tmp/__adb_qs_torch_{int(time.time())}.xml"
        tile = None
        try:
            self.shell("cmd statusbar expand-settings", check=False, timeout=5)
            time.sleep(0.55)

            for page_try in range(2):
                self.shell(f"uiautomator dump {remote_xml}", check=False, timeout=8)
                raw_xml = self.shell(f"cat {remote_xml}", check=False, timeout=6)
                tile = self._find_qs_flashlight_node(raw_xml)
                if tile is not None:
                    break
                if page_try == 0:
                    # প্রথম পেজে না পেলে Quick Settings পরের পেজে সোয়াইপ করে খোঁজা
                    wm = self.shell("wm size", check=False, timeout=5)
                    m_sz = re.search(r"(\d+)x(\d+)", wm or "")
                    w, h = (int(m_sz.group(1)), int(m_sz.group(2))) if m_sz else (720, 1600)
                    self.swipe(int(w * 0.85), int(h * 0.35), int(w * 0.15), int(h * 0.35), duration=250)
                    time.sleep(0.45)

            if tile is None:
                raise ADBError(
                    "Quick Settings প্যানেলে 'Flashlight / Torch' টাইল খুঁজে পাওয়া যায়নি।\n"
                    "টিপস: ফোনের Quick Settings-এ Flashlight টাইলটি প্রথম বা দ্বিতীয় পেজে রাখুন।"
                )

            # UI চেকবক্স থেকেই যদি দেখা যায় ইতিমধ্যেই কাঙ্ক্ষিত অবস্থায় আছে, তবে ট্যাপ বাদ দেওয়া
            if tile["checked"] is not None and tile["checked"] == enabled:
                return f"🔦 Flashlight ইতিমধ্যেই {target_label} আছে।"

            self.tap(tile["cx"], tile["cy"])
            time.sleep(0.35)
        finally:
            self.shell(f"rm -f {remote_xml}", check=False, timeout=4)
            self.shell("cmd statusbar collapse", check=False, timeout=5)

        final_state = self.get_flashlight_state()
        if final_state is not None:
            if final_state == enabled:
                return f"🔦 Flashlight সফলভাবে {target_label} করা হয়েছে!"
            return f"⚠️ Flashlight ট্যাপ পাঠানো হয়েছে, কিন্তু বর্তমান অবস্থা: {'ON' if final_state else 'OFF'}"
        return f"🔦 Flashlight {target_label} কমান্ড সম্পন্ন হয়েছে।"

    def set_brightness(self, level: int) -> str:
        level = max(0, min(255, int(level)))
        self.shell("settings put system screen_brightness_mode 0", check=False)
        self.shell(f"settings put system screen_brightness {level}", check=False)
        return f"Screen Brightness set to {level}/255"

    def set_screen_timeout(self, ms: int) -> str:
        ms = max(5000, int(ms))
        self.shell(f"settings put system screen_off_timeout {ms}", check=False)
        sec = ms // 1000
        label = f"{sec // 60} min" if sec >= 60 else f"{sec} sec"
        return f"Screen Timeout set to {label} ({ms} ms)"

    def set_airplane_mode(self, enabled: bool) -> str:
        """Toggles Airplane Mode cleanly (uses Root broadcast when rooted, or Android 11+ cmd connectivity)."""
        state_word = "enable" if enabled else "disable"
        num = "1" if enabled else "0"
        bval = "true" if enabled else "false"
        if self.has_root():
            self.root_shell(
                f"cmd connectivity airplane-mode {state_word} 2>/dev/null || "
                f"(settings put global airplane_mode_on {num} && "
                f"am broadcast -a android.intent.action.AIRPLANE_MODE --ez state {bval})",
                check=False,
                timeout=8,
            )
            return f"✈️ Airplane Mode (👑 Root) {'ON' if enabled else 'OFF'} করা হয়েছে!"
        out = self.shell(f"cmd connectivity airplane-mode {state_word}", check=False, timeout=8)
        if "unknown" in (out or "").lower() or "securityexception" in (out or "").lower():
            raise ADBError("নন-রুট ডিভাইসে এই Android ভার্সনে Airplane Mode টগল ব্লক করা আছে (Root প্রয়োজন)।")
        return f"✈️ Airplane Mode {'ON' if enabled else 'OFF'} করা হয়েছে!"

    def set_hotspot(self, enabled: bool) -> str:
        """Toggles Wi-Fi Mobile Hotspot (requires Root on Android 10–15)."""
        if not self.has_root():
            raise ADBError("Hotspot ON/OFF কন্ট্রোল করার জন্য রুটেড ডিভাইস (`su`) প্রয়োজন।")
        if enabled:
            out = self.root_shell(
                "cmd wifi start-softap 2>/dev/null || cmd tethering start-tethering wifi 2>/dev/null || svc wifi disable",
                check=False,
                timeout=8,
            )
            return f"📡 Hotspot (👑 Root) ON কমান্ড পাঠানো হয়েছে। {(out or '').strip()}".strip()
        out = self.root_shell(
            "cmd wifi stop-softap 2>/dev/null || cmd tethering stop-tethering wifi 2>/dev/null",
            check=False,
            timeout=8,
        )
        return f"📡 Hotspot (👑 Root) OFF করা হয়েছে। {(out or '').strip()}".strip()

    # -------- app permissions --------
    def grant_permission(self, package: str, permission: str) -> str:
        out = self.shell(f"pm grant {shlex.quote(package)} {shlex.quote(permission)}", check=False)
        return out or f"✔ Granted {permission} to {package}"

    def revoke_permission(self, package: str, permission: str) -> str:
        out = self.shell(f"pm revoke {shlex.quote(package)} {shlex.quote(permission)}", check=False)
        return out or f"🚫 Revoked {permission} from {package}"

    def list_app_permissions(self, package: str) -> str:
        return self.shell(
            f"dumpsys package {shlex.quote(package)} | grep -E 'android\\.permission\\.' | head -n 45",
            check=False,
        )

    # -------- PC -> Phone clipboard & Unicode/Bangla helper --------
    def set_phone_clipboard(self, text: str) -> str:
        """Sets clipboard text on the device (supports Bangla/Emoji where shell permits)."""
        if not text:
            raise ADBError("ক্লিপবোর্ডে পাঠানোর জন্য টেক্সট খালি।")
        # ১. cmd clipboard (কাস্টম/নতুন ROM) ও service call clipboard চেষ্টা
        out1 = self.shell(f"cmd clipboard set {shlex.quote(text)}", check=False, timeout=6)
        if out1 and "unknown" not in out1.lower() and "error" not in out1.lower() and "can't find" not in out1.lower():
            return "✔ ফোনের ক্লিপবোর্ডে টেক্সট পাঠানো হয়েছে।"

        # ২. service call clipboard (Android 9-14: transaction 1 = setPrimaryClip বা 2)
        out2 = self.shell(f"am broadcast -a clipper.set -e text {shlex.quote(text)}", check=False, timeout=6)
        if "result=-1" in out2 or "Broadcast completed: result=1" in out2:
            return "✔ Clipper-এর মাধ্যমে ফোনের ক্লিপবোর্ডে পাঠানো হয়েছে।"

        # ৩. Android 10+ ব্যাকগ্রাউন্ড সিকিউরিটির জন্য scrcpy ক্লিপবোর্ড সিংক সবচেয়ে নির্ভরযোগ্য
        raise ADBError(
            "Android 10+ সিকিউরিটির কারণে ব্যাকগ্রাউন্ড ADB থেকে সরাসরি ফোনের ক্লিপবোর্ডে লেখা ব্লক করা।\n"
            "টিপস: টেক্সটটি আপনার পিসির ক্লিপবোর্ডে কপি করা হয়েছে — scrcpy স্ক্রিন শেয়ার উইন্ডোতে Ctrl+V চাপলেই বাংলা/ইমোজি সরাসরি পেস্ট হবে!"
        )

    def send_unicode_text(self, text: str, auto_paste: bool = True) -> str:
        """Sends ASCII via input_text, or Unicode/Bangla/Emoji via clipboard + KEYCODE_PASTE."""
        if not text:
            return ""
        if text.isascii():
            return self.input_text(text, delay_after_first=1.0)
        msg = self.set_phone_clipboard(text)
        if auto_paste:
            time.sleep(0.15)
            self.keyevent("279")  # KEYCODE_PASTE
            return msg + " এবং Paste (KEYCODE_PASTE) পাঠানো হয়েছে।"
        return msg

    # -------- live notifications --------
    def list_notifications(self, limit: int = 30) -> List[dict]:
        """Parses active notifications from dumpsys notification."""
        import re

        dump = self.shell("dumpsys notification --noredact", check=False, timeout=12)
        if not dump or "NotificationRecord(" not in dump:
            dump = self.shell("dumpsys notification", check=False, timeout=12)
        if not dump:
            return []

        records = re.split(r"NotificationRecord\(", dump)[1:]
        items: List[dict] = []
        seen = set()
        for rec in records:
            pkg_m = re.search(r"\bpkg=([^\s\)]+)", rec)
            pkg = pkg_m.group(1) if pkg_m else "unknown"

            title_m = re.search(r"android\.title=(?:String\s*\((.*?)\)|([^\n]+))", rec)
            title = ""
            if title_m:
                title = (title_m.group(1) or title_m.group(2) or "").strip()

            text_m = re.search(r"android\.text=(?:String\s*\((.*?)\)|([^\n]+))", rec)
            body = ""
            if text_m:
                body = (text_m.group(1) or text_m.group(2) or "").strip()

            if title.lower() == "null":
                title = ""
            if body.lower() == "null":
                body = ""
            if not title and not body:
                continue

            sig = (pkg, title, body)
            if sig in seen:
                continue
            seen.add(sig)
            items.append({"package": pkg, "title": title or "(No Title)", "text": body})
            if len(items) >= limit:
                break
        return items

    # -------- SMS / OTP & Call Logs --------
    @staticmethod
    def _parse_content_rows(raw: str) -> List[dict]:
        import re

        if not raw or "No result found" in raw:
            return []
        if "SecurityException" in raw or "Permission Denial" in raw:
            raise ADBError(
                "ফোন থেকে SMS/Call Log পড়ার পারমিশন ব্লক করা আছে (Android Security Policy)।\n"
                "কিছু ফোনে Developer Options-এ 'USB Debugging (Security settings)' চালু করলে এটি কাজ করে।"
            )
        rows: List[dict] = []
        current: Optional[str] = None
        for line in raw.splitlines():
            if line.startswith("Row: "):
                if current is not None:
                    rows.append(current)
                current = line[5:]
            elif current is not None:
                current += "\n" + line
        if current is not None:
            rows.append(current)

        parsed: List[dict] = []
        for r in rows:
            # Row format: 0 key=val, key2=val2, ...
            r_clean = re.sub(r"^\d+\s+", "", r.strip())
            parts = re.split(r",\s+(?=[a-zA-Z_][a-zA-Z0-9_]*=)", r_clean)
            d: dict = {}
            for p in parts:
                if "=" in p:
                    k, v = p.split("=", 1)
                    d[k.strip()] = v.strip()
            if d:
                parsed.append(d)
        return parsed

    @staticmethod
    def extract_otp(text: str) -> str:
        import re

        if not text:
            return ""
        # OTP/Code/PIN কীওয়ার্ডের কাছাকাছি ৪-৮ ডিজিট খোঁজা, নাহলে যেকোনো ৪-৮ ডিজিট
        m = re.search(
            r"(?:otp|code|pin|passcode|verification|verify|ভেরিফিকেশন|কোড)[^\d]{0,20}(\d{4,8})\b",
            text,
            flags=re.IGNORECASE,
        )
        if m:
            return m.group(1)
        m2 = re.search(r"\b(\d{4,8})\b", text)
        return m2.group(1) if m2 else ""

    def list_sms(self, limit: int = 25) -> List[dict]:
        from datetime import datetime

        raw = self.shell(
            "content query --uri content://sms/inbox --projection _id:address:date:body --sort 'date DESC'",
            check=False,
            timeout=10,
        )
        rows: List[dict] = []
        blocked = "SecurityException" in (raw or "") or "Permission Denial" in (raw or "")
        if not blocked:
            rows = self._parse_content_rows(raw)[:limit]

        # 👑 Root Unblocked Fallback (su content query -> direct SQLite mmssms.db)
        if (blocked or not rows) and self.has_root():
            r_raw = self.root_shell(
                "content query --uri content://sms/inbox --projection _id:address:date:body --sort 'date DESC'",
                check=False,
                timeout=10,
            )
            if r_raw and "SecurityException" not in r_raw and "Permission Denial" not in r_raw:
                rows = self._parse_content_rows(r_raw)[:limit]
            if not rows:
                sq_rows = self._read_root_sqlite(
                    [
                        "/data/user_de/0/com.android.providers.telephony/databases/mmssms.db",
                        "/data/data/com.android.providers.telephony/databases/mmssms.db",
                    ],
                    f"SELECT address, date, body FROM sms WHERE type=1 ORDER BY date DESC LIMIT {int(limit)}",
                )
                for addr, ts, body in sq_rows:
                    rows.append({"address": str(addr or "Unknown"), "date": str(ts or ""), "body": str(body or "")})
        elif blocked:
            # Trigger standard helpful error message on non-root device
            self._parse_content_rows(raw)

        out: List[dict] = []
        for r in rows:
            ts = r.get("date", "")
            dt_str = ts
            if ts.isdigit():
                try:
                    dt_str = datetime.fromtimestamp(int(ts) / 1000.0).strftime("%d-%m %I:%M %p")
                except Exception:
                    pass
            body = r.get("body", "")
            out.append(
                {
                    "address": r.get("address", "Unknown"),
                    "date": dt_str,
                    "body": body,
                    "otp": self.extract_otp(body),
                }
            )
        return out

    def list_call_logs(self, limit: int = 25) -> List[dict]:
        from datetime import datetime

        raw = self.shell(
            "content query --uri content://call_log/calls --projection number:name:type:date:duration --sort 'date DESC'",
            check=False,
            timeout=10,
        )
        rows: List[dict] = []
        blocked = "SecurityException" in (raw or "") or "Permission Denial" in (raw or "")
        if not blocked:
            rows = self._parse_content_rows(raw)[:limit]

        # 👑 Root Unblocked Fallback (su content query -> direct SQLite calllog.db / contacts2.db)
        if (blocked or not rows) and self.has_root():
            r_raw = self.root_shell(
                "content query --uri content://call_log/calls --projection number:name:type:date:duration --sort 'date DESC'",
                check=False,
                timeout=10,
            )
            if r_raw and "SecurityException" not in r_raw and "Permission Denial" not in r_raw:
                rows = self._parse_content_rows(r_raw)[:limit]
            if not rows:
                sq_rows = self._read_root_sqlite(
                    [
                        "/data/user_de/0/com.android.providers.contacts/databases/calllog.db",
                        "/data/data/com.android.providers.contacts/databases/calllog.db",
                        "/data/data/com.android.providers.contacts/databases/contacts2.db",
                    ],
                    f"SELECT number, name, type, date, duration FROM calls ORDER BY date DESC LIMIT {int(limit)}",
                )
                for num, name, ctype, ts, dur in sq_rows:
                    rows.append(
                        {
                            "number": str(num or "Unknown"),
                            "name": str(name or ""),
                            "type": str(ctype or ""),
                            "date": str(ts or ""),
                            "duration": str(dur or "0"),
                        }
                    )
        elif blocked:
            self._parse_content_rows(raw)

        type_map = {"1": "Incoming", "2": "Outgoing", "3": "Missed", "5": "Rejected"}
        out: List[dict] = []
        for r in rows:
            ts = r.get("date", "")
            dt_str = ts
            if ts.isdigit():
                try:
                    dt_str = datetime.fromtimestamp(int(ts) / 1000.0).strftime("%d-%m %I:%M %p")
                except Exception:
                    pass
            name = r.get("name", "")
            if name.lower() == "null":
                name = ""
            out.append(
                {
                    "number": r.get("number", "Unknown"),
                    "name": name or "-",
                    "type": type_map.get(r.get("type", ""), r.get("type", "")),
                    "date": dt_str,
                    "duration": f"{r.get('duration', '0')}s",
                }
            )
        return out

    def dial_number(self, number: str) -> str:
        num = number.strip().replace(" ", "")
        if not num:
            raise ADBError("অনুগ্রহ করে একটি মোবাইল নাম্বার লিখুন।")
        # Android Dialer drops raw '#' as URI fragment unless URL-encoded as '%23' (needed for USSD like *511# / *2#)
        uri_num = num.replace("#", "%23")
        return self.shell(f"am start -a android.intent.action.DIAL -d {shlex.quote('tel:' + uri_num)}", check=False)

    def call_action(self, action: str) -> str:
        if action == "answer":
            return self.keyevent("KEYCODE_CALL")
        if action == "end":
            return self.keyevent("KEYCODE_ENDCALL")
        raise ADBError(f"Unknown call action: {action}")

    # -------- SIM & Phone Number Detection --------
    @staticmethod
    def _decode_binder_string16(raw: str) -> str:
        """Decodes a UTF-16LE string16 from Android `service call` Parcel hex dump output."""
        import re

        if not raw or "Exception" in raw or "Error" in raw or "Parcel data" in raw:
            return ""
        words: List[int] = []
        for line in raw.splitlines():
            m = re.match(r"\s*0x[0-9a-fA-F]+:\s+([0-9a-fA-F ]{8,40})", line)
            if m:
                for hx in m.group(1).split():
                    if len(hx) == 8:
                        words.append(int(hx, 16))
        if len(words) < 3 or words[0] != 0:
            return ""
        slen = words[1]
        if slen <= 0 or slen > 64:
            return ""
        raw_bytes = b"".join(w.to_bytes(4, "little") for w in words[2:])
        return raw_bytes[: slen * 2].decode("utf-16-le", errors="ignore").rstrip("\x00").strip()

    @staticmethod
    def _is_valid_phone_number(val: str) -> bool:
        """Validates a candidate phone number (rejects redacted masks like [****] and 18-20 digit ICCIDs)."""
        import re

        s = (val or "").strip()
        if not s or "*" in s or s.lower() in ("null", "none", "unknown", "empty"):
            return False
        digits = re.sub(r"\D", "", s)
        # Real phone numbers are 7–15 digits; ICCIDs are 18–20 digits starting with 89
        if len(digits) < 7 or len(digits) > 15:
            return False
        if len(digits) >= 14 and digits.startswith("89"):
            return False
        return bool(re.fullmatch(r"\+?[\d\-\s\(\)]{7,22}", s))

    @staticmethod
    def _ussd_for_carrier(carrier: str, mcc_mnc: str = "") -> str:
        """Returns quick 'Check My Number' USSD code for common carriers (especially Bangladesh)."""
        c = (carrier or "").lower()
        code = (mcc_mnc or "").strip()
        if code == "47003" or "banglalink" in c:
            return "*511#"
        if code == "47001" or "grameen" in c or c == "gp":
            return "*2#"
        if code == "47002" or "robi" in c:
            return "*2#"
        if code == "47007" or "airtel" in c:
            return "*2#"
        if code == "47004" or "teletalk" in c:
            return "*551#"
        if "skitto" in c:
            return "*121*1*1#"
        return ""

    def get_sim_info(self) -> List[dict]:
        """
        Detects active SIM slots, carrier names, and mobile phone numbers via non-root ADB:
        1. `dumpsys isub` (active subscriptions, carrier, country ISO, unredacted number if exposed)
        2. `service call iphonesubinfo` (UTF-16LE Parcel line1Number for devices exposing EF_MSISDN to shell)
        3. `dumpsys ims` (VoLTE sip:/tel: MSISDN URI if present)
        4. `getprop gsm.sim.operator.*` fallback if `dumpsys isub` has no active rows
        """
        import re

        raw = self.shell(
            "dumpsys isub | head -n 65; "
            "echo '__ALPHA__'; getprop gsm.sim.operator.alpha; "
            "echo '__NUM__'; getprop gsm.sim.operator.numeric; "
            "echo '__ISO__'; getprop gsm.sim.operator.iso-country; "
            "echo '__STATE__'; getprop gsm.sim.state",
            check=False,
            timeout=8,
        )
        parts = (raw or "").split("__ALPHA__")
        isub_txt = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        alpha_raw = rest.split("__NUM__")[0].strip() if "__NUM__" in rest else ""
        rest2 = rest.split("__NUM__")[1] if "__NUM__" in rest else ""
        num_raw = rest2.split("__ISO__")[0].strip() if "__ISO__" in rest2 else ""
        rest3 = rest2.split("__ISO__")[1] if "__ISO__" in rest2 else ""
        iso_raw = rest3.split("__STATE__")[0].strip() if "__STATE__" in rest3 else ""
        state_raw = rest3.split("__STATE__")[1].strip() if "__STATE__" in rest3 else ""

        by_slot: Dict[int, dict] = {}
        for line in isub_txt.splitlines():
            if "Active subscription info list changed" in line:
                break
            if "simSlotIndex=" not in line:
                continue
            m_slot = re.search(r"simSlotIndex=(-?\d+)", line)
            if not m_slot:
                continue
            slot_idx = int(m_slot.group(1))
            if slot_idx < 0 or slot_idx in by_slot:
                continue

            m_id = re.search(r"\bid=(\d+)", line)
            m_disp = re.search(r"displayName=([^\s\]\}]+(?:\s+[^\s\]\}=]+)*?)(?=\s+[a-zA-Z_]+=|[\]\}]|$)", line)
            m_car = re.search(r"carrierName=([^\s\]\}]+(?:\s+[^\s\]\}=]+)*?)(?=\s+[a-zA-Z_]+=|[\]\}]|$)", line)
            m_mcc = re.search(r"\bmcc=(\d+)", line)
            m_mnc = re.search(r"\bmnc=(\d+)", line)
            m_iso = re.search(r"countryIso=([a-zA-Z]{2})", line)

            carrier = ""
            if m_car and m_car.group(1).strip() not in ("", "null", "No network connection", "Emergency calls only"):
                carrier = m_car.group(1).strip()
            elif m_disp and m_disp.group(1).strip() not in ("", "null"):
                carrier = m_disp.group(1).strip()

            mcc_mnc = (m_mcc.group(1) + m_mnc.group(1)) if (m_mcc and m_mnc) else ""
            iso = m_iso.group(1).lower() if m_iso else ""

            # Check unredacted phone number fields in SubscriptionInfo
            detected_num = ""
            detected_src = ""
            redacted = False
            for field_name, src_label in (
                ("number", "SIM"),
                ("numberFromCarrier", "Carrier"),
                ("numberFromIms", "IMS"),
            ):
                m_num = re.search(rf"\b{field_name}=([^\s\]\}}]*)", line)
                if not m_num:
                    continue
                cand = m_num.group(1).strip()
                if "[****]" in cand or "***" in cand:
                    redacted = True
                elif self._is_valid_phone_number(cand) and not detected_num:
                    detected_num = cand
                    detected_src = src_label

            by_slot[slot_idx] = {
                "slot": slot_idx + 1,
                "sub_id": m_id.group(1) if m_id else "",
                "carrier": carrier or f"SIM {slot_idx + 1}",
                "country_iso": iso,
                "mcc_mnc": mcc_mnc,
                "number": detected_num,
                "source": detected_src,
                "redacted": redacted,
                "ussd": self._ussd_for_carrier(carrier, mcc_mnc),
            }

        # Fallback to gsm.sim.operator.* if dumpsys isub yielded no active slots
        if not by_slot and alpha_raw:
            alphas = [a.strip() for a in alpha_raw.split(",")]
            nums = [n.strip() for n in num_raw.split(",")] if num_raw else []
            isos = [i.strip().lower() for i in iso_raw.split(",")] if iso_raw else []
            states = [st.strip().upper() for st in state_raw.split(",")] if state_raw else []
            for idx, car in enumerate(alphas):
                st = states[idx] if idx < len(states) else ""
                if not car and st not in ("LOADED", "READY"):
                    continue
                mm = nums[idx] if idx < len(nums) else ""
                iso = isos[idx] if idx < len(isos) else ""
                by_slot[idx] = {
                    "slot": idx + 1,
                    "sub_id": "",
                    "carrier": car or f"SIM {idx + 1}",
                    "country_iso": iso,
                    "mcc_mnc": mm,
                    "number": "",
                    "source": "",
                    "redacted": False,
                    "ussd": self._ussd_for_carrier(car, mm),
                }

        sims = [by_slot[k] for k in sorted(by_slot.keys())]

        # Tier 2: Try `service call iphonesubinfo` in ONE batched shell command for any slot still missing a number
        missing = [s for s in sims if not s["number"]]
        if missing:
            cmds: List[str] = []
            for s in missing:
                sid = s["sub_id"]
                if sid:
                    for code in (15, 16, 17, 18):
                        cmds.append(
                            f"echo '__SUB_{s['slot']}_{code}__'; "
                            f"service call iphonesubinfo {code} i32 {sid} s16 com.android.shell s16 '' 2>/dev/null"
                        )
                elif len(sims) == 1:
                    for code in (15, 16, 17):
                        cmds.append(
                            f"echo '__SUB_{s['slot']}_{code}__'; "
                            f"service call iphonesubinfo {code} s16 com.android.shell s16 '' 2>/dev/null"
                        )
            if cmds:
                parcel_out = self.shell("; ".join(cmds), check=False, timeout=8)
                for s in missing:
                    for code in (15, 16, 17, 18):
                        marker = f"__SUB_{s['slot']}_{code}__"
                        if marker not in parcel_out:
                            continue
                        seg = parcel_out.split(marker, 1)[1].split("__SUB_", 1)[0]
                        cand = self._decode_binder_string16(seg)
                        if self._is_valid_phone_number(cand):
                            s["number"] = cand
                            s["source"] = "SIM"
                            break

        # Tier 3: Check `dumpsys ims` for VoLTE sip:/tel: MSISDN if still missing
        if any(not s["number"] for s in sims):
            ims_out = self.shell(
                "dumpsys ims | grep -iE 'sip:\\+?[0-9]{7,15}@|tel:\\+?[0-9]{7,15}' | head -n 10",
                check=False,
                timeout=6,
            )
            found_ims: List[str] = []
            for m in re.finditer(r"(?:sip|tel):(\+?\d{7,15})(?:[@;>\s]|$)", ims_out or "", flags=re.IGNORECASE):
                cand = m.group(1)
                if self._is_valid_phone_number(cand) and cand not in found_ims:
                    found_ims.append(cand)
            if found_ims:
                idx_ims = 0
                for s in sims:
                    if not s["number"] and idx_ims < len(found_ims):
                        s["number"] = found_ims[idx_ims]
                        s["source"] = "IMS"
                        idx_ims += 1

        # Tier 4: Root SQLite reader on `telephony.db` (`siminfo` table) if still missing
        if any(not s["number"] for s in sims) and self.has_root():
            sq_rows = self._read_root_sqlite(
                [
                    "/data/user_de/0/com.android.providers.telephony/databases/telephony.db",
                    "/data/data/com.android.providers.telephony/databases/telephony.db",
                ],
                "SELECT sim_id, number FROM siminfo WHERE sim_id >= 0 AND number IS NOT NULL AND number != ''",
            )
            by_slot_db = {int(slot_i) + 1: str(num).strip() for slot_i, num in sq_rows if self._is_valid_phone_number(str(num or ""))}
            for s in sims:
                if not s["number"] and s["slot"] in by_slot_db:
                    s["number"] = by_slot_db[s["slot"]]
                    s["source"] = "Root DB"

        return sims

    def list_recent_media(self, folder: str, limit: int = 20) -> List[dict]:
        """Lists newest non-directory media files in a remote Android folder via ls -lt."""
        clean_dir = folder.rstrip("/")
        ls_target = "/" if not clean_dir else f"{clean_dir}/"
        raw = self.shell(f"ls -lt {shlex.quote(ls_target)}", check=False, timeout=10)
        if not raw or "No such file or directory" in raw:
            return []
        items: List[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("total ") or line.startswith("d") or line.startswith("l"):
                continue
            # Typical toybox ls -lt: -rw-rw---- 1 u0_a123 media_rw 2451234 2026-11-16 10:15 IMG_001.jpg
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            size_str = parts[4]
            date_str = f"{parts[5]} {parts[6]}"
            name = parts[7]
            if name in (".", ".."):
                continue
            try:
                sz = int(size_str)
                if sz >= 1024 * 1024:
                    size_fmt = f"{sz / (1024 * 1024):.1f} MB"
                elif sz >= 1024:
                    size_fmt = f"{sz / 1024:.0f} KB"
                else:
                    size_fmt = f"{sz} B"
            except ValueError:
                size_fmt = size_str
            items.append(
                {
                    "name": name,
                    "path": f"{clean_dir}/{name}",
                    "size": size_fmt,
                    "modified": date_str,
                }
            )
            if len(items) >= limit:
                break
        return items

    def list_directory_entries(self, remote_dir: str, use_root: bool = False) -> List[dict]:
        """Lists folders and files inside remote_dir via ls -la (supports Root Mode for /data/data, /system)."""
        clean_dir = (remote_dir or "/sdcard").strip()
        if clean_dir != "/":
            clean_dir = clean_dir.rstrip("/")
        # Trailing slash forces Android toybox `ls -la /sdcard/` to follow directory symlinks!
        ls_target = "/" if clean_dir == "/" else f"{clean_dir}/"
        cmd = f"ls -la {shlex.quote(ls_target)}"
        raw = self.root_shell(cmd, check=False, timeout=12) if use_root else self.shell(cmd, check=False, timeout=12)
        if not raw:
            return []
        if "No such file or directory" in raw:
            raise ADBError(f"ফোল্ডার পাওয়া যায়নি: {clean_dir}")
        if "Permission denied" in raw and len(raw.splitlines()) <= 2:
            hint = "" if use_root else " (রুটেড ফোন হলে '👑 Root Mode' চালু করে দেখুন)"
            raise ADBError(f"পারমিশন নেই: {clean_dir}{hint}")

        dirs: List[dict] = []
        files: List[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("total ") or line.startswith("ls:"):
                continue
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            perms = parts[0]
            size_str = parts[4]
            date_str = f"{parts[5]} {parts[6]}"
            name = parts[7]
            if perms.startswith("l") and " -> " in name:
                name = name.split(" -> ", 1)[0].strip()
            name = name.strip("/")
            if name in (".", "..") or not name:
                continue

            is_dir = perms.startswith("d") or perms.startswith("l")
            full_path = f"/{name}" if clean_dir == "/" else f"{clean_dir}/{name}"

            if is_dir:
                size_fmt = "<DIR>"
            else:
                try:
                    sz = int(size_str)
                    if sz >= 1024 * 1024 * 1024:
                        size_fmt = f"{sz / (1024 * 1024 * 1024):.2f} GB"
                    elif sz >= 1024 * 1024:
                        size_fmt = f"{sz / (1024 * 1024):.1f} MB"
                    elif sz >= 1024:
                        size_fmt = f"{sz / 1024:.0f} KB"
                    else:
                        size_fmt = f"{sz} B"
                except ValueError:
                    size_fmt = size_str

            entry = {
                "name": name,
                "type": "dir" if is_dir else "file",
                "size": size_fmt,
                "modified": date_str,
                "path": full_path,
            }
            if is_dir:
                dirs.append(entry)
            else:
                files.append(entry)

        dirs.sort(key=lambda x: x["name"].lower())
        files.sort(key=lambda x: x["name"].lower())
        return dirs + files

    def rename_path(self, old_path: str, new_path: str, use_root: bool = False) -> str:
        """Renames or moves a file/folder on the device."""
        cmd = f"mv {shlex.quote(old_path)} {shlex.quote(new_path)}"
        return self.root_shell(cmd, check=True) if use_root else self.shell(cmd, check=True)


class ADBTerminalTool:
    def __init__(self):
        self.adb = ADB()
        self.running = True
        self.menu_groups = self._build_menu()

    def _build_menu(self):
        return [
            (
                "DEVICE MANAGEMENT",
                [
                    MenuItem(1, "List devices", self.list_devices),
                    MenuItem(2, "Connect WiFi device", self.connect_wifi),
                    MenuItem(3, "Disconnect device", self.disconnect_device),
                    MenuItem(4, "Wait for device", self.wait_device),
                    MenuItem(5, "Restart ADB", self.restart_adb),
                    MenuItem(6, "Check connection", self.check_connection),
                    MenuItem(7, "Execute shell command", self.exec_shell),
                    MenuItem(8, "Get device logs", self.get_logs),
                    MenuItem(9, "Reboot device", self.reboot_device),
                ],
            ),
            (
                "APP MANAGEMENT",
                [
                    MenuItem(10, "Install APK", self.install_apk),
                    MenuItem(11, "Uninstall app", self.uninstall_app),
                    MenuItem(12, "List packages", self.list_packages),
                    MenuItem(13, "Package info", self.package_info),
                    MenuItem(14, "Clear app data", self.clear_app_data),
                    MenuItem(15, "Force stop app", self.force_stop_app),
                    MenuItem(16, "Start app", self.start_app),
                    MenuItem(17, "Backup app (APK path)", self.backup_app_stub),
                    MenuItem(18, "Batch install", self.batch_install_stub),
                ],
            ),
            (
                "FILE OPERATIONS",
                [
                    MenuItem(19, "Take screenshot", self.take_screenshot),
                    MenuItem(20, "Multiple screenshots", self.multiple_screenshots_stub),
                    MenuItem(21, "Record screen", self.record_screen),
                    MenuItem(22, "Upload file", self.upload_file),
                    MenuItem(23, "Download file", self.download_file),
                    MenuItem(24, "List files", self.list_files),
                    MenuItem(25, "Create folder", self.create_folder),
                    MenuItem(26, "Delete file/folder", self.delete_path),
                    MenuItem(27, "Backup folder", self.backup_folder_stub),
                ],
            ),
            (
                "SYSTEM MONITORING",
                [
                    MenuItem(28, "Battery info", self.battery_info),
                    MenuItem(29, "Memory info", self.memory_info),
                    MenuItem(30, "CPU info", self.cpu_info),
                    MenuItem(31, "Storage info", self.storage_info),
                    MenuItem(32, "Running processes", self.running_processes),
                    MenuItem(33, "Network info", self.network_info),
                    MenuItem(34, "System properties", self.system_properties),
                    MenuItem(35, "Apps with size", self.apps_with_size_stub),
                    MenuItem(36, "Monitor performance", self.monitor_performance_stub),
                ],
            ),
            (
                "SCREEN SHARING",
                [
                    MenuItem(37, "Start screen sharing", self.start_scrcpy),
                    MenuItem(38, "Custom screen sharing", self.custom_scrcpy),
                    MenuItem(39, "Stop screen sharing", self.stop_scrcpy),
                    MenuItem(40, "Stop all sharing", self.stop_scrcpy_all),
                    MenuItem(41, "Show active sessions", self.show_scrcpy_sessions),
                    MenuItem(42, "Multi-device sharing", self.multi_device_stub),
                    MenuItem(43, "Check scrcpy", self.check_scrcpy),
                    MenuItem(44, "Record screen (scrcpy)", self.scrcpy_record_stub),
                    MenuItem(45, "Sync folder", self.sync_folder_stub),
                ],
            ),
            (
                "WIFI MANAGEMENT",
                [
                    MenuItem(46, "Manual WiFi transfer", self.tcpip_mode),
                    MenuItem(47, "Transfer all devices", self.transfer_all_devices_stub),
                    MenuItem(48, "Pair with WiFi QR code", self.pair_wifi_qr),
                    MenuItem(49, "WiFi status", self.wifi_status),
                    MenuItem(50, "Disconnect WiFi", self.disconnect_wifi),
                    MenuItem(51, "Toggle auto-transfer", self.auto_transfer_stub),
                    MenuItem(52, "Monitor logcat", self.monitor_logcat_stub),
                    MenuItem(53, "Service status", self.service_status),
                    MenuItem(54, "Settings", self.settings_stub),
                ],
            ),
        ]

    def render_menu(self):
        console.clear()
        title = Text("ADB TERMINAL TOOL", style="bold green")
        subtitle = Text("Python + ADB + Rich", style="green")
        console.print(Panel(Text.assemble(title, "\n", subtitle), border_style="green"))

        panels = []
        for group_title, items in self.menu_groups:
            body = "\n".join(f"[green]{item.number}.[/green] {item.title}" for item in items)
            panels.append(Panel(body, title=group_title, border_style="green", expand=True))

        console.print(Columns(panels, equal=True, expand=True))
        console.print("[bold green]Enter your choice (1-54, 99 to exit)[/bold green]", end="")

    def run(self):
        while self.running:
            self.render_menu()
            choice = Prompt.ask("")
            if choice == "99":
                self.running = False
                console.print("[bold green]Goodbye[/bold green]")
                break
            self.handle_choice(choice)

    def handle_choice(self, choice: str):
        try:
            num = int(choice)
        except ValueError:
            self.pause_error("ভুল ইনপুট")
            return

        for _, items in self.menu_groups:
            for item in items:
                if item.number == num:
                    try:
                        item.action()
                    except ADBError as e:
                        self.pause_error(str(e))
                    except Exception as e:
                        self.pause_error(f"Unexpected error: {e}")
                    return
        self.pause_error("Invalid choice")

    def show_output(self, title: str, content: str, markup: bool = False):
        console.clear()
        raw_text = content or "Done"
        body = raw_text if markup else escape(raw_text)
        console.print(Panel.fit(body, title=title, border_style="green"))
        Prompt.ask("\nPress Enter to continue", default="")

    def pause_error(self, message: str):
        console.print(f"[bold red]Error:[/bold red] {escape(message)}")
        Prompt.ask("Press Enter to continue", default="")

    def select_device_if_needed(self):
        devices = ADB.list_devices()
        ready = [d for d in devices if d["state"] == "device"]
        if not ready:
            self.adb.serial = None
            raise ADBError("কোনো connected device নেই।")
        if self.adb.serial and any(d["serial"] == self.adb.serial for d in ready):
            return
        if len(ready) == 1:
            self.adb.serial = ready[0]["serial"]
            return

        table = Table(title="Connected Devices", show_lines=True)
        table.add_column("No")
        table.add_column("Serial")
        table.add_column("State")
        table.add_column("Details")
        for i, d in enumerate(ready, start=1):
            table.add_row(str(i), d["serial"], d["state"], d["details"])
        console.clear()
        console.print(table)
        valid_choices = [str(i) for i in range(1, len(ready) + 1)]
        idx = int(Prompt.ask("Select device number", choices=valid_choices, default="1"))
        self.adb.serial = ready[idx - 1]["serial"]

    # -------- device management --------
    def list_devices(self):
        devices = ADB.list_devices()
        table = Table(title="ADB Devices", show_lines=True)
        table.add_column("Serial")
        table.add_column("State")
        table.add_column("Details")
        for d in devices:
            table.add_row(d["serial"], d["state"], d["details"])
        console.clear()
        console.print(table)
        Prompt.ask("\nPress Enter to continue", default="")

    def connect_wifi(self):
        target = Prompt.ask("IP:PORT")
        self.show_output("Connect", self.adb.connect(target))

    def disconnect_device(self):
        target = Prompt.ask("Target (blank for all)", default="")
        self.adb.serial = None
        self.show_output("Disconnect", self.adb.disconnect(target or None))

    def wait_device(self):
        self.show_output("Wait for device", self.adb.wait())

    def restart_adb(self):
        try:
            out1 = subprocess.run(
                ["adb", "kill-server"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            out2 = subprocess.run(
                ["adb", "start-server"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as e:
            raise ADBError("adb পাওয়া যায়নি। Android Platform Tools install করে PATH-এ যোগ করুন।") from e
        self.adb.serial = None
        self.show_output("Restart ADB", (out1.stdout + out1.stderr + "\n" + out2.stdout + out2.stderr).strip())

    def check_connection(self):
        self.select_device_if_needed()
        info = self.adb.info()
        text = "\n".join(f"{k}: {v}" for k, v in info.items())
        self.show_output("Connection OK", text)

    def exec_shell(self):
        self.select_device_if_needed()
        cmd = Prompt.ask("Shell command")
        self.show_output("Shell Output", self.adb.shell(cmd, check=False))

    def get_logs(self):
        self.select_device_if_needed()
        lines = max(1, IntPrompt.ask("How many lines", default=100))
        mode = Prompt.ask(
            "Filter [1=All, 2=Errors Only, 3=Search Keyword]",
            choices=["1", "2", "3"],
            default="1",
        )

        if mode == "2":
            # শুধু Error লগ আনবে
            raw = self.adb.run(["logcat", "-d", "-t", str(lines), "*:E"], check=False)
        elif mode == "3":
            keyword = Prompt.ask("Keyword (যেমন: wifi, battery, crash)")
            raw = self.adb.run(["logcat", "-d", "-t", str(lines)], check=False)
            raw = "\n".join(
                l for l in raw.splitlines() if keyword.lower() in l.lower()
            )
        else:
            raw = self.adb.logcat(lines)

        # Error ( E ) ও Warning ( W ) লাইনগুলো রঙিন করে দেখানো
        colored_lines = []
        for line in raw.splitlines():
            safe_line = escape(line)
            if " E " in line:
                colored_lines.append(f"[bold red]{safe_line}[/bold red]")
            elif " W " in line:
                colored_lines.append(f"[yellow]{safe_line}[/yellow]")
            else:
                colored_lines.append(safe_line)

        self.show_output("Device Logs", "\n".join(colored_lines) or "কোনো লগ পাওয়া যায়নি", markup=True)

    def reboot_device(self):
        self.select_device_if_needed()
        if Confirm.ask("Reboot device?"):
            self.show_output("Reboot", self.adb.reboot())

    # -------- app management --------
    def install_apk(self):
        self.select_device_if_needed()
        path = Prompt.ask("APK path")
        self.show_output("Install APK", self.adb.install_apk(path))

    def uninstall_app(self):
        self.select_device_if_needed()
        package = Prompt.ask("Package name")
        self.show_output("Uninstall App", self.adb.uninstall_app(package))

    def list_packages(self):
        self.select_device_if_needed()
        self.show_output("Packages", self.adb.list_packages())

    def package_info(self):
        self.select_device_if_needed()
        package = Prompt.ask("Package name")
        self.show_output("Package Info", self.adb.package_info(package))

    def clear_app_data(self):
        self.select_device_if_needed()
        package = Prompt.ask("Package name")
        self.show_output("Clear Data", self.adb.clear_data(package))

    def force_stop_app(self):
        self.select_device_if_needed()
        package = Prompt.ask("Package name")
        self.show_output("Force Stop", self.adb.force_stop(package))

    def start_app(self):
        self.select_device_if_needed()
        package = Prompt.ask("Package name")
        self.show_output("Start App", self.adb.start_app(package))

    def backup_app_stub(self):
        self.show_output("Not implemented", "এখানে package path থেকে APK extract feature যোগ করা যাবে।")

    def batch_install_stub(self):
        self.show_output("Not implemented", "একটা folder scan করে সব APK install করা যাবে।")

    # -------- file operations --------
    def take_screenshot(self):
        self.select_device_if_needed()
        path = Prompt.ask("Save as", default="screenshot.png")
        self.show_output("Screenshot", self.adb.screenshot(path))

    def multiple_screenshots_stub(self):
        self.show_output("Not implemented", "interval screenshot scheduler যোগ করা যাবে।")

    def record_screen(self):
        self.select_device_if_needed()
        path = Prompt.ask("Save as", default="record.mp4")
        seconds = IntPrompt.ask("Seconds (1-180)", default=10)
        self.show_output("Screen Record", self.adb.screenrecord(path, seconds))

    @staticmethod
    def _cli_progress(label: str, done_b: int, total_b: int, pct: float, speed_bps: float):
        done_s = ADB.format_bytes(done_b)
        tot_s = ADB.format_bytes(total_b) if total_b > 0 else "?"
        spd_s = ADB.format_bytes(speed_bps) + "/s"
        sys.stdout.write(f"\r{label}: {pct:5.1f}%  [{done_s} / {tot_s}]  ⚡ {spd_s}    ")
        sys.stdout.flush()

    def upload_file(self):
        self.select_device_if_needed()
        local_path = Prompt.ask("Local path")
        remote_path = Prompt.ask("Remote path", default="/sdcard/Download")
        out = self.adb.push(
            local_path,
            remote_path,
            progress_callback=lambda d, t, p, s: self._cli_progress("⬆️ Uploading", d, t, p, s),
        )
        print()
        self.show_output("Upload File", out)

    def download_file(self):
        self.select_device_if_needed()
        remote_path = Prompt.ask("Remote path")
        local_path = Prompt.ask("Local path", default=".")
        out = self.adb.pull(
            remote_path,
            local_path,
            progress_callback=lambda d, t, p, s: self._cli_progress("⬇️ Downloading", d, t, p, s),
        )
        print()
        self.show_output("Download File", out)

    def list_files(self):
        self.select_device_if_needed()
        remote_path = Prompt.ask("Remote path", default="/sdcard")
        self.show_output("List Files", self.adb.ls(remote_path))

    def create_folder(self):
        self.select_device_if_needed()
        remote_path = Prompt.ask("Folder path")
        self.show_output("Create Folder", self.adb.mkdir(remote_path))

    def delete_path(self):
        self.select_device_if_needed()
        remote_path = Prompt.ask("Path to delete")
        if Confirm.ask(f"Delete {remote_path} ?"):
            self.show_output("Delete", self.adb.rm(remote_path))

    def backup_folder_stub(self):
        self.show_output("Not implemented", "adb pull দিয়ে পুরো folder backup feature যোগ করা যাবে।")

    # -------- system monitoring --------
    def battery_info(self):
        self.select_device_if_needed()
        self.show_output("Battery Info", self.adb.battery())

    def memory_info(self):
        self.select_device_if_needed()
        self.show_output("Memory Info", self.adb.memory())

    def cpu_info(self):
        self.select_device_if_needed()
        self.show_output("CPU Info", self.adb.cpu())

    def storage_info(self):
        self.select_device_if_needed()
        self.show_output("Storage Info", self.adb.storage())

    def running_processes(self):
        self.select_device_if_needed()
        self.show_output("Running Processes", self.adb.processes())

    def network_info(self):
        self.select_device_if_needed()
        self.show_output("Network Info", self.adb.netstat())

    def system_properties(self):
        self.select_device_if_needed()
        info = self.adb.info()
        self.show_output("System Properties", "\n".join(f"{k}: {v}" for k, v in info.items()))

    def apps_with_size_stub(self):
        self.show_output("Not implemented", "du + package path দিয়ে app size বের করা যাবে।")

    def monitor_performance_stub(self):
        self.show_output("Not implemented", "live refresh dashboard বানানো যাবে।")

    # -------- screen sharing --------

    def _run_scrcpy_bg(self, args: List[str]):
        """scrcpy ব্যাকগ্রাউন্ডে রান করার সহায়ক মেথড"""
        # যদি একই ফোল্ডারে scrcpy.exe রাখা থাকে তবে সেটি আগে নেবে, নাহলে PATH থেকে নেবে
        exe = "scrcpy"
        for candidate in ["scrcpy.exe", os.path.join("scrcpy", "scrcpy.exe")]:
            if os.path.exists(candidate):
                exe = candidate
                break

        try:
            subprocess.Popen(
                [exe] + args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as e:
            raise ADBError(
                "scrcpy পাওয়া যায়নি!\n"
                "টার্মিনালে নিচের কমান্ডটি চালিয়ে ইনস্টল করুন:\n"
                "  winget install Genymobile.scrcpy"
            ) from e

    def start_scrcpy(self):
        self.select_device_if_needed()
        self._run_scrcpy_bg(["-s", self.adb.serial])
        self.show_output("scrcpy", f"ডিভাইস ({self.adb.serial})-এর স্ক্রিন শেয়ার শুরু হয়েছে!")

    def custom_scrcpy(self):
        self.select_device_if_needed()
        console.clear()
        mode = Prompt.ask(
            "কোন মোডে স্ক্রিন শেয়ার করতে চান?\n"
            "  [1] WiFi Smooth Mode (ল্যাগ ছাড়া ফাস্ট চলবে - 720p, 4Mbps)\n"
            "  [2] Turn Phone Screen Off (ফোনের ডিসপ্লে বন্ধ থাকবে, পিসিতে চলবে)\n"
            "  [3] Stay Awake + Always on Top (স্ক্রিন সবসময় উপরে ও চালু থাকবে)\n"
            "  [4] Read-Only Mode (শুধু দেখা যাবে, মাউস/কিবোর্ড কাজ করবে না)\n"
            "  [5] নিজের ইচ্ছেমতো কমান্ড লিখব\n"
            "পছন্দ করুন",
            choices=["1", "2", "3", "4", "5"],
            default="1",
        )

        preset_args = {
            "1": ["-m", "1024", "-b", "4M", "--max-fps", "60"],
            "2": ["-S", "-w"],
            "3": ["-w", "--always-on-top"],
            "4": ["-n"],
        }

        if mode == "2":
            self.adb.shell("svc power stayon true", check=False)
            self.adb.keyevent("KEYCODE_WAKEUP")
            if os.name == "nt":
                subprocess.run(["taskkill", "/IM", "scrcpy.exe", "/F"], capture_output=True)
            else:
                subprocess.run(["pkill", "-f", "scrcpy"], capture_output=True)
            time.sleep(0.4)
            extra_args = ["-S", "-w"]
            self._run_scrcpy_bg(["-s", self.adb.serial] + extra_args)
            self.show_output(
                "Custom scrcpy (Screen-Off Mode)",
                f"scrcpy চালু হয়েছে: {' '.join(extra_args)}\n\n"
                "⚠️ জরুরি টিপস:\n"
                "• ফোনের ফিজিক্যাল Power বাটন চাপবেন না (চাপলে ফোন লক হয়ে যায়)।\n"
                "• যেকোনো সময় ফোনের স্ক্রিন আবার বন্ধ করতে scrcpy উইন্ডোতে চাপুন: Alt + O\n"
                "• ফোনের স্ক্রিন আবার চালু করতে scrcpy উইন্ডোতে চাপুন: Alt + Shift + O",
            )
            return

        if mode in preset_args:
            extra_args = preset_args[mode]
        else:
            extra = Prompt.ask("Extra scrcpy args (যেমন: -m 1024 --always-on-top)", default="")
            extra_args = shlex.split(extra)

        self._run_scrcpy_bg(["-s", self.adb.serial] + extra_args)
        self.show_output("Custom scrcpy", f"scrcpy চালু হয়েছে: {' '.join(extra_args)}")

    def stop_scrcpy(self):
        if os.name == "nt":
            subprocess.run(["taskkill", "/IM", "scrcpy.exe", "/F"], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "scrcpy"], capture_output=True)
        self.show_output("Stop scrcpy", "সব scrcpy সেশন বন্ধ করা হয়েছে।")

    def stop_scrcpy_all(self):
        self.stop_scrcpy()

    def show_scrcpy_sessions(self):
        if os.name == "nt":
            proc = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq scrcpy.exe"],
                capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            out = proc.stdout.strip()
            if "scrcpy.exe" not in out.lower():
                out = "কোনো সক্রিয় scrcpy সেশন চলছে না।"
            self.show_output("Active scrcpy Sessions", out)
        else:
            proc = subprocess.run(["pgrep", "-af", "scrcpy"], capture_output=True, text=True)
            self.show_output("Sessions", proc.stdout.strip() or "No active scrcpy session")

    def multi_device_stub(self):
        devices = [d for d in ADB.list_devices() if d["state"] == "device"]
        if not devices:
            raise ADBError("কোনো কানেক্টেড ডিভাইস পাওয়া যায়নি।")

        for d in devices:
            self._run_scrcpy_bg(["-s", d["serial"], "--window-title", f"Phone: {d['serial']}"])
        self.show_output(
            "Multi-Device Sharing",
            f"মোট {len(devices)}টি ডিভাইসের জন্য আলাদা আলাদা স্ক্রিন শেয়ার চালু হয়েছে!",
        )

    def check_scrcpy(self):
        exe = "scrcpy"
        for candidate in ["scrcpy.exe", os.path.join("scrcpy", "scrcpy.exe")]:
            if os.path.exists(candidate):
                exe = candidate
                break
        try:
            proc = subprocess.run(
                [exe, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.show_output("scrcpy Version", (proc.stdout or proc.stderr).strip())
        except FileNotFoundError:
            self.show_output(
                "scrcpy Check",
                "❌ scrcpy ইনস্টল করা নেই।\n\n"
                "ইনস্টল করতে PowerShell বা CMD-তে লিখুন:\n"
                "  winget install Genymobile.scrcpy",
            )

    def scrcpy_record_stub(self):
        self.select_device_if_needed()
        filename = Prompt.ask("ভিডিও ফাইলের নাম দিন", default=f"scrcpy_rec_{int(time.time())}.mp4")
        self._run_scrcpy_bg(["-s", self.adb.serial, "--record", filename])
        self.show_output(
            "scrcpy Recording Started",
            f"স্ক্রিন শেয়ার ও রেকর্ডিং শুরু হয়েছে!\n"
            f"রেকর্ডিং শেষ করতে scrcpy উইন্ডোটি কেটে দিন বা Option 39 চাপুন।\n"
            f"ফাইল সেভ হবে: {filename}",
        )

    def sync_folder_stub(self):
        self.select_device_if_needed()
        mode = Prompt.ask(
            "কীভাবে ফোল্ডার Sync করবেন? [1=PC থেকে ফোনে, 2=ফোন থেকে PC-তে]",
            choices=["1", "2"],
            default="1",
        )
        local_path = Prompt.ask("PC ফোল্ডারের পাথ দিন")
        remote_path = Prompt.ask("ফোনের ফোল্ডারের পাথ দিন", default="/sdcard/Download")

        if mode == "1":
            out = self.adb.push(local_path, remote_path)
        else:
            out = self.adb.pull(remote_path, local_path)
        self.show_output("Sync Folder", out)    
    # -------- wifi management --------
    def tcpip_mode(self):
        self.select_device_if_needed()
        port = IntPrompt.ask("Port", default=5555)
        self.show_output("TCPIP Mode", self.adb.tcpip(port))

    def transfer_all_devices_stub(self):
        self.show_output("Not implemented", "সব connected device-কে TCPIP mode-এ পাঠানো যাবে।")

    def pair_wifi_qr(self):
        """ধাপ ১: ফোনে WiFi না থাকলে WiFi QR দেখাবে, ধাপ ২: ADB QR দিয়ে পেয়ার করবে"""
        console.clear()

        # ১. আগে জিজ্ঞেস করবে ফোনে ওয়াইফাই কানেক্ট আছে কি না
        has_wifi = Confirm.ask("ফোনে কি আগে থেকেই একই WiFi কানেক্ট করা আছে?", default=True)

        # যদি ফোনে ওয়াইফাই কানেক্ট না থাকে (사용কারী 'n' চাপলে)
        if not has_wifi:
            ssid, wifi_pass = "", ""

            # কম্পিউটার থেকে অটোমেটিক WiFi-এর নাম ও পাসওয়ার্ড বের করা
            try:
                out = subprocess.run(
                    ["netsh", "wlan", "show", "interfaces"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace"
                ).stdout
                for line in out.splitlines():
                    if "SSID" in line and "BSSID" not in line:
                        ssid = line.split(":", 1)[1].strip()
                        break

                if ssid:
                    prof = subprocess.run(
                        ["netsh", "wlan", "show", "profile", f"name={ssid}", "key=clear"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace"
                    ).stdout
                    for line in prof.splitlines():
                        if "Key Content" in line:
                            wifi_pass = line.split(":", 1)[1].strip()
                            break
            except Exception:
                pass

            # যদি কম্পিউটার থেকে না পায়, তখন টাইপ করতে বলবে
            if not ssid or not wifi_pass:
                ssid = Prompt.ask("আপনার WiFi-এর নাম (SSID) লিখুন")
                wifi_pass = Prompt.ask("আপনার WiFi-এর পাসওয়ার্ড লিখুন")

            # ১ম QR কোড: ফোনে ওয়াইফাই কানেক্ট করার জন্য
            wifi_qr_data = f"WIFI:T:WPA;S:{ssid};P:{wifi_pass};;"
            console.clear()
            console.print(
                Panel(
                    f"ধাপ ১ — আগে ফোনে WiFi কানেক্ট করুন:\n\n"
                    f"  ১. ফোনের Camera অথবা WiFi QR Scanner খুলুন\n"
                    f"  ২. নিচের QR কোডটি স্ক্যান করলে '{ssid}' ওয়াইফাই কানেক্ট হয়ে যাবে।",
                    title="Step 1: WiFi Connect QR",
                    border_style="cyan",
                )
            )
            w_qr = qrcode.QRCode(border=2)
            w_qr.add_data(wifi_qr_data)
            w_qr.make(fit=True)
            w_qr.print_ascii(invert=True)

            Prompt.ask("\n[bold green]ফোনে WiFi কানেক্ট হয়ে গেলে Enter চাপুন[/bold green]")

        # ২. এবার ২য় QR কোড: ADB Wireless Debugging-এর জন্য (আপনার আগের কোড)
        service_name = f"adbtool-{secrets.token_hex(4)}"
        password = secrets.token_urlsafe(12)
        qr_data = f"WIFI:T:ADB;S:{service_name};P:{password};;"

        console.clear()
        console.print(
            Panel(
                "ধাপ ২ — ফোনে যান:\n"
                "  Settings → Developer options → Wireless debugging\n"
                "  → Pair device with QR code\n\n"
                "তারপর নিচের QR কোডটি স্ক্যান করুন।",
                title="Step 2: Wireless Debugging — QR Pairing",
                border_style="green",
            )
        )

        qr = qrcode.QRCode(border=2)
        qr.add_data(qr_data)
        qr.make(fit=True)
        qr.print_ascii(invert=True)

        console.print(
            "\n[yellow]⏳ Pairing device খোঁজা হচ্ছে... (সর্বোচ্চ 120 সেকেন্ড)[/yellow]"
        )

        found_pair = threading.Event()
        pair_info = {}
        pairing_type = "_adb-tls-pairing._tcp.local."

        def on_pairing_service(zeroconf, service_type, name, state_change):
            if state_change not in (
                ServiceStateChange.Added,
                ServiceStateChange.Updated,
            ):
                return
            if not name.lower().startswith(service_name.lower() + "."):
                return
            info = zeroconf.get_service_info(service_type, name, timeout=5000)
            if not info:
                return
            addrs = [a for a in info.parsed_addresses() if ":" not in a]
            if addrs:
                pair_info["ip"] = addrs[0]
                pair_info["port"] = info.port
                found_pair.set()

        zc = Zeroconf()
        browser_pair = ServiceBrowser(
            zc, pairing_type, handlers=[on_pairing_service]
        )

        try:
            if not found_pair.wait(timeout=120):
                raise ADBError(
                    "Pairing service পাওয়া যায়নি।\n"
                    "  • ফোন ও কম্পিউটার কি একই WiFi-তে?\n"
                    "  • ফোনে QR স্ক্যান করেছেন?"
                )

            target = f"{pair_info['ip']}:{pair_info['port']}"
            console.print(f"[green]✔ Pairing service পাওয়া গেছে: {target}[/green]")

            console.print("[cyan]🔗 Pairing করা হচ্ছে...[/cyan]")
            pair_out = ADB(timeout=30).run(["pair", target, password])
            console.print(f"[green]{escape(pair_out)}[/green]")

            if "successfully" not in pair_out.lower():
                raise ADBError(f"Pairing ব্যর্থ:\n{pair_out}")

            console.print(
                "\n[cyan]⏳ Connection port খোঁজা হচ্ছে... (সর্বোচ্চ 30 সেকেন্ড)[/cyan]"
            )

            found_conn = threading.Event()
            conn_info = {}
            connect_type = "_adb-tls-connect._tcp.local."

            def on_connect_service(zeroconf, service_type, name, state_change):
                if state_change not in (
                    ServiceStateChange.Added,
                    ServiceStateChange.Updated,
                ):
                    return
                info = zeroconf.get_service_info(service_type, name, timeout=5000)
                if not info:
                    return
                addrs = [a for a in info.parsed_addresses() if ":" not in a]
                if addrs and addrs[0] == pair_info["ip"]:
                    conn_info["ip"] = addrs[0]
                    conn_info["port"] = info.port
                    found_conn.set()

            browser_conn = ServiceBrowser(
                zc, connect_type, handlers=[on_connect_service]
            )

            try:
                if found_conn.wait(timeout=30):
                    conn_target = f"{conn_info['ip']}:{conn_info['port']}"
                    console.print(
                        f"[green]✔ Connection service পাওয়া গেছে: {conn_target}[/green]"
                    )
                    conn_out = self.adb.connect(conn_target)
                    self.show_output(
                        "✔ Pairing + Connection সফল!",
                        f"Pair: {pair_out}\n"
                        f"Connect: {conn_out}\n\n"
                        f"ডিভাইস: {conn_target}\n\n"
                        "এখন '1 — List devices' দিয়ে যাচাই করুন।",
                    )
                else:
                    self.show_output(
                        "✔ Pairing সফল, কিন্তু auto-connect হয়নি",
                        f"Pair: {pair_out}\n\n"
                        "⚠️  Connection port পাওয়া যায়নি।\n"
                        "ম্যানুয়ালি '2 — Connect WiFi device' দিয়ে কানেক্ট করুন।",
                    )
            finally:
                browser_conn.cancel()

        finally:
            browser_pair.cancel()
            zc.close()

    def quick_wifi_setup(self):
        self.select_device_if_needed()
        port = int(Prompt.ask("Port", default="5555"))
        out1 = self.adb.tcpip(port)
        ip = self.adb.shell("ip -f inet addr show wlan0 | grep inet | awk '{print $2}' | cut -d/ -f1", check=False)
        out2 = self.adb.connect(f"{ip}:{port}") if ip else "WiFi IP পাওয়া যায়নি"
        self.show_output("Quick WiFi Setup", f"{out1}\n\nIP: {ip}\n{out2}")

    # এটা ADBTerminalTool ক্লাসের ভিতরে থাকবে
    def wifi_status(self):
        self.select_device_if_needed()

        # ১. Android-এর বিল্ট-ইন কমান্ড দিয়ে সরাসরি ও দ্রুত WiFi স্ট্যাটাস আনা
        status_out = self.adb.shell("cmd wifi status", check=False)

        # ২. dumpsys থেকে শুধু প্রথম ৩০ লাইন নেওয়া (পুরো ৩ মেগাবাইট না টেনে)
        basic_dump = self.adb.shell("dumpsys wifi | head -n 30", check=False)

        # ৩. শেষ ৫টি Toggle ইতিহাস ফোনের ভেতরেই ফিল্টার করে আনা
        toggles = self.adb.shell(
            "dumpsys wifi | grep 'CMD_WIFI_TOGGLED' | tail -n 5",
            check=False,
        )

        result = ["═══ Connected WiFi Status ═══\n"]
        if status_out:
            for line in status_out.splitlines():
                line_s = line.strip()
                # শুধু দরকারি লাইনগুলো সুন্দর করে সাজানো
                if any(k in line_s for k in [
                    "Wifi is", "SSID:", "DhcpResults:", "successful", "Additional"
                ]):
                    result.append(line_s)
        else:
            result.append("তথ্য পাওয়া যায়নি")

        result.append("\n═══ System Settings ═══\n")
        important_keys = (
            "WifiState",
            "AirplaneModeOn",
            "ScanAlwaysAvailable",
            "Current wifi mode",
        )
        for line in basic_dump.splitlines():
            stripped = line.strip()
            if stripped.startswith(important_keys):
                result.append(stripped)

        result.append("\n═══ শেষ ৫টি WiFi Toggle ইতিহাস ═══\n")
        result.append(toggles.strip() or "কোনো toggle ইতিহাস নেই")

        self.show_output("WiFi Status", "\n".join(result))

    def disconnect_wifi(self):
        target = Prompt.ask("IP:PORT", default="")
        self.show_output("Disconnect WiFi", self.adb.disconnect(target or None))

    def auto_transfer_stub(self):
        self.show_output("Not implemented", "auto sync / watch mode এখানে যোগ করা যাবে।")

    def monitor_logcat_stub(self):
        self.show_output("Tip", "live monitor-এর জন্য subprocess + streaming UI দরকার।")

    def service_status(self):
        self.select_device_if_needed()
        self.show_output("Service Status", self.adb.service_status())

    def settings_stub(self):
        current = self.adb.serial or "auto"
        self.show_output("Settings", f"Current serial: {current}\nTimeout: {self.adb.timeout} sec")


if __name__ == "__main__":
    if "--ui" in sys.argv or "--gui" in sys.argv or console is None:
        from ui import main as run_ui
        run_ui()
    else:
        try:
            ADBTerminalTool().run()
        except KeyboardInterrupt:
            console.print("\n[bold yellow]Interrupted[/bold yellow]")
            sys.exit(0)
